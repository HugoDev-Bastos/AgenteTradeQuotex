"""
loops.py - Loops de execucao e helpers de conexao/timing.
"""

import json
import math
import time
import asyncio
from datetime import datetime
from pathlib import Path

from agents import (
    AgentProtetor, AgentAnalisador, AgentVerificador,
    AgentQuotex, AgentTelegram,
)
from skills import (
    skill_calculate_mg, skill_register_operation,
    skill_log_alert,
)
from config import carregar_config, salvar_config
from utils import (
    log_s, _inp, _shutdown_gracioso,
    _instalar_shutdown_gracioso, _restaurar_shutdown,
    _sleep_cancelavel, _verificar_internet,
    _classificar_ativo, _esta_no_horario,
)

# --- Constantes operacionais ---
_TIMEOUT_CANDLES_SEG   = 30   # timeout para buscar historico de candles
_FATOR_CANDLES_HIST    = 100  # quantos candles buscar (duracao x fator)
_ESPERA_RETRY_REDE_SEG = 30   # espera antes de retry em erro de rede
_TOP_ATIVOS_EXIBIR     = 5    # numero de ativos alternativos a exibir


async def _conectar_com_retry(quotex: "AgentQuotex", cfg: dict) -> bool:
    """Conecta na Quotex com timeout e multiplas tentativas.

    Usa timeout_conexao_seg e tentativas_reconexao do config.
    Retorna True se conectou, False se esgotou as tentativas.
    """
    timeout_con = int(cfg.get("timeout_conexao_seg", 30))
    tentativas   = int(cfg.get("tentativas_reconexao", 3))

    for i in range(1, tentativas + 1):
        try:
            print(f"  Conectando... (tentativa {i}/{tentativas}, timeout {timeout_con}s)")
            await asyncio.wait_for(quotex.conectar(), timeout=timeout_con)
            log_s("INFO", f"Quotex conectado (tentativa {i}/{tentativas})")
            return True
        except asyncio.TimeoutError:
            print(f"  [TIMEOUT] Conexao nao estabelecida em {timeout_con}s")
            log_s("WARN", f"Timeout de conexao em {timeout_con}s (tentativa {i}/{tentativas})")
        except Exception as e:
            print(f"  [ERRO] {type(e).__name__}: {e}")
            log_s("ERROR", f"Erro ao conectar: {type(e).__name__}: {e} (tentativa {i}/{tentativas})")

        if i < tentativas:
            print(f"  Aguardando {cfg.get('intervalo_verificacao_seg', 3)}s...")
            await asyncio.sleep(int(cfg.get("intervalo_verificacao_seg", 3)))

    print(f"  [FALHA] Nao foi possivel conectar apos {tentativas} tentativas.")
    log_s("ERROR", f"Falha ao conectar apos {tentativas} tentativas")
    return False


async def _reconectar(quotex: "AgentQuotex", cfg: dict) -> bool:
    """Tenta reconectar apos queda de conexao mid-session."""
    print(f"\n  [RECONEXAO] Conexao perdida. Reconectando...")
    log_s("WARN", "Conexao perdida mid-session. Tentando reconectar...")
    quotex.conectado = False
    try:
        await quotex.client.close()
    except Exception:
        pass
    ok = await _conectar_com_retry(quotex, cfg)
    if ok:
        log_s("INFO", "Reconexao bem-sucedida")
    else:
        log_s("ERROR", "Reconexao falhou")
    return ok


def _listar_ativos_quotex(quotex: "AgentQuotex", cfg: dict) -> list[dict]:
    """Retorna ativos abertos na Quotex com payout, filtrados pelo config.

    Usa get_payment() (sync) que ja foi populado na conexao.
    Filtra por: aberto, tipo_ativo (OTC/NAO_OTC/AMBOS), payout_minimo_pct.
    Ordena por payout decrescente.
    """
    tipo_ativo = cfg.get("tipo_ativo", "AMBOS")
    tipo_mercado = cfg.get("tipo_mercado", "AMBOS")
    payout_min = float(cfg.get("payout_minimo_pct", 0))

    try:
        payments = quotex.client.get_payment()
    except Exception:
        return []

    ativos = []
    for nome_display, data in payments.items():
        if not data.get("open", False):
            continue
        payout_pct = float(data.get("payment", 0))
        if payout_pct < payout_min:
            continue

        is_otc = "(OTC)" in nome_display

        if tipo_ativo == "OTC" and not is_otc:
            continue
        if tipo_ativo == "NAO_OTC" and is_otc:
            continue

        mercado = _classificar_ativo(nome_display)
        if tipo_mercado != "AMBOS" and mercado != tipo_mercado:
            continue

        # "EUR/USD (OTC)" -> "EURUSD_otc" | "EUR/USD" -> "EURUSD"
        nome_interno = nome_display.replace(" (OTC)", "").replace("/", "").strip()
        if is_otc:
            nome_interno = nome_interno + "_otc"

        ativos.append({
            "display": nome_display,
            "interno": nome_interno,
            "payout_pct": payout_pct,
            "is_otc": is_otc,
            "mercado": mercado,
        })

    ativos.sort(key=lambda x: x["payout_pct"], reverse=True)
    return ativos


async def _executar_ciclo_mg(
    quotex,
    asset_real: str,
    direction: str,
    valor: float,
    niveis: int,
    payout: float,
    duracao: int,
    cfg: dict,
    fonte: str,
    aguardar_candle: bool = False,
    horario_sinal: str | None = None,
    extra_fields: dict | None = None,
    saldo_atual: float | None = None,
) -> dict:
    """Executa um ciclo completo de MG (entrada + niveis de martingale).

    Retorna {"cenario": int, "lucro": float}.
    Cenario: 0=DOJI, 1=WIN direto, 2=WIN no MG, 3=LOSS total.

    aguardar_candle=True: aguarda abertura do proximo candle antes do nivel 0 (loop quotex).
    horario_sinal: se None, aguarda candle antes do nivel 0 (telegram/lista sem horario fixo).
    extra_fields: campos extras para skill_register_operation (ex: estrategia no loop autonomo).
    """
    fator_correcao = (1.0 / payout) if cfg.get("fator_correcao_mg") and payout > 0 else 1.0
    mg = skill_calculate_mg(entrada=valor, payout=payout, nivel=niveis, fator_correcao=fator_correcao)

    # Aviso: saldo insuficiente para cobrir todas as perdas do ciclo MG
    if saldo_atual is not None and mg["perda_total_se_perder_tudo"] > saldo_atual:
        print(f"  [!] AVISO: Saldo R${saldo_atual:.2f} insuficiente para ciclo MG completo")
        print(f"      Perda maxima possivel: R${mg['perda_total_se_perder_tudo']:.2f}")
        print(f"      Considere reduzir niveis de MG ou valor de entrada.")
        log_s("WARN", f"Saldo insuficiente para MG: saldo R${saldo_atual:.2f} < perda_max R${mg['perda_total_se_perder_tudo']:.2f}")

    seq_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    cenario_final = 1
    lucro_seq = 0.0
    teve_loss = False  # rastreia se houve LOSS antes de qualquer break inesperado

    for i, nivel_info in enumerate(mg["niveis"]):
        nivel_nome = "entrada" if i == 0 else f"mg{i}"
        valor_op = nivel_info["valor"]

        if i == 0:
            if aguardar_candle or horario_sinal is None:
                await aguardar_proximo_intervalo(duracao)
        else:
            print(f"\n    [MG] Entrada imediata: {datetime.now().strftime('%H:%M:%S')}")

        print(f"\n    {nivel_info['nivel'].upper()}: R${valor_op} -> {asset_real} {direction.upper()} ({duracao}s)")

        timeout_res = int(cfg.get("timeout_resultado_seg", 900))
        try:
            result = await asyncio.wait_for(
                quotex.operar(asset=asset_real, direction=direction,
                              amount=valor_op, duration=duracao),
                timeout=timeout_res,
            )
        except asyncio.TimeoutError:
            print(f"    [TIMEOUT] Resultado nao recebido em {timeout_res}s")
            if await _reconectar(quotex, cfg):
                print(f"    [RECONEXAO OK] Pulando nivel atual")
            cenario_final = 3  # resultado desconhecido — assume C3 (conservador)
            break
        except Exception as e:
            print(f"    [ERRO CONEXAO] {e}")
            if await _reconectar(quotex, cfg):
                print(f"    [RECONEXAO OK] Pulando nivel atual")
            cenario_final = 3  # resultado desconhecido — assume C3 (conservador)
            break

        if not result["success"]:
            det = result.get("detalhes", "")
            print(f"    [ERRO] {result.get('erro', 'Falha')}" + (f": {det}" if det else ""))
            break

        profit = result["profit"]
        lucro_seq += profit

        if result["result"] == "WIN":
            print(f"    >>> WIN! +R${profit} <<<")
            cenario_final = 1 if i == 0 else 2
        elif result["result"] == "DOJI":
            print(f"    >>> DOJI (empate) - entrada devolvida <<<")
            cenario_final = 0
        else:
            print(f"    >>> LOSS R${profit} <<<")
            teve_loss = True
            if i == len(mg["niveis"]) - 1:
                cenario_final = 3

        op = {
            "asset": asset_real, "direction": direction,
            "amount": valor_op, "result": result["result"],
            "profit": profit, "timestamp": datetime.now().isoformat(),
            "cenario": cenario_final, "nivel_mg": nivel_nome,
            "sequencia_id": seq_id,
            "fonte": fonte,
            "modo": quotex.account_mode,
            "duracao": duracao,
        }
        if extra_fields:
            op.update(extra_fields)
        skill_register_operation(op)

        if result["result"] in ("WIN", "DOJI"):
            break

    return {"cenario": cenario_final, "lucro": lucro_seq}


def _verificar_config_avisos(cfg: dict) -> None:
    """Exibe avisos se config tiver valores incomuns (nao bloqueia, apenas informa)."""
    avisos = []
    if float(cfg.get("entrada_padrao", 10)) > 100:
        avisos.append(f"  [!] Entrada alta: R${cfg['entrada_padrao']} (comum <= R$100)")
    if int(cfg.get("niveis_mg", 3)) >= 5:
        avisos.append(f"  [!] Niveis MG: {cfg['niveis_mg']} (>= 5 — risco de perda expressiva)")
    if float(cfg.get("limite_perda_pct", 20)) > 30:
        avisos.append(f"  [!] Limite perda: {cfg['limite_perda_pct']}% (acima de 30%)")
    if float(cfg.get("payout_minimo_pct", 75)) < 70:
        avisos.append(f"  [!] Payout minimo: {cfg['payout_minimo_pct']}% (abaixo de 70% pode ser desvantajoso)")
    if avisos:
        print("\n  [CONFIG] Valores incomuns detectados:")
        for a in avisos:
            print(a)
        log_s("WARN", "Config com valores incomuns: " + " | ".join(a.strip() for a in avisos))
        print()


