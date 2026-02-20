"""
Main - Sistema completo: SIMULACAO | QUOTEX (real/demo) | MANUAL.

Modos:
  sim     - Loop automatico com simulacao local (sem API)
  quotex  - Loop automatico conectado na Quotex via websocket
  operar  - Operacao manual unica
"""

import sys
import time
import signal
import socket
import threading
import asyncio
import json
from pathlib import Path
from datetime import datetime

# --- Encoding: garante UTF-8 em qualquer terminal Windows (CMD, PowerShell, etc.) ---
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --- Verificador de conexao com a internet ---
def _verificar_internet(host: str = "8.8.8.8", port: int = 53, timeout: int = 3) -> tuple[bool, float]:
    """Testa conexao TCP com o DNS publico do Google (8.8.8.8:53).

    Nao envia dados — apenas verifica se a rede responde.
    Retorna (conectado: bool, latencia_ms: float).
    """
    try:
        inicio = time.monotonic()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        ms = (time.monotonic() - inicio) * 1000
        return True, ms
    except OSError:
        return False, 0.0


# --- Validacao de ambiente: Python, .env e internet ---
def _validar_ambiente():
    """Verifica requisitos minimos antes de iniciar. Aborta com mensagem clara se falhar."""
    erros = []

    # Python >= 3.12
    if sys.version_info < (3, 12):
        erros.append(
            f"Python 3.12+ necessario. Versao atual: {sys.version_info.major}.{sys.version_info.minor}\n"
            f"  Baixe em: https://www.python.org/downloads/"
        )

    # Arquivo .env
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        erros.append(
            ".env nao encontrado.\n"
            "  Copie o arquivo .env.example para .env e preencha suas credenciais.\n"
            f"  Local esperado: {env_path}"
        )
    else:
        # Verifica variaveis obrigatorias
        try:
            from dotenv import dotenv_values
            env = dotenv_values(env_path)
        except ImportError:
            env = {}

        obrigatorias = ["ANTHROPIC_API_KEY", "QUOTEX_EMAIL", "QUOTEX_PASSWORD", "QUOTEX_ACCOUNT_MODE"]
        faltando = [v for v in obrigatorias if not env.get(v) or env[v].startswith("COLOQUE_")]
        if faltando:
            erros.append(
                "Variaveis obrigatorias nao configuradas no .env:\n" +
                "".join(f"  - {v}\n" for v in faltando) +
                "  Consulte o arquivo .env.example para instrucoes."
            )

    # Conexao com a internet
    print("  [REDE] Testando conexao com a internet...", end=" ", flush=True)
    ok, ms = _verificar_internet()
    if ok:
        print(f"OK ({ms:.0f}ms)")
    else:
        print("FALHOU")
        erros.append(
            "Sem conexao com a internet (timeout 3s).\n"
            "  Verifique sua rede antes de iniciar o sistema."
        )

    if erros:
        print("\n" + "=" * 60)
        print("  [ERRO] Ambiente nao configurado corretamente:")
        print("=" * 60)
        for e in erros:
            print(f"\n  {e}")
        print("\n" + "=" * 60 + "\n")
        sys.exit(1)

_validar_ambiente()

from agents import AgentGerenciador, AgentAnalisador, AgentProtetor, AgentQuotex, AgentTelegram
from skills import (
    skill_execute_operation, skill_calculate_mg, skill_register_operation,
    skill_read_balance, skill_check_protection, skill_log_alert,
)

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

# ============================================================
# SHUTDOWN GRACIOSO (Ctrl+C durante trade ativo)
# ============================================================

_shutdown_gracioso = threading.Event()


def _instalar_shutdown_gracioso():
    """Instala handler de Ctrl+C que aguarda a operacao atual terminar.

    - 1o Ctrl+C: seta flag, operacao atual completa e registra normalmente
    - 2o Ctrl+C: saida imediata (comportamento padrao)

    Retorna o handler original para restaurar depois.
    """
    _shutdown_gracioso.clear()
    handler_original = signal.getsignal(signal.SIGINT)

    def _handler(*_):
        if _shutdown_gracioso.is_set():
            # Segundo Ctrl+C: saida imediata
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt
        print("\n\n  [!] Ctrl+C recebido. Aguardando operacao atual finalizar...")
        print("      Pressione Ctrl+C novamente para saida imediata.\n")
        _shutdown_gracioso.set()

    signal.signal(signal.SIGINT, _handler)
    return handler_original


def _restaurar_shutdown(handler_original):
    """Restaura handler original de Ctrl+C e limpa a flag."""
    signal.signal(signal.SIGINT, handler_original)
    _shutdown_gracioso.clear()


async def _sleep_cancelavel(segundos: int):
    """Sleep em blocos de 1s para checar flag de shutdown rapidamente."""
    for _ in range(segundos):
        if _shutdown_gracioso.is_set():
            return
        await asyncio.sleep(1)

_CONFIG_DEFAULTS = {
    # Conta
    "account_mode": "PRACTICE",
    # Operacao
    "entrada_padrao": 10.0,
    "duracao_padrao": 300,
    "niveis_mg": 3,
    "fator_correcao_mg": False,
    "estrategia_ativa": "NENHUMA",
    # Filtros
    "tipo_ativo": "AMBOS",
    "tipo_mercado": "AMBOS",
    "payout_minimo_pct": 75,
    "payout_minimo_pct_telegram": 75,
    # Protecao
    "max_loss_streak": 5,
    "max_ops_sessao": 50,
    "stop_loss_pct": 20.0,
    "stop_loss_reais": None,
    "take_profit_reais": None,
    "saldo_inicial": 1000.0,
    # Conexao
    "janela_execucao_seg": 5,
    "intervalo_verificacao_seg": 3,
    "timeout_operacao_seg": 1800,
    "timeout_resultado_seg": 900,
    "timeout_conexao_seg": 30,
    "tentativas_reconexao": 3,
}