def loop_quotex(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop automatico conectado na Quotex via websocket."""
    try:
        asyncio.run(_loop_quotex_async(protetor, analisador))
    except KeyboardInterrupt:
        pass


async def _loop_quotex_async(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop async que opera na Quotex real."""

    # --- Conectar com retry ---
    cfg = carregar_config()
    print("\n  [QUOTEX] Conectando via websocket... (modo: {})".format(cfg.get("account_mode", "PRACTICE")))
    try:
        quotex = AgentQuotex(account_mode=cfg.get("account_mode", "PRACTICE"))
    except Exception as e:
        print(f"  [ERRO] {type(e).__name__}: {e}")
        return

    if not await _conectar_com_retry(quotex, cfg):
        return

    saldo_info = await quotex.get_saldo()
    print(f"  [OK] Conectado! Modo: {quotex.account_mode}")
    print(f"  Usuario: {saldo_info['usuario']}")
    print(f"  Saldo:   R$ {saldo_info['saldo']}")
    protetor.sincronizar_saldo(saldo_info['saldo'])
    log_s("INFO", f"Loop QUOTEX iniciado | Modo: {quotex.account_mode} | Saldo: R${saldo_info['saldo']}")
    _verificar_config_avisos(cfg)
    print()

    # --- Selecionar ativo da lista ---
    print("  Carregando ativos disponiveis...")
    ativos = _listar_ativos_quotex(quotex, cfg)

    if not ativos:
        print(f"  [AVISO] Nenhum ativo aberto com payout >= {cfg.get('payout_minimo_pct')}%")
        print(f"  Tipo filtrado: {cfg.get('tipo_ativo')} | Ajuste o config se necessario.")
        await quotex.desconectar()
        return

    print(f"\n  Ativos disponiveis ({len(ativos)}) - ordenados por payout:\n")
    print(f"  {'N':>4}  {'Par de Ativo':<24} {'Payout':>7}  {'Mercado'}")
    print(f"  {'-'*4}  {'-'*24} {'-'*7}  {'-'*12}")
    for i, a in enumerate(ativos, 1):
        print(f"  {i:>4}. {a['display']:<24} {a['payout_pct']:>6}%  {a['mercado']}")
    print()

    v, cancel = _inp("Numero do ativo (0=voltar)", "1")
    if cancel:
        await quotex.desconectar()
        return
    try:
        idx = int(v) - 1
        if not (0 <= idx < len(ativos)):
            raise ValueError()
        ativo_sel = ativos[idx]
        asset = ativo_sel["interno"]
        print(f"  -> Selecionado: {ativo_sel['display']} | Payout: {ativo_sel['payout_pct']}%\n")
    except ValueError:
        print("  [ERRO] Numero invalido. Saindo.")
        await quotex.desconectar()
        return

    print("  Configuracao (Enter = valor padrao, 0 = voltar ao menu):")
    direction, cancel = _inp("Direcao call/put", "call")
    if cancel:
        await quotex.desconectar()
        return
    direction = direction.lower()

    v, cancel = _inp("Entrada R$", cfg["entrada_padrao"])
    if cancel: await quotex.desconectar(); return
    valor = float(v)

    v, cancel = _inp("Max sequencias", cfg["max_ops_sessao"])
    if cancel: await quotex.desconectar(); return
    max_ops = int(v)

    v, cancel = _inp("Niveis MG", cfg["niveis_mg"])
    if cancel: await quotex.desconectar(); return
    niveis = int(v)

    v, cancel = _inp("Duracao do trade (seg)", cfg["duracao_padrao"])
    if cancel: await quotex.desconectar(); return
    duracao = int(v)

    v, cancel = _inp("Intervalo entre sequencias (seg)", 5)
    if cancel: await quotex.desconectar(); return
    intervalo = float(v)

    # Verifica asset (real-time)
    asset_info = await quotex.check_asset(asset)
    if not asset_info["aberto"]:
        print(f"\n  [ERRO] {asset} esta fechado no momento.")
        ativos_alt = _listar_ativos_quotex(quotex, cfg)
        if ativos_alt:
            print(f"\n  Ativos disponiveis agora (top 5):")
            for i, a in enumerate(ativos_alt[:_TOP_ATIVOS_EXIBIR], 1):
                print(f"    {i}. {a['display']:<24} {a['payout_pct']:>6}%")
        print("\n  Volte ao menu e selecione outro ativo.")
        await quotex.desconectar()
        return

    asset_real = asset_info["asset_resolvido"]
    payout_info = quotex.get_payout(asset)
    payout = payout_info["payout"]
    payout_inicial = payout  # referencia para detectar mudancas durante a sessao

    print()
    print("=" * 55)
    print(f"  QUOTEX - OPERACAO REAL ({quotex.account_mode})")
    print(f"  {asset_real} | {direction} | R${valor} | {duracao}s")
    print(f"  Payout: {payout_info['payout_pct']}% | {niveis} niveis MG")
    print(f"  Ctrl+C para parar")
    print("=" * 55)
    print()

    seq_num = 0
    cenarios_3_session = 0
    lucro_session = 0.0

    handler_orig = _instalar_shutdown_gracioso()
    try:
        while seq_num < max_ops and not _shutdown_gracioso.is_set():
            seq_num += 1
            hora = datetime.now().strftime("%H:%M:%S")

            print(f"\n{'#' * 55}")
            print(f"  [{hora}] SEQUENCIA {seq_num}/{max_ops} - QUOTEX {quotex.account_mode}")
            print(f"{'#' * 55}")

            # --- PROTETOR ---
            print(f"\n  [PROTETOR] Verificando...")
            pre = protetor.verificar()
            if not pre["pode_continuar"]:
                print(f"  *** BLOQUEADO: {pre.get('motivo_bloqueio')} ***")
                log_s("WARN", f"Protetor bloqueou loop QUOTEX: {pre.get('motivo_bloqueio')}")
                break
            print(f"  -> OK (Risco: {pre['perda_pct']}%)")
            if pre.get("loss_streak_atual", 0) >= pre.get("max_loss_streak", 5) - 1 and pre.get("loss_streak_atual", 0) > 0:
                print(f"  [!] AVISO: Loss streak {pre['loss_streak_atual']} — proxima loss pode bloquear (max: {pre['max_loss_streak']})")
                log_s("WARN", f"Loss streak {pre['loss_streak_atual']}/{pre['max_loss_streak']} — proximo bloqueio iminente")

            # --- Filtro de horario ---
            if not _esta_no_horario(cfg):
                h = datetime.now().strftime("%H:%M")
                print(f"  [HORARIO] {h} fora da janela {cfg['horario_inicio']}-{cfg['horario_fim']}. Encerrando.")
                await quotex.desconectar()
                return

            # Verificar se payout mudou desde o inicio da sessao
            payout_info_agora = quotex.get_payout(asset)
            if payout_info_agora["payout"] > 0 and payout_info_agora["payout"] != payout:
                print(f"  [!] AVISO: Payout mudou: {round(payout * 100)}% -> {payout_info_agora['payout_pct']}%")
                log_s("WARN", f"Payout mudou durante sessao: {round(payout * 100)}% -> {payout_info_agora['payout_pct']}% ({asset})")
                payout = payout_info_agora["payout"]

            # --- GERENCIADOR COM MG (Quotex real) ---
            res_mg = await _executar_ciclo_mg(
                quotex, asset_real, direction, valor, niveis, payout, duracao,
                cfg=cfg, fonte="quotex", aguardar_candle=True,
                saldo_atual=pre.get("saldo_atual"),
            )
            cenario_final = res_mg["cenario"]
            lucro_seq     = res_mg["lucro"]

            lucro_session += lucro_seq
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"Cenario 3 QUOTEX {asset_real}", {"perda": lucro_seq})

            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")
            log_s("INFO", f"Ciclo QUOTEX | {asset_real} | {direction.upper()} | C{cenario_final} | R${round(lucro_seq,2)}")

            # --- ANALISADOR ---
            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            # Saldo real da Quotex - sincroniza protetor
            saldo_real = await quotex.get_saldo()
            protetor.sincronizar_saldo(saldo_real['saldo'])
            print(f"\n  Saldo Quotex: R${saldo_real['saldo']} | Taxa: {met['taxa_acerto']}% | Acao: {rec['acao']}")

            if rec["acao"] == "PAUSAR":
                for m in rec["motivos"]:
                    print(f"  -> {m}")
                print(f"\n  Loop PAUSADO pelo Analisador.")
                log_s("WARN", f"Analisador PAUSOU loop QUOTEX | {' | '.join(rec['motivos'])}")
                break
            elif rec["acao"] == "AJUSTAR":
                log_s("WARN", f"Analisador AJUSTAR (QUOTEX) | {' | '.join(rec['motivos'])}")

            if seq_num < max_ops and not _shutdown_gracioso.is_set():
                print(f"\n  Aguardando {intervalo}s...")
                await _sleep_cancelavel(intervalo)

    except KeyboardInterrupt:
        print(f"\n\n  [!] Saida imediata")
    finally:
        _restaurar_shutdown(handler_orig)
        if _shutdown_gracioso.is_set():
            print(f"\n  [!] Encerrado apos operacao registrada.")

    # Relatorio final
    print(f"\n{'=' * 55}")
    print(f"  RELATORIO FINAL - QUOTEX {quotex.account_mode}")
    print(f"{'=' * 55}\n")
    analisador.gerar_relatorio(salvar=True, imprimir=True)

    try:
        saldo_final = await quotex.get_saldo()
        print(f"\n  Saldo Quotex final: R${saldo_final['saldo']}")
    except Exception:
        pass

    print(f"  Sequencias: {seq_num} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")
    print(f"  [Salvo em relatorio.txt]")
    print(f"{'=' * 55}\n")
    log_s("INFO", f"Loop QUOTEX encerrado | Sequencias: {seq_num} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")

    await quotex.desconectar()
    print("  [Quotex desconectado]\n")


# ============================================================
# LOOP TELEGRAM (sinais automaticos via Telegram)
# ============================================================

def loop_telegram(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop automatico que executa sinais recebidos do Telegram."""
    try:
        asyncio.run(_loop_telegram_async(protetor, analisador))
    except KeyboardInterrupt:
        pass


async def _loop_telegram_async(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop async: escuta Telegram + executa na Quotex."""
    cfg = carregar_config()
    _verificar_config_avisos(cfg)

    print("\n  [TELEGRAM] Configuracao (Enter = valor padrao, 0 = voltar ao menu):")

    v, cancel = _inp("Entrada base R$", cfg["entrada_padrao"])
    if cancel: return
    valor = float(v)

    v, cancel = _inp("Niveis MG", cfg["niveis_mg"])
    if cancel: return
    niveis = int(v)

    v, cancel = _inp("Payout minimo %", cfg["payout_minimo_pct_telegram"])
    if cancel: return
    payout_min = float(v) / 100

    v, cancel = _inp("Duracao padrao (seg) se sinal nao informar", cfg["duracao_padrao"])
    if cancel: return
    duracao_default = int(v)

    # --- Conectar Quotex com retry ---
    print("\n  [QUOTEX] Conectando... (modo: {})".format(cfg.get("account_mode", "PRACTICE")))
    try:
        quotex = AgentQuotex(account_mode=cfg.get("account_mode", "PRACTICE"))
    except Exception as e:
        print(f"  [ERRO] {e}")
        return

    if not await _conectar_com_retry(quotex, cfg):
        return

    saldo_info = await quotex.get_saldo()
    print(f"  [OK] Saldo: R${saldo_info['saldo']} ({quotex.account_mode})")
    protetor.sincronizar_saldo(saldo_info['saldo'])

    # --- Conectar Telegram ---
    print("\n  [TELEGRAM] Conectando... (1a vez: aguarde codigo SMS)")
    try:
        telegram = AgentTelegram()
        await telegram.conectar()
    except Exception as e:
        print(f"  [ERRO] Telegram: {e}")
        await quotex.desconectar()
        return

    print(f"  [OK] Escutando {telegram.bot_username}")
    print(f"  Offset: {telegram.time_offset} min | Payout min: {payout_min*100:.0f}%")
    print(f"\n  Aguardando sinais... (Ctrl+C para parar)\n")
    log_s("INFO", f"Loop TELEGRAM iniciado | Bot: {telegram.bot_username} | Modo: {quotex.account_mode}")

    lucro_session = 0.0
    cenarios_3_session = 0
    ops_executadas = 0

    # Escuta Telegram em task paralela
    escuta_task = asyncio.create_task(telegram.escutar())

    handler_orig = _instalar_shutdown_gracioso()
    try:
        while not _shutdown_gracioso.is_set():
            hora = datetime.now().strftime("%H:%M:%S")
            print(f"  [{hora}] Aguardando sinal na fila...", end="\r")

            sinal = await telegram.proximo_sinal()

            print(f"\n  {'#' * 55}")
            print(f"  SINAL: {sinal['ativo']} {sinal['direcao'].upper()} {sinal['duracao']}s")
            print(f"  {'#' * 55}")

            # --- Filtros do config (recarrega a cada sinal) ---
            cfg_atual = carregar_config()

            # Filtro tipo_ativo (OTC / NAO_OTC / AMBOS)
            tipo_ativo = cfg_atual.get("tipo_ativo", "AMBOS")
            is_otc = "_otc" in sinal["ativo"].lower()

            if tipo_ativo == "OTC" and not is_otc:
                print(f"  [SKIP] {sinal['ativo']} nao e OTC (config: apenas OTC)")
                continue
            elif tipo_ativo == "NAO_OTC" and is_otc:
                print(f"  [SKIP] {sinal['ativo']} e OTC (config: apenas NAO-OTC)")
                continue

            # Filtro tipo_mercado (FOREX / CRIPTO / MATERIA_PRIMA / ACAO / AMBOS)
            tipo_mercado = cfg_atual.get("tipo_mercado", "AMBOS")
            if tipo_mercado != "AMBOS":
                mercado = _classificar_ativo(sinal["ativo"].replace("_otc", "").upper())
                if mercado != tipo_mercado:
                    print(f"  [SKIP] {sinal['ativo']} e {mercado} (config: apenas {tipo_mercado})")
                    continue

            # Aguardar horario de entrada se especificado
            if sinal.get("horario"):
                ok = await _aguardar_horario(sinal["horario"], janela_seg=int(cfg_atual.get("janela_execucao_seg", 5)))
                if not ok:
                    continue  # sinal atrasado, descarta

            # --- Protetor ---
            pre = protetor.verificar()
            if not pre["pode_continuar"]:
                print(f"  *** BLOQUEADO: {pre.get('motivo_bloqueio')} ***")
                log_s("WARN", f"Protetor bloqueou loop TELEGRAM: {pre.get('motivo_bloqueio')}")
                continue
            if pre.get("loss_streak_atual", 0) >= pre.get("max_loss_streak", 5) - 1 and pre.get("loss_streak_atual", 0) > 0:
                print(f"  [!] AVISO: Loss streak {pre['loss_streak_atual']} — proxima loss pode bloquear (max: {pre['max_loss_streak']})")
                log_s("WARN", f"Loss streak {pre['loss_streak_atual']}/{pre['max_loss_streak']} — proximo bloqueio iminente")

            # --- Verificar ativo e payout ---
            ativo = sinal["ativo"]
            try:
                asset_info = await quotex.check_asset(ativo)
                if not asset_info["aberto"]:
                    print(f"  [SKIP] {ativo} esta fechado no momento")
                    continue

                payout_info = quotex.get_payout(ativo)
                payout = payout_info["payout"]

                if payout < payout_min:
                    print(f"  [SKIP] Payout {payout_info['payout_pct']}% < minimo {payout_min*100:.0f}%")
                    continue

                asset_real = asset_info["asset_resolvido"]
                print(f"  Payout: {payout_info['payout_pct']}% | Asset: {asset_real}")

            except Exception as e:
                print(f"  [ERRO] Verificando ativo: {e}")
                continue

            # --- Filtro de horario ---
            if not _esta_no_horario(cfg_atual):
                h = datetime.now().strftime("%H:%M")
                print(f"  [HORARIO] {h} fora da janela {cfg_atual['horario_inicio']}-{cfg_atual['horario_fim']}. Pulando sinal.")
                continue

            # --- Executar MG ---
            direcao = sinal["direcao"]
            duracao = sinal.get("duracao") or duracao_default
            res_mg = await _executar_ciclo_mg(
                quotex, asset_real, direcao, valor, niveis, payout, duracao,
                cfg=cfg_atual, fonte="telegram", horario_sinal=sinal.get("horario"),
                saldo_atual=pre.get("saldo_atual"),
            )
            cenario_final = res_mg["cenario"]
            lucro_seq     = res_mg["lucro"]

            lucro_session += lucro_seq
            ops_executadas += 1
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"C3 via Telegram {asset_real}", {"perda": lucro_seq})

            labels = {
                1: "CENARIO 1 - WIN DIRETO",
                2: "CENARIO 2 - RECUPEROU NO MG",
                3: "*** CENARIO 3 - PERDA TOTAL ***",
            }
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")
            log_s("INFO", f"Ciclo TELEGRAM | {asset_real} | {direcao.upper()} | C{cenario_final} | R${round(lucro_seq,2)}")

            # --- Analisador ---
            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            saldo_real = await quotex.get_saldo()
            protetor.sincronizar_saldo(saldo_real['saldo'])
            print(f"  Saldo: R${saldo_real['saldo']} | Taxa: {met['taxa_acerto']}% | Acao: {rec['acao']}")
            print(f"  Sinais na fila: {telegram.sinais_pendentes()}")

            if rec["acao"] == "PAUSAR":
                motivo = rec["motivos"][0] if rec["motivos"] else ""
                print(f"  [ANALISADOR] PAUSANDO - {motivo}")
                print(f"  (novo sinal necessario para retomar)")
                log_s("WARN", f"Analisador PAUSOU loop TELEGRAM | {' | '.join(rec['motivos'])}")
            elif rec["acao"] == "AJUSTAR":
                log_s("WARN", f"Analisador AJUSTAR (TELEGRAM) | {' | '.join(rec['motivos'])}")

    except KeyboardInterrupt:
        print(f"\n\n  [!] Saida imediata")
    finally:
        _restaurar_shutdown(handler_orig)
        if _shutdown_gracioso.is_set():
            print(f"\n  [!] Encerrado apos operacao registrada.")

    escuta_task.cancel()

    # Relatorio final
    print(f"\n{'=' * 55}")
    print(f"  RELATORIO FINAL - MODO TELEGRAM")
    print(f"{'=' * 55}\n")
    analisador.gerar_relatorio(salvar=True, imprimir=True)
    print(f"\n  Ops executadas: {ops_executadas} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")
    print(f"  {telegram.status()}")
    print(f"  [Salvo em relatorio.txt]")
    print(f"{'=' * 55}\n")
    log_s("INFO", f"Loop TELEGRAM encerrado | Ops: {ops_executadas} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")

    await telegram.desconectar()
    await quotex.desconectar()
    print("  [Desconectado]\n")


def loop_lista(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop automatico que executa sinais pre-definidos de um arquivo JSON."""
    try:
        asyncio.run(_loop_lista_async(protetor, analisador))
    except KeyboardInterrupt:
        pass


async def _loop_lista_async(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop async: carrega lista de sinais de arquivo JSON e executa na Quotex."""
    cfg = carregar_config()
    _verificar_config_avisos(cfg)

    print("\n  [LISTA] Configuracao (Enter = valor padrao, 0 = voltar ao menu):")

    v, cancel = _inp("Arquivo de sinais", "data/sinais.json")
    if cancel: return
    arquivo = Path(v)
    if not arquivo.is_absolute():
        arquivo = Path(__file__).resolve().parent / arquivo

    if not arquivo.exists():
        print(f"\n  [ERRO] Arquivo nao encontrado: {arquivo}")
        print(f"  Crie o arquivo com a lista de sinais no formato JSON.")
        print(f"  Exemplo: sinais.json na mesma pasta do sistema.")
        return

    try:
        with open(arquivo, "r", encoding="utf-8") as f:
            sinais_raw = json.load(f)
    except Exception as e:
        print(f"  [ERRO] Lendo arquivo: {e}")
        return

    if not sinais_raw:
        print("  [AVISO] Lista de sinais vazia.")
        return

    v, cancel = _inp("Entrada base R$", cfg["entrada_padrao"])
    if cancel: return
    valor = float(v)

    v, cancel = _inp("Niveis MG", cfg["niveis_mg"])
    if cancel: return
    niveis = int(v)

    v, cancel = _inp("Duracao padrao (seg) se sinal nao informar", cfg["duracao_padrao"])
    if cancel: return
    duracao_default = int(v)

    # --- Conectar Quotex ---
    print("\n  [QUOTEX] Conectando... (modo: {})".format(cfg.get("account_mode", "PRACTICE")))
    try:
        quotex = AgentQuotex(account_mode=cfg.get("account_mode", "PRACTICE"))
    except Exception as e:
        print(f"  [ERRO] {e}")
        return

    if not await _conectar_com_retry(quotex, cfg):
        return

    saldo_info = await quotex.get_saldo()
    print(f"  [OK] Saldo: R${saldo_info['saldo']} ({quotex.account_mode})")
    protetor.sincronizar_saldo(saldo_info['saldo'])

    # Ordena sinais por horario (sem horario = executa imediatamente, vai ao final)
    def _sort_key(s):
        return s.get("horario") or "99:99"

    sinais = sorted(sinais_raw, key=_sort_key)

    # Exibe lista resumida para confirmacao
    print(f"\n  {len(sinais)} sinais carregados ({arquivo.name}):\n")
    print(f"  {'N':>4}  {'Ativo':<22} {'Dir':<5} {'Horario':>8}  {'Dur':>5}")
    print(f"  {'-'*4}  {'-'*22} {'-'*5} {'-'*8}  {'-'*5}")
    for i, s in enumerate(sinais, 1):
        h = s.get("horario", "imediato")
        dur = s.get("duracao", duracao_default)
        print(f"  {i:>4}. {s['ativo']:<22} {s['direcao'].upper():<5} {h:>8}  {dur:>5}s")
    print()

    confirma = input("  Confirmar execucao? (s/N): ").strip().lower()
    if confirma != "s":
        await quotex.desconectar()
        return

    print()
    print("=" * 55)
    print(f"  LOOP LISTA - {len(sinais)} sinais ({quotex.account_mode})")
    print(f"  Ctrl+C para parar")
    print("=" * 55)
    print()
    log_s("INFO", f"Loop LISTA iniciado | {len(sinais)} sinais | Modo: {quotex.account_mode}")

    lucro_session = 0.0
    cenarios_3_session = 0
    ops_executadas = 0

    handler_orig = _instalar_shutdown_gracioso()
    try:
        for sinal in sinais:
            if _shutdown_gracioso.is_set():
                break

            hora = datetime.now().strftime("%H:%M:%S")
            print(f"\n{'#' * 55}")
            print(f"  [{hora}] SINAL {ops_executadas + 1}/{len(sinais)}: {sinal['ativo']} {sinal['direcao'].upper()} {sinal.get('horario', 'imediato')}")
            print(f"{'#' * 55}")

            # Recarrega config a cada sinal
            cfg_atual = carregar_config()

            # Filtro tipo_ativo
            tipo_ativo = cfg_atual.get("tipo_ativo", "AMBOS")
            is_otc = "_otc" in sinal["ativo"].lower()
            if tipo_ativo == "OTC" and not is_otc:
                print(f"  [SKIP] {sinal['ativo']} nao e OTC (config: apenas OTC)")
                continue
            elif tipo_ativo == "NAO_OTC" and is_otc:
                print(f"  [SKIP] {sinal['ativo']} e OTC (config: apenas NAO-OTC)")
                continue

            # Filtro tipo_mercado
            tipo_mercado = cfg_atual.get("tipo_mercado", "AMBOS")
            if tipo_mercado != "AMBOS":
                mercado = _classificar_ativo(sinal["ativo"].replace("_otc", "").upper())
                if mercado != tipo_mercado:
                    print(f"  [SKIP] {sinal['ativo']} e {mercado} (config: apenas {tipo_mercado})")
                    continue

            # Aguardar horario de entrada se especificado
            if sinal.get("horario"):
                ok = await _aguardar_horario(sinal["horario"], janela_seg=int(cfg_atual.get("janela_execucao_seg", 5)))
                if not ok:
                    continue  # sinal atrasado, descarta

            # Protetor
            pre = protetor.verificar()
            if not pre["pode_continuar"]:
                print(f"  *** BLOQUEADO: {pre.get('motivo_bloqueio')} ***")
                log_s("WARN", f"Protetor bloqueou loop LISTA: {pre.get('motivo_bloqueio')}")
                break
            if pre.get("loss_streak_atual", 0) >= pre.get("max_loss_streak", 5) - 1 and pre.get("loss_streak_atual", 0) > 0:
                print(f"  [!] AVISO: Loss streak {pre['loss_streak_atual']} — proxima loss pode bloquear (max: {pre['max_loss_streak']})")
                log_s("WARN", f"Loss streak {pre['loss_streak_atual']}/{pre['max_loss_streak']} — proximo bloqueio iminente")

            # Verificar ativo e payout
            ativo = sinal["ativo"]
            payout_min = float(cfg_atual.get("payout_minimo_pct", 0)) / 100
            try:
                asset_info = await quotex.check_asset(ativo)
                if not asset_info["aberto"]:
                    print(f"  [SKIP] {ativo} esta fechado no momento")
                    continue

                payout_info = quotex.get_payout(ativo)
                payout = payout_info["payout"]

                if payout < payout_min:
                    print(f"  [SKIP] Payout {payout_info['payout_pct']}% < minimo {payout_min*100:.0f}%")
                    continue

                asset_real = asset_info["asset_resolvido"]
                print(f"  Payout: {payout_info['payout_pct']}% | Asset: {asset_real}")

            except Exception as e:
                print(f"  [ERRO] Verificando ativo: {e}")
                continue

            # Filtro de horario
            if not _esta_no_horario(cfg_atual):
                h = datetime.now().strftime("%H:%M")
                print(f"  [HORARIO] {h} fora da janela {cfg_atual['horario_inicio']}-{cfg_atual['horario_fim']}. Pulando sinal.")
                continue

            # Executar MG
            direcao = sinal["direcao"]
            duracao = sinal.get("duracao") or duracao_default
            res_mg = await _executar_ciclo_mg(
                quotex, asset_real, direcao, valor, niveis, payout, duracao,
                cfg=cfg_atual, fonte="lista", horario_sinal=sinal.get("horario"),
                saldo_atual=pre.get("saldo_atual"),
            )
            cenario_final = res_mg["cenario"]
            lucro_seq     = res_mg["lucro"]

            lucro_session += lucro_seq
            ops_executadas += 1
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"C3 via Lista {asset_real}", {"perda": lucro_seq})

            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")
            log_s("INFO", f"Ciclo LISTA | {asset_real} | {direcao.upper()} | C{cenario_final} | R${round(lucro_seq,2)}")

            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            saldo_real = await quotex.get_saldo()
            protetor.sincronizar_saldo(saldo_real['saldo'])
            print(f"  Saldo: R${saldo_real['saldo']} | Taxa: {met['taxa_acerto']}% | Sinais restantes: {len(sinais) - ops_executadas}")

            if rec["acao"] == "PAUSAR":
                motivo = rec["motivos"][0] if rec["motivos"] else ""
                print(f"  [ANALISADOR] PAUSANDO - {motivo}")
                log_s("WARN", f"Analisador PAUSOU loop LISTA | {' | '.join(rec['motivos'])}")
                break
            elif rec["acao"] == "AJUSTAR":
                log_s("WARN", f"Analisador AJUSTAR (LISTA) | {' | '.join(rec['motivos'])}")

    except KeyboardInterrupt:
        print(f"\n\n  [!] Saida imediata")
    finally:
        _restaurar_shutdown(handler_orig)
        if _shutdown_gracioso.is_set():
            print(f"\n  [!] Encerrado apos operacao registrada.")

    # Relatorio final
    print(f"\n{'=' * 55}")
    print(f"  RELATORIO FINAL - MODO LISTA")
    print(f"{'=' * 55}\n")
    analisador.gerar_relatorio(salvar=True, imprimir=True)
    print(f"\n  Executados: {ops_executadas}/{len(sinais)} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")
    print(f"  [Salvo em relatorio.txt]")
    print(f"{'=' * 55}\n")
    log_s("INFO", f"Loop LISTA encerrado | Executados: {ops_executadas}/{len(sinais)} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")

    await quotex.desconectar()
    print("  [Desconectado]\n")