def carregar_config() -> dict:
    """Le config.json. Retorna defaults se arquivo nao existir."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in _CONFIG_DEFAULTS.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception:
        return dict(_CONFIG_DEFAULTS)


def salvar_config(cfg: dict):
    """Salva config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def comando_config():
    """Menu interativo para editar configuracoes de gerenciamento de risco."""
    cfg = carregar_config()

    campos = [
        # CONTA
        ("account_mode",               "Modo da conta Quotex",         "choice:PRACTICE:REAL"),
        # OPERACAO
        ("entrada_padrao",             "Entrada padrao (R$)",          "float"),
        ("duracao_padrao",             "Time Frame padrao",            "choice_int:60:300:900:1800:3600"),
        ("niveis_mg",                  "Niveis MG",                    "int"),
        ("fator_correcao_mg",          "Fator correcao MG",            "bool"),
        # FILTROS
        ("tipo_ativo",                 "Tipo ativo (OTC/Normal)",      "choice:OTC:NAO_OTC:AMBOS"),
        ("tipo_mercado",               "Tipo de mercado",              "choice:FOREX:CRIPTO:MATERIA_PRIMA:ACAO:AMBOS"),
        ("payout_minimo_pct",          "Payout minimo - Quotex (%)",   "float"),
        ("payout_minimo_pct_telegram", "Payout minimo - Telegram (%)", "float"),
        # PROTECAO
        ("max_loss_streak",            "Max loss streak",              "int"),
        ("max_ops_sessao",             "Max ops/sessao (0=ilimit)",    "int_null"),
        ("stop_loss_pct",              "Stop Loss (%)",                "float"),
        ("stop_loss_reais",            "Stop Loss R$ (0=desativ)",     "float_null"),
        ("take_profit_reais",          "Take Profit R$ (0=desativ)",   "float_null"),
        ("saldo_inicial",              "Saldo inicial (R$)",           "float"),
        # CONEXAO
        ("janela_execucao_seg",        "Janela de execucao (+-seg)",   "int"),
        ("intervalo_verificacao_seg",  "Intervalo de verificacao (seg)","int"),
        ("timeout_resultado_seg",      "Timeout de resultado (seg)",   "int"),
        ("timeout_operacao_seg",       "Timeout de operacao (seg)",    "int"),
        ("timeout_conexao_seg",        "Timeout de conexao (seg)",     "int"),
        ("tentativas_reconexao",       "Tentativas de reconexao",      "int"),
    ]

    while True:
        print()
        print("  +--------------------------------------------------+")
        print("  |        GERENCIAMENTO DE RISCO - CONFIG           |")
        print("  +--------------------------------------------------+")

        for i, (key, label, _) in enumerate(campos, 1):
            val = cfg[key]
            if val is None:
                val_str = "OFF"
            elif val is True:
                val_str = "ATIVADO"
            elif val is False:
                val_str = "DESATIVADO"
            else:
                val_str = str(val)
            print(f"  |  {i:2}. {label:<28} {val_str:<12}|")

        print("  +--------------------------------------------------+")
        print("  |  Digite numero p/ editar | s = salvar | x = sair |")
        print("  +--------------------------------------------------+")

        entrada = input("\n  Opcao: ").strip().lower()

        if entrada == "x":
            print("  Cancelado. Config nao alterado.\n")
            break

        if entrada == "s":
            salvar_config(cfg)
            print("  [OK] Config salvo em config.json\n")
            break

        if entrada.isdigit():
            idx = int(entrada) - 1
            if 0 <= idx < len(campos):
                key, label, tipo = campos[idx]
                val_atual = cfg[key]
                if val_atual is None:
                    val_str = "OFF"
                elif val_atual is True:
                    val_str = "ATIVADO"
                elif val_atual is False:
                    val_str = "DESATIVADO"
                else:
                    val_str = str(val_atual)

                if tipo == "bool":
                    print(f"  {label} [{val_str}]")
                    print(f"    1 - ATIVADO")
                    print(f"    2 - DESATIVADO")
                    novo = input("  Escolha: ").strip()
                    if novo == "1":
                        cfg[key] = True
                        print(f"  -> {label} = ATIVADO")
                    elif novo == "2":
                        cfg[key] = False
                        print(f"  -> {label} = DESATIVADO")
                    else:
                        print(f"  [ERRO] Opcao invalida")
                    continue

                if tipo.startswith("choice_int:"):
                    _DUR_LABELS = {
                        "60": "M1  - 1 min",  "300": "M5  - 5 min",
                        "900": "M15 - 15 min", "1800": "M30 - 30 min",
                        "3600": "H1  - 1 hora",
                    }
                    opcoes = tipo.split(":")[1:]
                    print(f"  {label} [{val_str}]")
                    for i, op in enumerate(opcoes, 1):
                        lbl = _DUR_LABELS.get(op, "")
                        print(f"    {i} - {op}s  {lbl}")
                    novo = input("  Escolha: ").strip()
                    if novo.isdigit() and 1 <= int(novo) <= len(opcoes):
                        cfg[key] = int(opcoes[int(novo) - 1])
                        print(f"  -> {label} = {cfg[key]}")
                    else:
                        print(f"  [ERRO] Opcao invalida")
                    continue

                if tipo.startswith("choice:"):
                    opcoes = tipo.split(":")[1:]
                    print(f"  {label} [{val_str}]")
                    for i, op in enumerate(opcoes, 1):
                        print(f"    {i} - {op}")
                    novo = input("  Escolha: ").strip()
                    if novo.isdigit() and 1 <= int(novo) <= len(opcoes):
                        cfg[key] = opcoes[int(novo) - 1]
                        print(f"  -> {label} = {cfg[key]}")
                    else:
                        print(f"  [ERRO] Opcao invalida")
                    continue

                novo = input(f"  {label} [{val_str}]: ").strip()
                if not novo:
                    continue
                try:
                    if tipo == "float":
                        cfg[key] = float(novo)
                    elif tipo == "int":
                        cfg[key] = int(novo)
                    elif tipo == "float_null":
                        v = float(novo)
                        cfg[key] = None if v == 0 else v
                    elif tipo == "int_null":
                        v = int(novo)
                        cfg[key] = None if v == 0 else v
                    print(f"  -> {label} = {cfg[key]}")
                except ValueError:
                    print(f"  [ERRO] Valor invalido: '{novo}'")