# ============================================================
# LOOP AUTONOMO (estrategias tecnicas)
# ============================================================

def loop_autonomo(protetor: AgentProtetor, analisador: AgentAnalisador, verificador: AgentVerificador,
                  preset: dict | None = None):
    """Loop automatico guiado por estrategias tecnicas (sem sinais externos)."""
    try:
        asyncio.run(_loop_autonomo_async(protetor, analisador, verificador, preset=preset))
    except KeyboardInterrupt:
        pass


async def _loop_autonomo_async(protetor: AgentProtetor, analisador: AgentAnalisador, verificador: AgentVerificador,
                               preset: dict | None = None):
    """Loop async: analisa candles a cada abertura e executa se a estrategia sinalizar.

    preset: se fornecido (vindo do backteste ranking), pula os prompts interativos de
    configuracao e inicia diretamente com o ativo/estrategia/params pre-definidos.
    Chaves esperadas: asset, asset_display, payout_pct, estrategia_nome, duracao,
                      entrada, niveis_mg.
    """
    from estrategias import ESTRATEGIAS, ESTRATEGIAS_META, executar_estrategia

    cfg = carregar_config()
    _verificar_config_avisos(cfg)

    if preset is None:
        print("\n  [AUTONOMO] Configuracao (Enter = valor padrao, 0 = voltar ao menu):")

    # --- Conectar Quotex ---
    print("\n  [QUOTEX] Conectando... (modo: {})".format(cfg.get("account_mode", "PRACTICE")))
    try:
        quotex = AgentQuotex(account_mode=cfg.get("account_mode", "PRACTICE"))
    except Exception as e:
        print(f"  [ERRO] {e}")
        return

    if not await _conectar_com_retry(quotex, cfg):
        return

    saldo_info = await quotex.get_saldo()
    print(f"  [OK] Saldo: R${saldo_info['saldo']} ({quotex.account_mode})")
    protetor.sincronizar_saldo(saldo_info['saldo'])

    if preset is not None:
        # --- Configuracao pre-definida pelo backteste ranking ---
        asset           = preset["asset"]
        estrategia_nome = preset["estrategia_nome"]
        valor           = preset["entrada"]
        niveis          = preset["niveis_mg"]
        duracao         = preset["duracao"]
        tf_rec          = ESTRATEGIAS_META.get(estrategia_nome, {}).get("timeframe_rec")
        cfg["estrategia_ativa"] = estrategia_nome
        salvar_config(cfg)
        tf_label_p = f"M{duracao // 60}" if duracao < 3600 else f"H{duracao // 3600}"
        print(f"\n  [RANKING -> AUTONOMO] Configuracao pre-definida:")
        print(f"  Ativo:      {preset['asset_display']} | Payout: {preset['payout_pct']}%")
        print(f"  Estrategia: {estrategia_nome} | {tf_label_p} ({duracao}s)")
        print(f"  Entrada:    R${valor:.2f} | MG {niveis} niveis\n")
    else:
        # --- Selecionar ativo (interativo) ---
        print("  Carregando ativos disponiveis...")
        ativos = _listar_ativos_quotex(quotex, cfg)

        if not ativos:
            print(f"  [AVISO] Nenhum ativo aberto com os filtros atuais. Ajuste o config.")
            await quotex.desconectar()
            return

        # Exibe top-3 como preview
        print(f"\n  Top payouts disponiveis:")
        for a in ativos[:3]:
            print(f"    {a['display']:<24} {a['payout_pct']:>6}%  {a['mercado']}")
        if len(ativos) > 3:
            print(f"    ... e mais {len(ativos) - 3} ativos")
        print()

        v, cancel = _inp("Selecionar melhor payout automaticamente? (S/n)", "s")
        if cancel:
            await quotex.desconectar()
            return

        if v.strip().lower() != "n":
            ativo_sel = ativos[0]
            asset = ativo_sel["interno"]
            print(f"  -> Auto: {ativo_sel['display']} | Payout: {ativo_sel['payout_pct']}%\n")
        else:
            print(f"  Ativos disponiveis ({len(ativos)}) - ordenados por payout:\n")
            print(f"  {'N':>4}  {'Par de Ativo':<24} {'Payout':>7}  {'Mercado'}")
            print(f"  {'-'*4}  {'-'*24} {'-'*7}  {'-'*12}")
            for i, a in enumerate(ativos, 1):
                print(f"  {i:>4}. {a['display']:<24} {a['payout_pct']:>6}%  {a['mercado']}")
            print()
            v, cancel = _inp("Numero do ativo", "1")
            if cancel:
                await quotex.desconectar()
                return
            try:
                idx = int(v) - 1
                if not (0 <= idx < len(ativos)):
                    raise ValueError()
                ativo_sel = ativos[idx]
                asset = ativo_sel["interno"]
                print(f"  -> {ativo_sel['display']} | Payout: {ativo_sel['payout_pct']}%\n")
            except ValueError:
                print("  [ERRO] Numero invalido.")
                await quotex.desconectar()
                return

        # --- Selecionar estrategia ---
        nomes_est = list(ESTRATEGIAS.keys())
        est_atual = cfg.get("estrategia_ativa", "EMA_RSI")
        print(f"  Estrategias disponiveis:\n")
        for i, nome in enumerate(nomes_est, 1):
            meta = ESTRATEGIAS_META.get(nome, {})
            desc = meta.get("descricao", "")
            tf = meta.get("timeframe_rec")
            tf_str = f"  [rec: M{tf//60 if tf and tf < 3600 else (tf//3600 if tf else 0)}]" if tf else ""
            marca = "  <- atual" if nome == est_atual else ""
            print(f"    {i} - {nome}{tf_str}{marca}")
            if desc:
                print(f"        {desc}")
        print()

        idx_default = nomes_est.index(est_atual) + 1 if est_atual in nomes_est else 1
        v, cancel = _inp("Numero da estrategia", str(idx_default))
        if cancel:
            await quotex.desconectar()
            return
        try:
            estrategia_nome = nomes_est[int(v) - 1]
        except (ValueError, IndexError):
            estrategia_nome = nomes_est[0]

        meta_sel = ESTRATEGIAS_META.get(estrategia_nome, {})
        tf_rec = meta_sel.get("timeframe_rec")
        if tf_rec:
            tf_label = f"M{tf_rec // 60}" if tf_rec < 3600 else f"H{tf_rec // 3600}"
            print(f"  -> Estrategia: {estrategia_nome}  [timeframe recomendado: {tf_label} = {tf_rec}s]\n")
        else:
            print(f"  -> Estrategia: {estrategia_nome}\n")

        cfg["estrategia_ativa"] = estrategia_nome
        salvar_config(cfg)

        # --- Parametros de operacao ---
        v, cancel = _inp("Entrada R$", cfg["entrada_padrao"])
        if cancel: await quotex.desconectar(); return
        valor = float(v)

        v, cancel = _inp("Niveis MG", cfg["niveis_mg"])
        if cancel: await quotex.desconectar(); return
        niveis = int(v)

        dur_default = tf_rec if tf_rec else cfg["duracao_padrao"]
        v, cancel = _inp("Duracao do candle (seg)", dur_default)
        if cancel: await quotex.desconectar(); return
        duracao = int(v)

        # Alerta de incompatibilidade de timeframe
        if tf_rec and duracao != tf_rec:
            tf_conf  = f"M{duracao // 60}" if duracao < 3600 else f"H{duracao // 3600}"
            tf_recom = f"M{tf_rec // 60}"  if tf_rec  < 3600 else f"H{tf_rec  // 3600}"
            print(f"\n  [!] {estrategia_nome} foi calibrada para {tf_recom} ({tf_rec}s)")
            print(f"      Voce configurou {tf_conf} ({duracao}s).")
            print(f"      Isso pode reduzir a taxa de acerto.")
            v2, cancel2 = _inp("Continuar mesmo assim? (s/N)", "n")
            if cancel2 or v2.strip().lower() != "s":
                await quotex.desconectar()
                return

    # Verifica ativo e payout inicial (real-time)
    asset_info = await quotex.check_asset(asset)
    if not asset_info["aberto"]:
        print(f"\n  [ERRO] {asset} esta fechado no momento.")
        ativos_alt = _listar_ativos_quotex(quotex, cfg)
        if ativos_alt:
            print(f"\n  Ativos disponiveis agora (top 5):")
            for i, a in enumerate(ativos_alt[:_TOP_ATIVOS_EXIBIR], 1):
                print(f"    {i}. {a['display']:<24} {a['payout_pct']:>6}%")
        print("\n  Volte ao menu e selecione outro ativo.")
        await quotex.desconectar()
        return

    asset_real = asset_info["asset_resolvido"]
    payout_info = quotex.get_payout(asset)
    payout = payout_info["payout"]

    print()
    print("=" * 55)
    print(f"  LOOP AUTONOMO ({quotex.account_mode})")
    print(f"  {asset_real} | {duracao}s | Estrategia: {estrategia_nome}")
    print(f"  Payout: {payout_info['payout_pct']}% | MG {niveis} niveis | R${valor}")
    print(f"  Ctrl+C para parar")
    print("=" * 55)
    print()
    log_s("INFO", f"Loop AUTONOMO iniciado | {asset_real} | {estrategia_nome} | {duracao}s | Modo: {quotex.account_mode}")

    lucro_session = 0.0
    cenarios_3_session = 0
    ops_executadas = 0
    candles_analisados = 0
    _timeouts_candle_consecutivos = 0
    _MAX_TIMEOUTS_CANDLE = 3  # reconecta apos N timeouts seguidos

    handler_orig = _instalar_shutdown_gracioso()
    try:
        while not _shutdown_gracioso.is_set():

            # Protetor antes de aguardar candle
            pre = protetor.verificar()
            if not pre["pode_continuar"]:
                print(f"\n  *** BLOQUEADO: {pre.get('motivo_bloqueio')} ***")
                log_s("WARN", f"Protetor bloqueou loop AUTONOMO: {pre.get('motivo_bloqueio')}")
                break
            if pre.get("loss_streak_atual", 0) >= pre.get("max_loss_streak", 5) - 1 and pre.get("loss_streak_atual", 0) > 0:
                print(f"  [!] AVISO: Loss streak {pre['loss_streak_atual']} — proxima loss pode bloquear (max: {pre['max_loss_streak']})")
                log_s("WARN", f"Loss streak {pre['loss_streak_atual']}/{pre['max_loss_streak']} — proximo bloqueio iminente")

            # Aguarda abertura do proximo candle
            await aguardar_proximo_intervalo(duracao)

            if _shutdown_gracioso.is_set():
                break

            hora = datetime.now().strftime("%H:%M:%S")
            candles_analisados += 1
            cfg_atual = carregar_config()

            # Busca candles historicos
            try:
                candles = await asyncio.wait_for(
                    quotex.get_candles(asset, period=duracao, offset=duracao * _FATOR_CANDLES_HIST),
                    timeout=_TIMEOUT_CANDLES_SEG,
                )
                _timeouts_candle_consecutivos = 0  # reset ao receber candles
                if not candles:
                    print(f"  [{hora}] Sem dados de candle.", end=" ", flush=True)
                    ok, ms = _verificar_internet()
                    if ok:
                        print(f"Rede OK ({ms:.0f}ms) — aguardando proximo intervalo.")
                    else:
                        print("Sem internet detectado.")
                        print(f"  [{hora}] [REDE] Aguardando {_ESPERA_RETRY_REDE_SEG}s antes de tentar novamente...")
                        await asyncio.sleep(_ESPERA_RETRY_REDE_SEG)
                    continue
            except asyncio.TimeoutError:
                _timeouts_candle_consecutivos += 1
                print(f"  [{hora}] [TIMEOUT] get_candles nao respondeu em {_TIMEOUT_CANDLES_SEG}s.", end=" ", flush=True)
                ok, ms = _verificar_internet()
                if ok:
                    print(f"Rede OK ({ms:.0f}ms) — possivel instabilidade no servidor.")
                else:
                    print("Sem internet detectado.")
                    print(f"  [{hora}] [REDE] Aguardando {_ESPERA_RETRY_REDE_SEG}s antes de tentar novamente...")
                    await asyncio.sleep(_ESPERA_RETRY_REDE_SEG)
                if _timeouts_candle_consecutivos >= _MAX_TIMEOUTS_CANDLE:
                    print(f"  [{hora}] [RECONEXAO] {_timeouts_candle_consecutivos} timeouts consecutivos — reconectando...")
                    if await _reconectar(quotex, cfg_atual):
                        _timeouts_candle_consecutivos = 0
                    else:
                        break
                continue
            except Exception as e:
                print(f"  [{hora}] [ERRO] Buscando candles: {e}")
                ok, _ = _verificar_internet()
                if not ok:
                    print(f"  [{hora}] [REDE] Sem internet. Aguardando {_ESPERA_RETRY_REDE_SEG}s...")
                    await asyncio.sleep(_ESPERA_RETRY_REDE_SEG)
                continue

            # Executa estrategia
            resultado = executar_estrategia(estrategia_nome, candles, cfg_atual)
            sinal = resultado.get("sinal")
            motivo = resultado.get("motivo", "")
            indicadores = resultado.get("indicadores", {})

            ind_str = " | ".join(f"{k}={v}" for k, v in indicadores.items()) if indicadores else ""

            if not sinal:
                status = f"  [{hora}] Sem sinal - {motivo}"
                if ind_str:
                    status += f" | {ind_str}"
                print(status)
                continue

            # Sinal gerado — exibe destaque
            print(f"\n{'#' * 55}")
            print(f"  [{hora}] SINAL: {sinal.upper()} | {motivo}")
            if ind_str:
                print(f"  Indicadores: {ind_str}")
            print(f"{'#' * 55}")

            # Atualiza payout atual do ativo
            try:
                payout_info = quotex.get_payout(asset)
                payout = payout_info["payout"]
            except Exception:
                pass  # mantém payout anterior

            # Verifica se payout ainda esta acima do minimo; se nao, troca ativo principal
            payout_min_val = float(cfg_atual.get("payout_minimo_pct", 0)) / 100
            if payout < payout_min_val:
                print(f"\n  [PAYOUT] {asset_real}: {payout_info['payout_pct']}% caiu abaixo do minimo {payout_min_val*100:.0f}%")
                print(f"  [PAYOUT] Buscando novo ativo principal...")
                ativos_alt = _listar_ativos_quotex(quotex, cfg_atual)  # filtra por payout_min, ordena desc
                if ativos_alt:
                    melhor = ativos_alt[0]
                    try:
                        alt_info = await quotex.check_asset(melhor["interno"])
                        asset = melhor["interno"]
                        asset_real = alt_info["asset_resolvido"]
                        payout = quotex.get_payout(asset)["payout"]
                        print(f"  [PAYOUT] Novo ativo principal: {melhor['display']} | Payout: {melhor['payout_pct']}%")
                        print(f"  [PAYOUT] Sinal descartado. Analisando novo ativo a partir do proximo candle.")
                    except Exception as e:
                        print(f"  [PAYOUT] Erro ao trocar ativo: {e}. Ignorando sinal.")
                else:
                    print(f"  [PAYOUT] Nenhum ativo com payout >= {payout_min_val*100:.0f}% disponivel. Ignorando sinal.")
                continue  # descarta sinal — proximo candle ja sera do novo ativo

            # Verificador de condicoes de mercado
            _candle_open = math.floor(time.time() / duracao) * duracao
            _tempo_restante = max(0.0, _candle_open + duracao - time.time())
            ver = verificador.verificar(candles, cfg_atual, payout_atual=payout,
                                        tempo_restante_seg=_tempo_restante)
            if not ver["pode_entrar"]:
                print(f"  [{hora}] [VERIF] {ver['motivo']}")
                log_s("WARN", f"Verificador bloqueou sinal | {ver['motivo']}")
                skill_log_alert("WARNING", f"Verificador bloqueou sinal | {ver['motivo']}", ver.get("detalhes"))
                continue

            # Filtro de horario
            if not _esta_no_horario(cfg_atual):
                h = datetime.now().strftime("%H:%M")
                print(f"  [{hora}] [HORARIO] {h} fora da janela {cfg_atual['horario_inicio']}-{cfg_atual['horario_fim']}. Aguardando proximo candle.")
                continue

            # Executa MG
            res_mg = await _executar_ciclo_mg(
                quotex, asset_real, sinal, valor, niveis, payout, duracao,
                cfg=cfg_atual, fonte="autonomo",
                extra_fields={"estrategia": estrategia_nome},
                saldo_atual=pre.get("saldo_atual"),
            )
            cenario_final = res_mg["cenario"]
            lucro_seq     = res_mg["lucro"]

            lucro_session += lucro_seq
            ops_executadas += 1
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"C3 Autonomo {asset_real}",
                                {"perda": lucro_seq, "estrategia": estrategia_nome})

            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")
            log_s("INFO", f"Ciclo AUTONOMO | {asset_real} | {sinal.upper()} | {estrategia_nome} | C{cenario_final} | R${round(lucro_seq,2)}")

            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            saldo_real = await quotex.get_saldo()
            protetor.sincronizar_saldo(saldo_real['saldo'])
            print(f"  Saldo: R${saldo_real['saldo']} | Taxa: {met['taxa_acerto']}% | Ops: {ops_executadas} | Candles: {candles_analisados}")

            if rec["acao"] == "PAUSAR":
                motivo_p = rec["motivos"][0] if rec["motivos"] else ""
                print(f"  [ANALISADOR] PAUSANDO - {motivo_p}")
                log_s("WARN", f"Analisador PAUSOU loop AUTONOMO | {' | '.join(rec['motivos'])}")
                break
            elif rec["acao"] == "AJUSTAR":
                log_s("WARN", f"Analisador AJUSTAR (AUTONOMO) | {' | '.join(rec['motivos'])}")

    except KeyboardInterrupt:
        print(f"\n\n  [!] Saida imediata")
    finally:
        _restaurar_shutdown(handler_orig)
        if _shutdown_gracioso.is_set():
            print(f"\n  [!] Encerrado apos operacao registrada.")

    # Relatorio final
    print(f"\n{'=' * 55}")
    print(f"  RELATORIO FINAL - LOOP AUTONOMO ({estrategia_nome})")
    print(f"{'=' * 55}\n")
    analisador.gerar_relatorio(salvar=True, imprimir=True)
    print(f"\n  Candles analisados: {candles_analisados}")
    print(f"  Operacoes: {ops_executadas} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")
    print(f"  [Salvo em relatorio.txt]")
    print(f"{'=' * 55}\n")
    log_s("INFO", f"Loop AUTONOMO encerrado | {estrategia_nome} | Candles: {candles_analisados} | Ops: {ops_executadas} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")

    await quotex.desconectar()
    print("  [Desconectado]\n")


# ============================================================
# LOOP QUOTEX CANDLE SYNC
# ============================================================

async def aguardar_proximo_intervalo(duracao_seg: int):
    """Aguarda o inicio exato do proximo intervalo do candle.

    Garante que a entrada inicial ocorre na abertura do candle.
    Ex: M5 (300s) -> entrada em 14:00, 14:05, 14:10...
        M1 (60s)  -> entrada em 14:00, 14:01, 14:02...
    """
    import math
    agora = datetime.now()
    seg_hoje = agora.hour * 3600 + agora.minute * 60 + agora.second + agora.microsecond / 1_000_000
    proximo = math.ceil(seg_hoje / duracao_seg) * duracao_seg
    espera = proximo - seg_hoje

    # Se ja estamos no inicio exato do intervalo (< 1s), aguarda o proximo
    if espera < 1.0:
        espera += duracao_seg

    from datetime import timedelta
    horario_entrada = (agora + timedelta(seconds=espera)).strftime("%H:%M:%S")
    minutos = duracao_seg // 60
    print(f"  [CANDLE] Aguardando abertura M{minutos}: {horario_entrada} ({int(espera)}s)")
    await asyncio.sleep(espera)


async def _aguardar_horario(horario_str: str, janela_seg: int = 5) -> bool:
    """Aguarda ate o horario de entrada do sinal M5.

    Args:
        horario_str: Horario alvo no formato HH:MM (ja com offset aplicado)
        janela_seg:  Tolerancia em segundos (janela_execucao_seg do config).
                     Se passou mais que isso, descarta o sinal.

    Returns:
        True  - pode executar (horario ok ou dentro da janela)
        False - sinal fora da janela, deve ser descartado
    """
    from datetime import timedelta

    MAX_ESPERA_SEG = 600    # max 10 min de espera (seguranca)

    agora = datetime.now()
    hoje = agora.date()

    try:
        entrada = datetime.strptime(f"{hoje} {horario_str}", "%Y-%m-%d %H:%M")
    except ValueError:
        return True  # horario invalido, executa imediatamente

    segundos = (entrada - agora).total_seconds()

    if segundos > MAX_ESPERA_SEG:
        # Sinal muito no futuro (improvavel para M5) - executa imediatamente
        print(f"  [TIMING] Horario {horario_str} muito distante ({int(segundos)}s), executando agora")
        return True

    if segundos < -janela_seg:
        # Sinal fora da janela de execucao - descarta
        print(f"  [TIMING] Sinal ATRASADO {int(-segundos)}s (janela: +-{janela_seg}s) - DESCARTADO")
        return False

    if -janela_seg <= segundos < 0:
        # Dentro da janela de tolerancia - executa imediatamente
        print(f"  [TIMING] Sinal {int(-segundos)}s atrasado, dentro da janela (+-{janela_seg}s)")
        return True

    # Futuro: aguarda ate o horario exato
    print(f"  [TIMING] Aguardando ate {horario_str} ({int(segundos)}s)...")
    await asyncio.sleep(segundos)
    return True