# ============================================================
# HELPER: input com suporte a cancelamento
# ============================================================

def _inp(prompt: str, default=None):
    """Input com suporte a cancelamento (0 ou 'voltar').

    Retorna (valor, cancelado). Se cancelado=True, o loop deve retornar.
    """
    hint = f" [{default}]" if default is not None else ""
    raw = input(f"  {prompt}{hint} (0=voltar): ").strip()
    if raw in ("0", "voltar", "menu"):
        return None, True
    if raw == "" and default is not None:
        return default, False
    return raw, False


# ============================================================
# LOOP SIMULACAO LOCAL (sem API nenhuma)
# ============================================================

def loop_simulacao(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop automatico com simulacao local. Sem Claude, sem Quotex."""
    cfg = carregar_config()

    print("\n  [SIMULACAO] Configuracao (Enter = valor padrao, 0 = voltar):")

    asset, cancel = _inp("Asset", "EURUSD")
    if cancel: return
    direction, cancel = _inp("Direcao call/put", "call")
    if cancel: return
    direction = direction.lower()

    v, cancel = _inp(f"Entrada R$", cfg["entrada_padrao"])
    if cancel: return
    valor = float(v)

    v, cancel = _inp("Max sequencias", cfg["max_ops_sessao"])
    if cancel: return
    max_ops = int(v)

    v, cancel = _inp("Niveis MG", cfg["niveis_mg"])
    if cancel: return
    niveis = int(v)

    v, cancel = _inp("Intervalo entre ops (seg)", 2)
    if cancel: return
    intervalo = float(v)

    print()
    print("=" * 55)
    print(f"  SIMULACAO LOCAL INICIADA")
    print(f"  {asset} | {direction} | R${valor} | {niveis} niveis | max {max_ops}")
    print(f"  Ctrl+C para parar")
    print("=" * 55)
    print()

    seq_num = 0
    cenarios_3_session = 0
    lucro_session = 0.0

    try:
        while seq_num < max_ops:
            seq_num += 1
            hora = datetime.now().strftime("%H:%M:%S")

            print(f"\n{'#' * 55}")
            print(f"  [{hora}] SEQUENCIA {seq_num}/{max_ops}")
            print(f"{'#' * 55}")

            # --- PROTETOR ---
            print(f"\n  [PROTETOR] Verificando risco...")
            pre = protetor.verificar()
            if not pre["pode_continuar"]:
                print(f"  *** BLOQUEADO: {pre.get('motivo_bloqueio')} ***")
                print(f"  Saldo: R${pre['saldo_atual']} | Perda: {pre['perda_pct']}%")
                break
            print(f"  -> OK (Saldo: R${pre['saldo_atual']} | Perda: {pre['perda_pct']}%)")

            # --- GERENCIADOR LOCAL (MG) ---
            print(f"\n  [GERENCIADOR] Operando {asset} {direction} R${valor}...")
            mg = skill_calculate_mg(entrada=valor, payout=0.85, nivel=niveis)
            seq_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            cenario_final = 1
            lucro_seq = 0.0

            for i, nivel_info in enumerate(mg["niveis"]):
                nivel_nome = "entrada" if i == 0 else f"mg{i}"
                valor_op = nivel_info["valor"]

                print(f"    {nivel_info['nivel'].upper()}: R${valor_op}...", end=" ")
                time.sleep(0.6)

                result = skill_execute_operation(asset, direction, valor_op)
                lucro_seq += result["profit"]

                if result["result"] == "WIN":
                    print(f"WIN! +R${result['profit']}")
                    if i == 0:
                        cenario_final = 1
                    else:
                        cenario_final = 2
                else:
                    print(f"LOSS R${result['profit']}")
                    if i == len(mg["niveis"]) - 1:
                        cenario_final = 3

                skill_register_operation({
                    "asset": asset.upper(), "direction": direction,
                    "amount": valor_op, "result": result["result"],
                    "profit": result["profit"], "timestamp": result["timestamp"],
                    "cenario": cenario_final, "nivel_mg": nivel_nome,
                    "sequencia_id": seq_id,
                })

                if result["result"] == "WIN":
                    break

            lucro_session += lucro_seq
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1

            # Visual cenario
            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels[cenario_final]} | R${round(lucro_seq, 2)}")

            if cenario_final == 3:
                skill_log_alert("CENARIO_3", f"Cenario 3 em {asset}", {"perda": lucro_seq})

            # --- ANALISADOR ---
            print(f"\n  [ANALISADOR] Avaliando...")
            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            print(f"  -> Taxa: {met['taxa_acerto']}% | Acao: {rec['acao']}")

            if rec["acao"] == "PAUSAR":
                for m in rec["motivos"]:
                    print(f"     {m}")
                print(f"\n  Loop PAUSADO pelo Analisador.")
                break

            # --- DASHBOARD ---
            pos = protetor.verificar()
            print(f"\n  +------------------------------------------+")
            print(f"  | Saldo: R${pos['saldo_atual']:<10} Lucro sessao: R${round(lucro_session,2):<7}|")
            print(f"  | C3: {cenarios_3_session}  Taxa: {met['taxa_acerto']}%  Risco: {pos['perda_pct']}%{'':<9}|")
            print(f"  +------------------------------------------+")

            if seq_num < max_ops:
                for s in range(int(intervalo), 0, -1):
                    print(f"  Proxima em {s}s...", end="\r")
                    time.sleep(1)
                frac = intervalo - int(intervalo)
                if frac > 0:
                    time.sleep(frac)
                print(" " * 30, end="\r")

    except KeyboardInterrupt:
        print(f"\n\n  [!] Interrompido pelo usuario")

    # Relatorio final
    print(f"\n{'=' * 55}")
    print(f"  RELATORIO FINAL - SIMULACAO")
    print(f"{'=' * 55}\n")
    analisador.gerar_relatorio(salvar=True, imprimir=True)
    print(f"\n  Sequencias: {seq_num} | Lucro: R${round(lucro_session,2)} | C3: {cenarios_3_session}")
    print(f"  [Salvo em relatorio.txt]")
    print(f"{'=' * 55}\n")


# ============================================================
# LOOP QUOTEX REAL (websocket)
# ============================================================

# Simbolos cripto conhecidos (parte do par de ativo)
_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "LTC", "XRP", "BCH", "EOS", "DOGE", "DASH",
    "XMR", "NEO", "IOTA", "TRX", "ADA", "XLM", "BNB", "ETC",
    "SOL", "DOT", "MATIC", "LINK", "AVAX", "UNI", "ALGO", "ATOM",
    "FIL", "VET", "THETA", "SHIB", "TON", "APT", "ARB", "OP",
    "NEAR", "FTM", "MANA", "SAND", "AXS", "CRO", "EGLD",
})

# Palavras-chave de materias-primas
_COMMODITY_KEYWORDS = frozenset({
    "GOLD", "SILVER", "OIL", "BRENT", "WTI", "GAS", "CRUDE",
    "COFFEE", "SUGAR", "COCOA", "COPPER", "PLATINUM", "PALLADIUM",
    "WHEAT", "CORN", "COTTON", "ZINC", "NICKEL", "ALUMINIUM",
    "LUMBER", "SOYBEAN", "NATURAL",
})


def _classificar_ativo(nome_display: str) -> str:
    """Classifica ativo por tipo de mercado a partir do nome de exibicao.

    Retorna: FOREX | CRIPTO | MATERIA_PRIMA | ACAO
    """
    nome_limpo = nome_display.replace("(OTC)", "").replace("(Digital)", "").strip()
    nome_upper = nome_limpo.upper()

    # Cripto: qualquer parte do par e simbolo cripto
    partes = [p.strip() for p in nome_limpo.replace("/", " ").split() if p.strip()]
    for parte in partes:
        if parte.upper() in _CRYPTO_SYMBOLS:
            return "CRIPTO"

    # Materia-prima: nome contem palavra-chave de commodity
    for keyword in _COMMODITY_KEYWORDS:
        if keyword in nome_upper:
            return "MATERIA_PRIMA"

    # Forex: padrao XXX/YYY com dois codigos de exatamente 3 letras
    if "/" in nome_limpo:
        lados = nome_limpo.split("/")
        if len(lados) == 2:
            a, b = lados[0].strip(), lados[1].strip()
            if len(a) == 3 and a.isalpha() and len(b) == 3 and b.isalpha():
                return "FOREX"

    # Padrao: acoes e indices
    return "ACAO"


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
            return True
        except asyncio.TimeoutError:
            print(f"  [TIMEOUT] Conexao nao estabelecida em {timeout_con}s")
        except Exception as e:
            print(f"  [ERRO] {type(e).__name__}: {e}")

        if i < tentativas:
            print(f"  Aguardando {cfg.get('intervalo_verificacao_seg', 3)}s...")
            await asyncio.sleep(int(cfg.get("intervalo_verificacao_seg", 3)))

    print(f"  [FALHA] Nao foi possivel conectar apos {tentativas} tentativas.")
    return False


async def _reconectar(quotex: "AgentQuotex", cfg: dict) -> bool:
    """Tenta reconectar apos queda de conexao mid-session."""
    print(f"\n  [RECONEXAO] Conexao perdida. Reconectando...")
    quotex.conectado = False
    try:
        await quotex.client.close()
    except Exception:
        pass
    return await _conectar_com_retry(quotex, cfg)


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

    # Verifica asset
    asset_info = await quotex.check_asset(asset)
    if not asset_info["aberto"]:
        print(f"\n  [ERRO] Ativo {asset} esta fechado!")
        await quotex.desconectar()
        return

    asset_real = asset_info["asset_resolvido"]
    payout_info = quotex.get_payout(asset)
    payout = payout_info["payout"]

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
                break
            print(f"  -> OK (Risco: {pre['perda_pct']}%)")

            # --- GERENCIADOR COM MG (Quotex real) ---
            fator_correcao = (1.0 / payout) if cfg.get("fator_correcao_mg") else 1.0
            mg = skill_calculate_mg(entrada=valor, payout=payout, nivel=niveis, fator_correcao=fator_correcao)
            seq_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            cenario_final = 1
            lucro_seq = 0.0

            for i, nivel_info in enumerate(mg["niveis"]):
                nivel_nome = "entrada" if i == 0 else f"mg{i}"
                valor_op = nivel_info["valor"]

                if i == 0:
                    # Entrada inicial: aguarda abertura do proximo candle
                    await aguardar_proximo_intervalo(duracao)
                else:
                    # Martingale: entra imediatamente apos resultado anterior
                    print(f"\n    [MG] Entrada imediata: {datetime.now().strftime('%H:%M:%S')}")

                print(f"\n    {nivel_info['nivel'].upper()}: R${valor_op} -> {asset_real} {direction} ({duracao}s)")

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
                        print(f"    [RECONEXAO OK] Pulando nivel atual, continuando loop")
                    break
                except Exception as e:
                    print(f"    [ERRO CONEXAO] {e}")
                    if await _reconectar(quotex, cfg):
                        print(f"    [RECONEXAO OK] Pulando nivel atual, continuando loop")
                    break

                if not result["success"]:
                    print(f"    [ERRO] {result.get('erro', 'Falha')}")
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
                    if i == len(mg["niveis"]) - 1:
                        cenario_final = 3

                # Registra no historico local
                skill_register_operation({
                    "asset": asset_real, "direction": direction,
                    "amount": valor_op, "result": result["result"],
                    "profit": profit,
                    "timestamp": datetime.now().isoformat(),
                    "cenario": cenario_final, "nivel_mg": nivel_nome,
                    "sequencia_id": seq_id,
                    "fonte": "quotex",
                    "modo": quotex.account_mode,
                    "duracao": duracao,
                })

                if result["result"] in ("WIN", "DOJI"):
                    break

            lucro_session += lucro_seq
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"Cenario 3 QUOTEX {asset_real}", {"perda": lucro_seq})

            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")

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
                break

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
                continue

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

            # --- Executar MG ---
            direcao = sinal["direcao"]
            duracao = sinal.get("duracao") or duracao_default
            fator_correcao = (1.0 / payout) if cfg_atual.get("fator_correcao_mg") else 1.0
            mg = skill_calculate_mg(entrada=valor, payout=payout, nivel=niveis, fator_correcao=fator_correcao)
            seq_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            cenario_final = 1
            lucro_seq = 0.0

            for i, nivel_info in enumerate(mg["niveis"]):
                nivel_nome = "entrada" if i == 0 else f"mg{i}"
                valor_op = nivel_info["valor"]

                if i == 0 and not sinal.get("horario"):
                    # Sinal sem horario especifico: aguarda abertura do candle
                    await aguardar_proximo_intervalo(duracao)
                elif i > 0:
                    # Martingale: entra imediatamente apos resultado anterior
                    print(f"\n    [MG] Entrada imediata: {datetime.now().strftime('%H:%M:%S')}")

                print(f"\n    {nivel_info['nivel'].upper()}: R${valor_op} -> {asset_real} {direcao.upper()} {duracao}s")

                timeout_res = int(cfg_atual.get("timeout_resultado_seg", 900))
                try:
                    result = await asyncio.wait_for(
                        quotex.operar(asset=asset_real, direction=direcao,
                                      amount=valor_op, duration=duracao),
                        timeout=timeout_res,
                    )
                except asyncio.TimeoutError:
                    print(f"    [TIMEOUT] Resultado nao recebido em {timeout_res}s")
                    if await _reconectar(quotex, cfg_atual):
                        print(f"    [RECONEXAO OK] Pulando nivel atual")
                    break
                except Exception as e:
                    print(f"    [ERRO CONEXAO] {e}")
                    if await _reconectar(quotex, cfg_atual):
                        print(f"    [RECONEXAO OK] Pulando nivel atual")
                    break

                if not result["success"]:
                    print(f"    [ERRO] {result.get('erro', 'Falha')}")
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
                    if i == len(mg["niveis"]) - 1:
                        cenario_final = 3

                skill_register_operation({
                    "asset": asset_real, "direction": direcao,
                    "amount": valor_op, "result": result["result"],
                    "profit": profit, "timestamp": datetime.now().isoformat(),
                    "cenario": cenario_final, "nivel_mg": nivel_nome,
                    "sequencia_id": seq_id,
                    "fonte": "telegram",
                    "modo": quotex.account_mode,
                    "duracao": duracao,
                })

                if result["result"] in ("WIN", "DOJI"):
                    break

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

    print("\n  [LISTA] Configuracao (Enter = valor padrao, 0 = voltar ao menu):")

    v, cancel = _inp("Arquivo de sinais", "sinais.json")
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
                break

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

            # Executar MG
            direcao = sinal["direcao"]
            duracao = sinal.get("duracao") or duracao_default
            fator_correcao = (1.0 / payout) if cfg_atual.get("fator_correcao_mg") else 1.0
            mg = skill_calculate_mg(entrada=valor, payout=payout, nivel=niveis, fator_correcao=fator_correcao)
            seq_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            cenario_final = 1
            lucro_seq = 0.0

            for i, nivel_info in enumerate(mg["niveis"]):
                nivel_nome = "entrada" if i == 0 else f"mg{i}"
                valor_op = nivel_info["valor"]

                if i == 0 and not sinal.get("horario"):
                    await aguardar_proximo_intervalo(duracao)
                elif i > 0:
                    print(f"\n    [MG] Entrada imediata: {datetime.now().strftime('%H:%M:%S')}")

                print(f"\n    {nivel_info['nivel'].upper()}: R${valor_op} -> {asset_real} {direcao.upper()} ({duracao}s)")

                timeout_res = int(cfg_atual.get("timeout_resultado_seg", 900))
                try:
                    result = await asyncio.wait_for(
                        quotex.operar(asset=asset_real, direction=direcao,
                                      amount=valor_op, duration=duracao),
                        timeout=timeout_res,
                    )
                except asyncio.TimeoutError:
                    print(f"    [TIMEOUT] Resultado nao recebido em {timeout_res}s")
                    if await _reconectar(quotex, cfg_atual):
                        print(f"    [RECONEXAO OK] Pulando nivel atual")
                    break
                except Exception as e:
                    print(f"    [ERRO CONEXAO] {e}")
                    if await _reconectar(quotex, cfg_atual):
                        print(f"    [RECONEXAO OK] Pulando nivel atual")
                    break

                if not result["success"]:
                    print(f"    [ERRO] {result.get('erro', 'Falha')}")
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
                    if i == len(mg["niveis"]) - 1:
                        cenario_final = 3

                skill_register_operation({
                    "asset": asset_real, "direction": direcao,
                    "amount": valor_op, "result": result["result"],
                    "profit": profit, "timestamp": datetime.now().isoformat(),
                    "cenario": cenario_final, "nivel_mg": nivel_nome,
                    "sequencia_id": seq_id,
                    "fonte": "lista",
                    "modo": quotex.account_mode,
                    "duracao": duracao,
                })

                if result["result"] in ("WIN", "DOJI"):
                    break

            lucro_session += lucro_seq
            ops_executadas += 1
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"C3 via Lista {asset_real}", {"perda": lucro_seq})

            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")

            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            saldo_real = await quotex.get_saldo()
            protetor.sincronizar_saldo(saldo_real['saldo'])
            print(f"  Saldo: R${saldo_real['saldo']} | Taxa: {met['taxa_acerto']}% | Sinais restantes: {len(sinais) - ops_executadas}")

            if rec["acao"] == "PAUSAR":
                motivo = rec["motivos"][0] if rec["motivos"] else ""
                print(f"  [ANALISADOR] PAUSANDO - {motivo}")
                break

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

    await quotex.desconectar()
    print("  [Desconectado]\n")


# ============================================================
# LOOP AUTONOMO (estrategias tecnicas)
# ============================================================

def loop_autonomo(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop automatico guiado por estrategias tecnicas (sem sinais externos)."""
    try:
        asyncio.run(_loop_autonomo_async(protetor, analisador))
    except KeyboardInterrupt:
        pass


async def _loop_autonomo_async(protetor: AgentProtetor, analisador: AgentAnalisador):
    """Loop async: analisa candles a cada abertura e executa se a estrategia sinalizar."""
    from estrategias import ESTRATEGIAS, ESTRATEGIAS_META, executar_estrategia

    cfg = carregar_config()

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

    # --- Selecionar ativo ---
    print("  Carregando ativos disponiveis...")
    ativos = _listar_ativos_quotex(quotex, cfg)

    if not ativos:
        print(f"  [AVISO] Nenhum ativo aberto com os filtros atuais. Ajuste o config.")
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
        print(f"  -> {ativo_sel['display']} | Payout: {ativo_sel['payout_pct']}%\n")
    except ValueError:
        print("  [ERRO] Numero invalido.")
        await quotex.desconectar()
        return

    # --- Selecionar estrategia ---
    nomes_est = list(ESTRATEGIAS.keys())
    est_atual = cfg.get("estrategia_ativa", "NENHUMA")
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
        estrategia_nome = "NENHUMA"

    # Exibe recomendacao de timeframe apos selecao
    meta_sel = ESTRATEGIAS_META.get(estrategia_nome, {})
    tf_rec = meta_sel.get("timeframe_rec")
    if tf_rec:
        tf_label = f"M{tf_rec // 60}" if tf_rec < 3600 else f"H{tf_rec // 3600}"
        print(f"  -> Estrategia: {estrategia_nome}  [timeframe recomendado: {tf_label} = {tf_rec}s]\n")
    else:
        print(f"  -> Estrategia: {estrategia_nome}\n")

    # Persiste selecao no config
    cfg["estrategia_ativa"] = estrategia_nome
    salvar_config(cfg)

    # --- Parametros de operacao ---
    v, cancel = _inp("Entrada R$", cfg["entrada_padrao"])
    if cancel: await quotex.desconectar(); return
    valor = float(v)

    v, cancel = _inp("Niveis MG", cfg["niveis_mg"])
    if cancel: await quotex.desconectar(); return
    niveis = int(v)

    # Usa timeframe recomendado da estrategia como default (se houver)
    dur_default = tf_rec if tf_rec else cfg["duracao_padrao"]
    v, cancel = _inp("Duracao do candle (seg)", dur_default)
    if cancel: await quotex.desconectar(); return
    duracao = int(v)

    # Verifica ativo e payout inicial
    asset_info = await quotex.check_asset(asset)
    if not asset_info["aberto"]:
        print(f"\n  [ERRO] {asset} esta fechado!")
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

    lucro_session = 0.0
    cenarios_3_session = 0
    ops_executadas = 0
    candles_analisados = 0

    handler_orig = _instalar_shutdown_gracioso()
    try:
        while not _shutdown_gracioso.is_set():

            # Protetor antes de aguardar candle
            pre = protetor.verificar()
            if not pre["pode_continuar"]:
                print(f"\n  *** BLOQUEADO: {pre.get('motivo_bloqueio')} ***")
                break

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
                    quotex.get_candles(asset, period=duracao, offset=duracao * 100),
                    timeout=30,
                )
                if not candles:
                    print(f"  [{hora}] Sem dados de candle.", end=" ", flush=True)
                    ok, ms = _verificar_internet()
                    if ok:
                        print(f"Rede OK ({ms:.0f}ms) — aguardando proximo intervalo.")
                    else:
                        print("Sem internet detectado.")
                        print(f"  [{hora}] [REDE] Aguardando 30s antes de tentar novamente...")
                        await asyncio.sleep(30)
                    continue
            except asyncio.TimeoutError:
                print(f"  [{hora}] [TIMEOUT] get_candles nao respondeu em 30s.", end=" ", flush=True)
                ok, ms = _verificar_internet()
                if ok:
                    print(f"Rede OK ({ms:.0f}ms) — possivel instabilidade no servidor.")
                else:
                    print("Sem internet detectado.")
                    print(f"  [{hora}] [REDE] Aguardando 30s antes de tentar novamente...")
                    await asyncio.sleep(30)
                continue
            except Exception as e:
                print(f"  [{hora}] [ERRO] Buscando candles: {e}")
                ok, _ = _verificar_internet()
                if not ok:
                    print(f"  [{hora}] [REDE] Sem internet. Aguardando 30s...")
                    await asyncio.sleep(30)
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

            # Executa MG
            fator_correcao = (1.0 / payout) if cfg_atual.get("fator_correcao_mg") else 1.0
            mg = skill_calculate_mg(entrada=valor, payout=payout, nivel=niveis, fator_correcao=fator_correcao)
            seq_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            cenario_final = 1
            lucro_seq = 0.0

            for i, nivel_info in enumerate(mg["niveis"]):
                nivel_nome = "entrada" if i == 0 else f"mg{i}"
                valor_op = nivel_info["valor"]

                if i > 0:
                    print(f"\n    [MG] Entrada imediata: {datetime.now().strftime('%H:%M:%S')}")

                print(f"\n    {nivel_info['nivel'].upper()}: R${valor_op} -> {asset_real} {sinal.upper()} ({duracao}s)")

                timeout_res = int(cfg_atual.get("timeout_resultado_seg", 900))
                try:
                    result = await asyncio.wait_for(
                        quotex.operar(asset=asset_real, direction=sinal,
                                      amount=valor_op, duration=duracao),
                        timeout=timeout_res,
                    )
                except asyncio.TimeoutError:
                    print(f"    [TIMEOUT] Resultado nao recebido em {timeout_res}s")
                    if await _reconectar(quotex, cfg_atual):
                        print(f"    [RECONEXAO OK] Pulando nivel atual")
                    break
                except Exception as e:
                    print(f"    [ERRO CONEXAO] {e}")
                    if await _reconectar(quotex, cfg_atual):
                        print(f"    [RECONEXAO OK] Pulando nivel atual")
                    break

                if not result["success"]:
                    print(f"    [ERRO] {result.get('erro', 'Falha')}")
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
                    if i == len(mg["niveis"]) - 1:
                        cenario_final = 3

                skill_register_operation({
                    "asset": asset_real, "direction": sinal,
                    "amount": valor_op, "result": result["result"],
                    "profit": profit, "timestamp": datetime.now().isoformat(),
                    "cenario": cenario_final, "nivel_mg": nivel_nome,
                    "sequencia_id": seq_id,
                    "fonte": "autonomo",
                    "estrategia": estrategia_nome,
                    "modo": quotex.account_mode,
                    "duracao": duracao,
                })

                if result["result"] in ("WIN", "DOJI"):
                    break

            lucro_session += lucro_seq
            ops_executadas += 1
            protetor.incrementar_ops()
            if cenario_final == 3:
                cenarios_3_session += 1
                skill_log_alert("CENARIO_3", f"C3 Autonomo {asset_real}",
                                {"perda": lucro_seq, "estrategia": estrategia_nome})

            labels = {0: "DOJI (empate - entrada devolvida)", 1: "CENARIO 1 - WIN DIRETO", 2: "CENARIO 2 - RECUPEROU NO MG", 3: "*** CENARIO 3 - PERDA TOTAL ***"}
            print(f"\n  -> {labels.get(cenario_final, '?')} | R${round(lucro_seq, 2)}")

            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]

            saldo_real = await quotex.get_saldo()
            protetor.sincronizar_saldo(saldo_real['saldo'])
            print(f"  Saldo: R${saldo_real['saldo']} | Taxa: {met['taxa_acerto']}% | Ops: {ops_executadas} | Candles: {candles_analisados}")

            if rec["acao"] == "PAUSAR":
                motivo_p = rec["motivos"][0] if rec["motivos"] else ""
                print(f"  [ANALISADOR] PAUSANDO - {motivo_p}")
                break

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
# MODO MANUAL (CLI interativo)
# ============================================================

_MENU = [
    ("1",  "sim",         "Loop SIMULACAO local (sem API)"),
    ("2",  "quotex",      "Loop QUOTEX real via websocket"),
    ("3",  "telegram",    "Loop TELEGRAM: sinais automaticos"),
    ("4",  "lista",       "Loop LISTA: sinais de arquivo JSON"),
    ("5",  "autonomo",    "Loop AUTONOMO: estrategias tecnicas"),
    ("6",  "config",      "Gerenciamento de risco (editar parametros)"),
    ("7",  "saldo",       "Consulta saldo na Quotex"),
    ("8",  "analise",     "Analise rapida (metricas, tendencia)"),
    ("9",  "relatorio",   "Relatorio completo (salva em arquivo)"),
    ("10", "status",      "Status do AgentProtetor"),
    ("11", "desbloquear", "Desbloqueia operacoes"),
    ("r",  "reiniciar",   "Reinicia a sessao (zera contadores+config)"),
    ("0",  "sair",        "Encerra o sistema"),
]


def comando_reiniciar() -> AgentProtetor:
    """Reinicia a sessao: recria protetor do config e limpa historico opcional."""
    print()
    print("  +--------------------------------------------------+")
    print("  |             REINICIAR SESSAO                     |")
    print("  +--------------------------------------------------+")
    print("  |  - Zera contador de ops da sessao                |")
    print("  |  - Zera referencia de saldo (re-sync Quotex)     |")
    print("  |  - Desbloqueia protetor se bloqueado             |")
    print("  |  - Recarrega parametros do config.json           |")
    print("  +--------------------------------------------------+")
    print()

    resp = input("  Limpar historico de operacoes (operacoes.json)? (s/N): ").strip().lower()

    novo = AgentProtetor.from_config()
    print("  [OK] Protetor reiniciado com config.json")

    if resp == "s":
        import json as _json
        ops_file = Path(__file__).resolve().parent / "operacoes.json"
        with open(ops_file, "w", encoding="utf-8") as f:
            _json.dump([], f)
        print("  [OK] Historico de operacoes limpo (operacoes.json)")

    print()
    return novo