# ============================================================
# BACKTESTE (walk-forward, acuracia do sinal sem MG)
# ============================================================

def loop_backteste(protetor: "AgentProtetor", analisador: "AgentAnalisador",
                   verificador: "AgentVerificador"):
    """Wrapper sincrono para o backteste async."""
    print("\n  Modo backteste:")
    print("  1. Simples      (escolhe ativo + estrategia)")
    print("  2. Ranking      (escolhe estrategia, testa top N ativos)")
    print("  3. Como funciona")
    v, cancel = _inp("Modo", "1")
    if cancel:
        return
    if str(v).strip() == "3":
        _backteste_ajuda()
        return
    try:
        if str(v).strip() == "2":
            preset = asyncio.run(_backteste_ranking_async())
            if preset is not None:
                # Usuario optou por iniciar loop autonomo com o ativo selecionado
                loop_autonomo(protetor, analisador, verificador, preset=preset)
        else:
            asyncio.run(_backteste_async())
    except KeyboardInterrupt:
        pass


def _backteste_ajuda():
    """Exibe explicacao de como o backteste funciona."""
    linhas = [
        "",
        "  " + "=" * 60,
        "  COMO FUNCIONA O BACKTESTE",
        "  " + "=" * 60,
        "",
        "  O backteste simula uma estrategia em dados historicos reais",
        "  da Quotex, sem executar trades reais.",
        "",
        "  --- FLUXO ---",
        "",
        "  1. Conecta na Quotex (conta PRACTICE) apenas para baixar",
        "     os candles historicos do ativo selecionado.",
        "",
        "  2. Desconecta imediatamente apos receber os dados.",
        "     Nenhuma ordem e enviada.",
        "",
        "  3. Executa a estrategia candle por candle (walk-forward):",
        "     para cada candle i, a estrategia analisa o historico",
        "     ate i e decide: CALL, PUT ou sem sinal.",
        "",
        "  4. O resultado e verificado no candle seguinte (i+1):",
        "     - WIN:  preco fechou a favor da direcao operada",
        "     - LOSS: preco fechou contra",
        "     - DOJI: preco de fechamento igual ao de abertura",
        "",
        "  --- CALCULO FINANCEIRO ---",
        "",
        "  Sem MG:",
        "    WIN  -> lucro += entrada * payout",
        "    LOSS -> lucro -= entrada",
        "    DOJI -> lucro -= entrada  (Quotex nao reembolsa)",
        "",
        "  Com Martingale (ex: 3 niveis):",
        "    Ciclo C1: WIN na entrada",
        "              -> lucro = entrada * payout",
        "    Ciclo C2: LOSS entrada, WIN no MG",
        "              -> recupera tudo + lucro original",
        "    Ciclo C3: LOSS em todos os niveis",
        "              -> perde o acumulado total",
        "",
        "    Formula do proximo nivel MG:",
        "      valor = (acumulado_perdido + entrada) / payout",
        "",
        "  --- AVALIACOES ---",
        "",
        "  Sem MG:",
        "    EXCELENTE  taxa de acerto >= 60%",
        "    BOM        taxa de acerto >= 55%",
        "    NEUTRO     taxa de acerto >= 50%",
        "    FRACO      taxa de acerto <  50%",
        "",
        "  Com MG:",
        "    EXCELENTE  C3 < 15% e lucro positivo",
        "    BOM        C3 < 20% e lucro positivo",
        "    NEUTRO     C3 < 25% e lucro >= 0",
        "    FRACO      C3 >= 25% ou prejuizo",
        "",
        "  --- LIMITACOES ---",
        "",
        "  - Sem slippage: assume execucao perfeita no fechamento",
        "  - Sem latencia: no real o preco pode variar",
        "  - Dados historicos: bom desempenho no passado nao",
        "    garante resultado futuro",
        "  - MG simplificado: nao simula a espera entre niveis",
        "",
        "  " + "=" * 60,
        "",
    ]
    print("\n".join(linhas))


def _simular_walk_forward(
    candles: list,
    estrategia_nome: str,
    cfg: dict,
    payout: float,
    entrada: float,
    mg_ativo: bool,
    mg_niveis: int,
    duracao: int,
) -> dict:
    """Executa simulacao walk-forward sobre candles historicos.

    Retorna dict com todas as metricas calculadas (wins, losses, lucro, etc.).
    """
    from estrategias import executar_estrategia

    wins = 0
    losses = 0
    dojis = 0
    seq_win = 0
    seq_loss = 0
    max_seq_win = 0
    max_seq_loss = 0
    lucro_simulado = 0.0
    ciclos_c1 = 0
    ciclos_c2 = 0
    ciclos_c3 = 0
    seq_c3 = 0
    max_seq_c3 = 0
    mg_nivel = 1
    mg_acumulado = 0.0
    mg_valor_atual = entrada

    for i in range(len(candles) - 1):
        resultado = executar_estrategia(estrategia_nome, candles[: i + 1], cfg)
        sinal = resultado.get("sinal")
        if not sinal:
            continue

        entry = candles[i]["close"]
        exit_ = candles[i + 1]["close"]
        eh_win  = (sinal == "call" and exit_ > entry) or (sinal == "put" and exit_ < entry)
        eh_doji = exit_ == entry

        if eh_doji:
            dojis += 1
            seq_win = 0
            seq_loss += 1
        elif eh_win:
            wins += 1
            seq_win += 1
            seq_loss = 0
        else:
            losses += 1
            seq_win = 0
            seq_loss += 1
        if seq_win > max_seq_win:
            max_seq_win = seq_win
        if seq_loss > max_seq_loss:
            max_seq_loss = seq_loss

        if mg_ativo:
            if eh_win:
                lucro_ciclo = mg_valor_atual * payout - mg_acumulado
                lucro_simulado += lucro_ciclo
                if mg_nivel == 1:
                    ciclos_c1 += 1
                else:
                    ciclos_c2 += 1
                seq_c3 = 0
                mg_nivel = 1
                mg_acumulado = 0.0
                mg_valor_atual = entrada
            else:
                mg_acumulado += mg_valor_atual
                if mg_nivel < mg_niveis:
                    mg_nivel += 1
                    mg_valor_atual = (mg_acumulado + entrada) / payout
                else:
                    lucro_simulado -= mg_acumulado
                    ciclos_c3 += 1
                    seq_c3 += 1
                    if seq_c3 > max_seq_c3:
                        max_seq_c3 = seq_c3
                    mg_nivel = 1
                    mg_acumulado = 0.0
                    mg_valor_atual = entrada
        else:
            if eh_doji:
                lucro_simulado -= entrada
            elif eh_win:
                lucro_simulado += entrada * payout
            else:
                lucro_simulado -= entrada

    total_candles = len(candles)
    total_sinais = wins + losses + dojis
    decididos = wins + losses
    taxa_acerto = (wins / decididos * 100) if decididos > 0 else 0.0
    freq = (total_candles / total_sinais) if total_sinais > 0 else 0.0
    periodo_h = (total_candles * duracao) / 3600
    tf_label = f"M{duracao // 60}" if duracao < 3600 else f"H{duracao // 3600}"
    pct_sinal = (total_sinais / total_candles * 100) if total_candles > 0 else 0.0
    pct_w = (wins / total_sinais * 100) if total_sinais > 0 else 0.0
    pct_l = (losses / total_sinais * 100) if total_sinais > 0 else 0.0
    pct_d = (dojis / total_sinais * 100) if total_sinais > 0 else 0.0
    sinal_lucro = "+" if lucro_simulado >= 0 else ""

    if mg_ativo:
        ciclos_total = ciclos_c1 + ciclos_c2 + ciclos_c3
        pct_c1 = (ciclos_c1 / ciclos_total * 100) if ciclos_total > 0 else 0.0
        pct_c2 = (ciclos_c2 / ciclos_total * 100) if ciclos_total > 0 else 0.0
        pct_c3 = (ciclos_c3 / ciclos_total * 100) if ciclos_total > 0 else 0.0
        lucro_por_ciclo = (lucro_simulado / ciclos_total) if ciclos_total > 0 else 0.0
        lucro_por_sinal = 0.0
        sinal_por = "+" if lucro_por_ciclo >= 0 else ""
        if pct_c3 < 15 and lucro_simulado > 0:
            avaliacao = "EXCELENTE (C3<15%, lucro positivo)"
            aval_short = "E"
        elif pct_c3 < 20 and lucro_simulado > 0:
            avaliacao = "BOM (C3<20%, lucro positivo)"
            aval_short = "B"
        elif pct_c3 < 25 and lucro_simulado >= 0:
            avaliacao = "NEUTRO (C3<25%)"
            aval_short = "N"
        else:
            avaliacao = "FRACO (C3>=25% ou prejuizo)"
            aval_short = "F"
    else:
        ciclos_total = 0
        pct_c1 = pct_c2 = pct_c3 = 0.0
        max_seq_c3 = 0
        lucro_por_ciclo = 0.0
        lucro_por_sinal = (lucro_simulado / total_sinais) if total_sinais > 0 else 0.0
        sinal_por = "+" if lucro_por_sinal >= 0 else ""
        if taxa_acerto >= 60:
            avaliacao = "EXCELENTE (>=60% acerto)"
            aval_short = "E"
        elif taxa_acerto >= 55:
            avaliacao = "BOM (>=55% acerto)"
            aval_short = "B"
        elif taxa_acerto >= 50:
            avaliacao = "NEUTRO (50-54% acerto)"
            aval_short = "N"
        else:
            avaliacao = "FRACO (<50% acerto)"
            aval_short = "F"

    return {
        "wins": wins, "losses": losses, "dojis": dojis,
        "total_sinais": total_sinais, "total_candles": total_candles,
        "taxa_acerto": taxa_acerto,
        "pct_sinal": pct_sinal, "pct_w": pct_w, "pct_l": pct_l, "pct_d": pct_d,
        "lucro_simulado": lucro_simulado, "sinal_lucro": sinal_lucro,
        "ciclos_total": ciclos_total,
        "ciclos_c1": ciclos_c1, "ciclos_c2": ciclos_c2, "ciclos_c3": ciclos_c3,
        "pct_c1": pct_c1, "pct_c2": pct_c2, "pct_c3": pct_c3,
        "max_seq_c3": max_seq_c3,
        "lucro_por_ciclo": lucro_por_ciclo, "lucro_por_sinal": lucro_por_sinal,
        "sinal_por": sinal_por,
        "max_seq_win": max_seq_win, "max_seq_loss": max_seq_loss,
        "freq": freq, "periodo_h": periodo_h, "tf_label": tf_label,
        "avaliacao": avaliacao, "aval_short": aval_short,
    }