def _exibir_menu(cfg: dict):
    """Imprime o menu numerado com resumo da config atual."""
    modo    = cfg.get("account_mode", "PRACTICE")
    mercado = cfg.get("tipo_mercado", "AMBOS")
    ativo   = cfg.get("tipo_ativo", "AMBOS")
    pay     = f"{cfg['payout_minimo_pct']}%"
    sl_pct  = f"{cfg['stop_loss_pct']}%"
    sl_r    = f"R${cfg['stop_loss_reais']}" if cfg.get('stop_loss_reais') else "OFF"
    tp      = f"R${cfg['take_profit_reais']}" if cfg.get('take_profit_reais') else "OFF"
    maxops  = str(cfg['max_ops_sessao']) if cfg.get('max_ops_sessao') else "ilimit"

    # Largura total: 53 chars por linha
    # Coluna esquerda: 25 chars | Coluna direita: 23 chars
    def _linha(lbl1, val1, lbl2, val2):
        esq  = f"  {lbl1:<7}: {val1:<12}  "  # 25 chars
        dir_ = f"  {lbl2:<7}: {val2:<10}  "  # 23 chars
        print(f"  |{esq}|{dir_}|")

    SEP_F = "  +" + "-" * 49 + "+"          # borda completa
    SEP_S = "  +" + "-" * 25 + "+" + "-" * 23 + "+"  # borda dividida

    print()
    print(SEP_F)
    print(f"  |{'TRADING AGENT SYSTEM':^49}|")
    print(SEP_S)
    _linha("Modo",    modo,    "Mercado",  mercado)
    _linha("Ativo",   ativo,   "Pay min",  pay)
    _linha("SL",      sl_pct,  "SL-R$",    sl_r)
    _linha("TP",      tp,      "MaxOps",   maxops)
    print(SEP_F)
    for num, _, descricao in _MENU:
        print(f"  |  [{num}] {descricao:<43}|")
    print(SEP_F)
    print()