async def _backteste_async():
    """Backteste walk-forward: busca candles historicos e simula acuracia do sinal."""
    from estrategias import ESTRATEGIAS, ESTRATEGIAS_META, executar_estrategia

    cfg = carregar_config()

    print("\n  [BACKTESTE] Modo: PRACTICE (sem trades reais)")

    # --- Conectar ---
    print("  [QUOTEX] Conectando...")
    try:
        quotex = AgentQuotex(account_mode="PRACTICE")
    except Exception as e:
        print(f"  [ERRO] {e}")
        return

    if not await _conectar_com_retry(quotex, cfg):
        return

    # --- Selecionar ativo ---
    print("  Carregando ativos disponiveis...")
    ativos = _listar_ativos_quotex(quotex, cfg)

    if not ativos:
        print(f"  [AVISO] Nenhum ativo aberto com os filtros atuais.")
        await quotex.desconectar()
        return

    print(f"\n  Ativos disponiveis ({len(ativos)}) - ordenados por payout:\n")
    print(f"  {'N':>4}  {'Par de Ativo':<24} {'Payout':>7}  {'Mercado'}")
    print(f"  {'-'*4}  {'-'*24} {'-'*7}  {'-'*12}")
    for i, a in enumerate(ativos, 1):
        print(f"  {i:>4}. {a['display']:<24} {a['payout_pct']:>6}%  {a['mercado']}")
    print()

    v, cancel = _inp("Numero do ativo", "1")
    if cancel:
        await quotex.desconectar()
        return
    try:
        idx = int(v) - 1
        if not (0 <= idx < len(ativos)):
            raise ValueError()
        ativo_sel = ativos[idx]
        asset = ativo_sel["interno"]
        payout_pct = ativo_sel["payout_pct"]
        payout = payout_pct / 100.0
        print(f"  -> {ativo_sel['display']} | Payout: {payout_pct}%\n")
    except ValueError:
        print("  [ERRO] Numero invalido.")
        await quotex.desconectar()
        return

    # --- Selecionar estrategia ---
    nomes_est = list(ESTRATEGIAS.keys())
    est_atual = cfg.get("estrategia_ativa", "EMA_RSI")
    print(f"  Estrategias disponiveis:\n")
    for i, nome in enumerate(nomes_est, 1):
        meta = ESTRATEGIAS_META.get(nome, {})
        desc = meta.get("descricao", "")
        tf = meta.get("timeframe_rec")
        tf_str = f"  [rec: M{tf//60 if tf and tf < 3600 else (tf//3600 if tf else 0)}]" if tf else ""
        marca = "  <- atual" if nome == est_atual else ""
        print(f"    {i} - {nome}{tf_str}{marca}")
        if desc:
            print(f"        {desc}")
    print()

    idx_default = nomes_est.index(est_atual) + 1 if est_atual in nomes_est else 1
    v, cancel = _inp("Numero da estrategia", str(idx_default))
    if cancel:
        await quotex.desconectar()
        return
    try:
        estrategia_nome = nomes_est[int(v) - 1]
    except (ValueError, IndexError):
        print("  [ERRO] Numero invalido.")
        await quotex.desconectar()
        return

    meta_sel = ESTRATEGIAS_META.get(estrategia_nome, {})
    tf_rec = meta_sel.get("timeframe_rec")
    if tf_rec:
        tf_label = f"M{tf_rec // 60}" if tf_rec < 3600 else f"H{tf_rec // 3600}"
        print(f"  -> Estrategia: {estrategia_nome}  [timeframe recomendado: {tf_label} = {tf_rec}s]\n")
    else:
        print(f"  -> Estrategia: {estrategia_nome}\n")

    # --- Parametros ---
    dur_default = tf_rec if tf_rec else cfg["duracao_padrao"]
    v, cancel = _inp("Duracao do candle (seg)", dur_default)
    if cancel:
        await quotex.desconectar()
        return
    duracao = int(v)

    v, cancel = _inp("Entrada simulada R$", cfg["entrada_padrao"])
    if cancel:
        await quotex.desconectar()
        return
    entrada = float(v)

    # --- Simular MG? ---
    v, cancel = _inp("Simular Martingale? (s/N)", "n")
    if cancel:
        await quotex.desconectar()
        return
    mg_ativo = v.strip().lower() == "s"
    mg_niveis = 1
    if mg_ativo:
        v, cancel = _inp("Niveis de MG", cfg.get("niveis_mg", 3))
        if cancel:
            await quotex.desconectar()
            return
        mg_niveis = max(1, int(v))

    # --- Buscar candles historicos ---
    num_candles = 1000
    print(f"\n  Buscando ~{num_candles} candles historicos ({estrategia_nome} | {ativo_sel['display']} | {duracao}s)...")
    try:
        candles = await asyncio.wait_for(
            quotex.get_candles(asset, period=duracao, offset=duracao * num_candles),
            timeout=60,
        )
    except asyncio.TimeoutError:
        print("  [TIMEOUT] get_candles nao respondeu em 60s.")
        await quotex.desconectar()
        return
    except Exception as e:
        print(f"  [ERRO] {type(e).__name__}: {e}")
        await quotex.desconectar()
        return

    if not candles or len(candles) < 10:
        print(f"  [AVISO] Poucos candles retornados ({len(candles) if candles else 0}). Tente novamente.")
        await quotex.desconectar()
        return

    await quotex.desconectar()

    total_candles = len(candles)
    print(f"  [OK] {total_candles} candles recebidos. Simulando...")

    m = _simular_walk_forward(candles, estrategia_nome, cfg, payout, entrada, mg_ativo, mg_niveis, duracao)

    wins            = m["wins"]
    losses          = m["losses"]
    dojis           = m["dojis"]
    total_sinais    = m["total_sinais"]
    taxa_acerto     = m["taxa_acerto"]
    pct_sinal       = m["pct_sinal"]
    pct_w           = m["pct_w"]
    pct_l           = m["pct_l"]
    pct_d           = m["pct_d"]
    lucro_simulado  = m["lucro_simulado"]
    sinal_lucro     = m["sinal_lucro"]
    ciclos_total    = m["ciclos_total"]
    ciclos_c1       = m["ciclos_c1"]
    ciclos_c2       = m["ciclos_c2"]
    ciclos_c3       = m["ciclos_c3"]
    pct_c1          = m["pct_c1"]
    pct_c2          = m["pct_c2"]
    pct_c3          = m["pct_c3"]
    max_seq_c3      = m["max_seq_c3"]
    lucro_por_ciclo = m["lucro_por_ciclo"]
    lucro_por_sinal = m["lucro_por_sinal"]
    sinal_por       = m["sinal_por"]
    max_seq_win     = m["max_seq_win"]
    max_seq_loss    = m["max_seq_loss"]
    freq            = m["freq"]
    periodo_h       = m["periodo_h"]
    tf_label        = m["tf_label"]
    avaliacao       = m["avaliacao"]

    # --- Exibir resultados ---
    W = 53
    SEP  = "  +" + "=" * (W - 2) + "+"
    SEP2 = "  +" + "-" * (W - 2) + "+"

    def _lrow(label: str, valor: str):
        linha = f"  {label} {valor}"
        print(f"  | {linha:<{W-4}} |")

    mg_label = f" + MG {mg_niveis}x" if mg_ativo else ""
    print()
    print(SEP)
    titulo = f"BACKTESTE: {estrategia_nome}{mg_label}"
    print(f"  | {titulo:<{W-4}} |")
    subtitulo = f"{ativo_sel['display']} | {tf_label} ({duracao}s) | R${entrada:.2f} | Payout: {payout_pct}%"
    print(f"  | {subtitulo:<{W-4}} |")
    print(SEP)
    _lrow("Candles analisados: ", str(total_candles))
    _lrow("Sinais gerados:     ", f"{total_sinais}    ({pct_sinal:.1f}% dos candles)")
    print(SEP2)
    _lrow("Wins:               ", f"{wins}   ({pct_w:.1f}%)")
    _lrow("Losses:             ", f"{losses}   ({pct_l:.1f}%)")
    _lrow("Dojis:              ", f"{dojis}   ({pct_d:.1f}%)")
    _lrow("Taxa de acerto:     ", f"{taxa_acerto:.1f}%")
    if mg_ativo:
        print(SEP2)
        _lrow("CICLOS MG:          ", f"{mg_niveis} niveis | {ciclos_total} ciclos")
        _lrow("C1 win direto:      ", f"{ciclos_c1}   ({pct_c1:.1f}%)")
        _lrow("C2 recuperado MG:   ", f"{ciclos_c2}   ({pct_c2:.1f}%)")
        _lrow("C3 perda total:     ", f"{ciclos_c3}   ({pct_c3:.1f}%)")
        _lrow("Max C3 consecutivos:", str(max_seq_c3))
    print(SEP2)
    _lrow("Lucro simulado:     ", f"R$ {sinal_lucro}{lucro_simulado:.2f}")
    if mg_ativo:
        _lrow("Lucro por ciclo:    ", f"R$ {sinal_por}{lucro_por_ciclo:.2f} media")
    else:
        _lrow("Lucro por sinal:    ", f"R$ {sinal_por}{lucro_por_sinal:.2f} media")
    print(SEP2)
    _lrow("Max sequencia WIN:  ", str(max_seq_win))
    _lrow("Max sequencia LOSS: ", str(max_seq_loss))
    print(SEP2)
    _lrow("Frequencia de sinal:", f"1 a cada {freq:.1f} candles")
    _lrow("Periodo analisado:  ", f"~{periodo_h:.0f} horas ({tf_label} x {total_candles})")
    print(SEP)
    _lrow("AVALIACAO:", avaliacao)
    print(SEP)
    print()

    log_s("INFO", f"Backteste{mg_label} {estrategia_nome} | {ativo_sel['display']} | {tf_label} | "
          f"sinais={total_sinais} wins={wins} losses={losses} acerto={taxa_acerto:.1f}% "
          f"lucro=R${lucro_simulado:.2f}"
          + (f" | C1={ciclos_c1} C2={ciclos_c2} C3={ciclos_c3}" if mg_ativo else ""))

    # --- Salvar em arquivo (opcional) ---
    v, cancel = _inp("Salvar resultado em arquivo? (s/N)", "n")
    if not cancel and v.strip().lower() == "s":
        mg_suffix = f"_MG{mg_niveis}" if mg_ativo else ""
        nome_arquivo = f"backteste_{estrategia_nome}{mg_suffix}_{ativo_sel['display'].replace('/', '_').replace(' ', '_')}.txt"
        arq_path = Path(__file__).resolve().parent / "logs" / nome_arquivo
        try:
            with open(arq_path, "w", encoding="utf-8") as f:
                f.write(f"BACKTESTE: {estrategia_nome}{mg_label}\n")
                f.write(f"Ativo:    {ativo_sel['display']}\n")
                f.write(f"TF:       {tf_label} ({duracao}s)\n")
                f.write(f"Entrada:  R${entrada:.2f}\n")
                f.write(f"Payout:   {payout_pct}%\n")
                f.write(f"Data:     {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                f.write("-" * 40 + "\n")
                f.write(f"Candles:  {total_candles}\n")
                f.write(f"Sinais:   {total_sinais} ({pct_sinal:.1f}%)\n")
                f.write(f"Wins:     {wins} ({pct_w:.1f}%)\n")
                f.write(f"Losses:   {losses} ({pct_l:.1f}%)\n")
                f.write(f"Dojis:    {dojis} ({pct_d:.1f}%)\n")
                f.write(f"Acerto:   {taxa_acerto:.1f}%\n")
                if mg_ativo:
                    f.write(f"MG:       {mg_niveis} niveis\n")
                    f.write(f"C1:       {ciclos_c1} ({pct_c1:.1f}%)\n")
                    f.write(f"C2:       {ciclos_c2} ({pct_c2:.1f}%)\n")
                    f.write(f"C3:       {ciclos_c3} ({pct_c3:.1f}%)\n")
                    f.write(f"MaxC3:    {max_seq_c3}\n")
                    f.write(f"Lucro:    R$ {sinal_lucro}{lucro_simulado:.2f}\n")
                    f.write(f"L/ciclo:  R$ {sinal_por}{lucro_por_ciclo:.2f}\n")
                else:
                    f.write(f"Lucro:    R$ {sinal_lucro}{lucro_simulado:.2f}\n")
                    f.write(f"L/sinal:  R$ {sinal_por}{lucro_por_sinal:.2f}\n")
                f.write(f"MaxWin:   {max_seq_win}\n")
                f.write(f"MaxLoss:  {max_seq_loss}\n")
                f.write(f"Periodo:  ~{periodo_h:.0f}h\n")
                f.write(f"Avaliacao:{avaliacao}\n")
            print(f"  [OK] Salvo em {nome_arquivo}\n")
        except Exception as e:
            print(f"  [ERRO] Nao foi possivel salvar: {e}\n")


async def _backteste_ranking_async():
    """Backteste ranking: testa top N ativos com a estrategia selecionada e exibe ranking."""
    from estrategias import ESTRATEGIAS, ESTRATEGIAS_META

    cfg = carregar_config()
    print("\n  [BACKTESTE RANKING] Modo: PRACTICE (sem trades reais)")

    # --- Conectar ---
    print("  [QUOTEX] Conectando...")
    try:
        quotex = AgentQuotex(account_mode="PRACTICE")
    except Exception as e:
        print(f"  [ERRO] {e}")
        return

    if not await _conectar_com_retry(quotex, cfg):
        return

    # --- Selecionar estrategia ---
    nomes_est = list(ESTRATEGIAS.keys())
    est_atual = cfg.get("estrategia_ativa", "EMA_RSI")
    print(f"\n  Estrategias disponiveis:\n")
    for i, nome in enumerate(nomes_est, 1):
        meta = ESTRATEGIAS_META.get(nome, {})
        desc = meta.get("descricao", "")
        tf = meta.get("timeframe_rec")
        tf_str = f"  [rec: M{tf//60 if tf and tf < 3600 else (tf//3600 if tf else 0)}]" if tf else ""
        marca = "  <- atual" if nome == est_atual else ""
        print(f"    {i} - {nome}{tf_str}{marca}")
        if desc:
            print(f"        {desc}")
    print()

    idx_default = nomes_est.index(est_atual) + 1 if est_atual in nomes_est else 1
    v, cancel = _inp("Numero da estrategia", str(idx_default))
    if cancel:
        await quotex.desconectar()
        return
    try:
        estrategia_nome = nomes_est[int(v) - 1]
    except (ValueError, IndexError):
        print("  [ERRO] Numero invalido.")
        await quotex.desconectar()
        return

    meta_sel = ESTRATEGIAS_META.get(estrategia_nome, {})
    tf_rec = meta_sel.get("timeframe_rec")
    if tf_rec:
        tf_label_rec = f"M{tf_rec // 60}" if tf_rec < 3600 else f"H{tf_rec // 3600}"
        print(f"  -> Estrategia: {estrategia_nome}  [timeframe recomendado: {tf_label_rec} = {tf_rec}s]\n")
    else:
        print(f"  -> Estrategia: {estrategia_nome}\n")

    # --- Parametros ---
    dur_default = tf_rec if tf_rec else cfg["duracao_padrao"]
    v, cancel = _inp("Duracao do candle (seg)", dur_default)
    if cancel:
        await quotex.desconectar()
        return
    duracao = int(v)

    v, cancel = _inp("Entrada simulada R$", cfg["entrada_padrao"])
    if cancel:
        await quotex.desconectar()
        return
    entrada = float(v)

    v, cancel = _inp("Simular Martingale? (s/N)", "n")
    if cancel:
        await quotex.desconectar()
        return
    mg_ativo = v.strip().lower() == "s"
    mg_niveis = 1
    if mg_ativo:
        v, cancel = _inp("Niveis de MG", cfg.get("niveis_mg", 3))
        if cancel:
            await quotex.desconectar()
            return
        mg_niveis = max(1, int(v))

    v, cancel = _inp("Quantos ativos testar (top N por payout)", "10")
    if cancel:
        await quotex.desconectar()
        return
    try:
        n_ativos = max(1, int(v))
    except ValueError:
        n_ativos = 10

    # --- Listar top N ativos por payout ---
    print("  Carregando ativos disponiveis...")
    ativos = _listar_ativos_quotex(quotex, cfg)
    if not ativos:
        print("  [AVISO] Nenhum ativo aberto com os filtros atuais.")
        await quotex.desconectar()
        return

    ativos_sel = ativos[:n_ativos]
    mg_label = f" + MG {mg_niveis}x" if mg_ativo else ""
    tf_label = f"M{duracao // 60}" if duracao < 3600 else f"H{duracao // 3600}"
    print(f"\n  Testando {len(ativos_sel)} ativos | {estrategia_nome}{mg_label} | {tf_label} | R${entrada:.2f}\n")

    # --- Testar cada ativo ---
    num_candles = 1000
    ranking = []
    for idx, ativo in enumerate(ativos_sel, 1):
        asset = ativo["interno"]
        payout_pct = ativo["payout_pct"]
        payout = payout_pct / 100.0
        print(f"  [{idx}/{len(ativos_sel)}] {ativo['display']:<24} ", end="", flush=True)
        try:
            candles = await asyncio.wait_for(
                quotex.get_candles(asset, period=duracao, offset=duracao * num_candles),
                timeout=60,
            )
        except asyncio.TimeoutError:
            print("TIMEOUT — ignorado")
            continue
        except Exception as e:
            print(f"ERRO ({type(e).__name__}) — ignorado")
            continue

        if not candles or len(candles) < 10:
            print(f"poucos candles ({len(candles) if candles else 0}) — ignorado")
            continue

        m = _simular_walk_forward(candles, estrategia_nome, cfg, payout, entrada, mg_ativo, mg_niveis, duracao)
        print(f"OK  acerto={m['taxa_acerto']:.1f}%  lucro=R${m['lucro_simulado']:+.2f}  [{m['aval_short']}]")
        ranking.append({"ativo": ativo, "payout_pct": payout_pct, **m})

    await quotex.desconectar()

    if not ranking:
        print("\n  [AVISO] Nenhum ativo gerou resultado valido.")
        return

    # --- Ordenar por lucro simulado ---
    ranking.sort(key=lambda r: r["lucro_simulado"], reverse=True)

    # --- Exibir tabela de ranking ---
    W = 74
    SEP  = "  +" + "=" * (W - 2) + "+"
    SEP2 = "  +" + "-" * (W - 2) + "+"

    def _rrow(linha: str):
        print(f"  | {linha:<{W-4}} |")

    print()
    print(SEP)
    _rrow(f"RANKING: {estrategia_nome}{mg_label} | {tf_label} ({duracao}s) | R${entrada:.2f}")
    _rrow(f"Top {len(ativos_sel)} ativos por payout — ordenado por lucro simulado")
    print(SEP)
    _rrow(f"  {'#':>3}  {'Ativo':<24} {'Payout':>7}  {'Acerto':>7}  {'C3%':>5}  {'Lucro':>10}  Aval")
    print(SEP2)

    for pos, r in enumerate(ranking, 1):
        sinal_l = "+" if r["lucro_simulado"] >= 0 else ""
        c3_str = f"{r['pct_c3']:.1f}%" if mg_ativo else "  -  "
        linha = (
            f"  {pos:>3}. {r['ativo']['display']:<24} "
            f"{r['payout_pct']:>6}%  "
            f"{r['taxa_acerto']:>6.1f}%  "
            f"{c3_str:>5}  "
            f"R${sinal_l}{r['lucro_simulado']:>8.2f}  "
            f"({r['aval_short']})"
        )
        _rrow(linha)

    print(SEP)
    melhor = ranking[0]
    sinal_m = "+" if melhor["lucro_simulado"] >= 0 else ""
    _rrow(f"Melhor: {melhor['ativo']['display']} — {melhor['avaliacao']}")
    print(SEP)
    print()

    log_s("INFO", f"Ranking {estrategia_nome}{mg_label} | {tf_label} | {len(ranking)} ativos | "
          f"melhor={melhor['ativo']['display']} lucro=R${sinal_m}{melhor['lucro_simulado']:.2f}")

    # --- Salvar em arquivo (opcional) ---
    v, cancel = _inp("Salvar ranking em arquivo? (s/N)", "n")
    if not cancel and v.strip().lower() == "s":
        mg_suffix = f"_MG{mg_niveis}" if mg_ativo else ""
        nome_arquivo = f"ranking_{estrategia_nome}{mg_suffix}_{tf_label}.txt"
        arq_path = Path(__file__).resolve().parent / "logs" / nome_arquivo
        try:
            with open(arq_path, "w", encoding="utf-8") as f:
                f.write(f"RANKING: {estrategia_nome}{mg_label}\n")
                f.write(f"TF:      {tf_label} ({duracao}s)\n")
                f.write(f"Entrada: R${entrada:.2f}\n")
                f.write(f"Data:    {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
                f.write("-" * 65 + "\n")
                f.write(f"{'#':>3}  {'Ativo':<24} {'Payout':>7}  {'Acerto':>7}  {'C3%':>5}  {'Lucro':>10}  Aval\n")
                f.write("-" * 65 + "\n")
                for pos, r in enumerate(ranking, 1):
                    sinal_l = "+" if r["lucro_simulado"] >= 0 else ""
                    c3_str = f"{r['pct_c3']:.1f}%" if mg_ativo else "  -  "
                    f.write(
                        f"{pos:>3}. {r['ativo']['display']:<24} "
                        f"{r['payout_pct']:>6}%  "
                        f"{r['taxa_acerto']:>6.1f}%  "
                        f"{c3_str:>5}  "
                        f"R${sinal_l}{r['lucro_simulado']:>8.2f}  "
                        f"({r['aval_short']})\n"
                    )
            print(f"  [OK] Salvo em {nome_arquivo}\n")
        except Exception as e:
            print(f"  [ERRO] Nao foi possivel salvar: {e}\n")

    # --- Iniciar loop autonomo com ativo do ranking? ---
    v, cancel = _inp("Iniciar loop autonomo com ativo do ranking? (s/N)", "n")
    if cancel or v.strip().lower() != "s":
        return None

    print(f"\n  Ativos do ranking (ordenados por lucro simulado):\n")
    for pos, r in enumerate(ranking, 1):
        sinal_l = "+" if r["lucro_simulado"] >= 0 else ""
        print(f"  {pos:>3}. {r['ativo']['display']:<24} Lucro: R${sinal_l}{r['lucro_simulado']:.2f}  ({r['aval_short']})")
    print()

    v, cancel = _inp(f"Numero do ativo (1-{len(ranking)})", "1")
    if cancel:
        return None
    try:
        idx_sel = int(v) - 1
        if not (0 <= idx_sel < len(ranking)):
            raise ValueError()
    except ValueError:
        print("  [ERRO] Numero invalido.")
        return None

    r_sel = ranking[idx_sel]
    preset = {
        "asset":          r_sel["ativo"]["interno"],
        "asset_display":  r_sel["ativo"]["display"],
        "payout_pct":     r_sel["payout_pct"],
        "estrategia_nome": estrategia_nome,
        "duracao":        duracao,
        "entrada":        entrada,
        "niveis_mg":      mg_niveis,
    }
    print(f"\n  -> {r_sel['ativo']['display']} | {estrategia_nome} | {tf_label} | R${entrada:.2f} | MG {mg_niveis}x")
    log_s("INFO", f"Ranking -> Autonomo: {preset['asset']} | {estrategia_nome} | {tf_label}")
    return preset