def modo_manual(protetor: AgentProtetor, analisador: AgentAnalisador):
    """CLI interativo com menu numerado."""
    cfg = carregar_config()
    _exibir_menu(cfg)

    # Mapa numero/texto -> acao
    _alias = {num: cmd for num, cmd, _ in _MENU}
    _alias.update({cmd: cmd for _, cmd, _ in _MENU})
    _alias.update({"exit": "sair", "quit": "sair"})

    while True:
        entrada = input("  Opcao: ").strip().lower()

        if not entrada:
            continue

        cmd = _alias.get(entrada)

        if cmd is None:
            print(f"  Opcao invalida: '{entrada}'. Digite o numero ou nome do comando.")
            continue

        if cmd == "sair":
            print("\n  Ate mais!\n")
            break

        elif cmd == "sim":
            loop_simulacao(protetor, analisador)

        elif cmd == "quotex":
            loop_quotex(protetor, analisador)

        elif cmd == "telegram":
            loop_telegram(protetor, analisador)

        elif cmd == "lista":
            loop_lista(protetor, analisador)

        elif cmd == "autonomo":
            loop_autonomo(protetor, analisador)

        elif cmd == "config":
            comando_config()
            protetor = AgentProtetor.from_config()
            cfg = carregar_config()
            print("  [Protetor recarregado com nova config]\n")

        elif cmd == "saldo":
            try:
                saldo = asyncio.run(_check_saldo_quotex())
                print(f"\n  Saldo Quotex: R$ {saldo['saldo']}")
                print(f"  Modo:         {saldo['modo']}")
                print(f"  Demo:         R$ {saldo['demo_balance']}")
                print(f"  Real:         R$ {saldo['live_balance']}\n")
            except Exception as e:
                print(f"\n  [ERRO] {type(e).__name__}: {e}\n")

        elif cmd == "analise":
            print(f"\n{analisador.resumo()}\n")

        elif cmd == "relatorio":
            print()
            analisador.gerar_relatorio(salvar=True, imprimir=True)
            print("\n  [Salvo em relatorio.txt]\n")

        elif cmd == "status":
            print(f"\n{protetor.status()}\n")

        elif cmd == "desbloquear":
            protetor.forcar_desbloqueio()
            print("  [Protetor desbloqueado]\n")

        elif cmd == "reiniciar":
            protetor = comando_reiniciar()

        # Reexibe o menu sempre apos qualquer acao
        cfg = carregar_config()
        _exibir_menu(cfg)


async def _check_saldo_quotex() -> dict:
    """Helper async para consultar saldo Quotex."""
    cfg = carregar_config()
    quotex = AgentQuotex(account_mode=cfg.get("account_mode", "PRACTICE"))
    await quotex.conectar()
    saldo = await quotex.get_saldo()
    await quotex.desconectar()
    return saldo


# ============================================================
# MAIN
# ============================================================

def main():
    protetor = AgentProtetor.from_config()
    analisador = AgentAnalisador(janela=20)

    modo_manual(protetor, analisador)


if __name__ == "__main__":
    main()
