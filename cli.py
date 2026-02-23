"""
cli.py - Menu interativo, historico e comandos de sessao.
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path

from agents import (
    AgentProtetor, AgentAnalisador, AgentVerificador, AgentQuotex,
)
from skills import skill_read_history
from config import carregar_config, comando_config
from utils import log_s
from loops import (
    loop_quotex, loop_telegram, loop_lista,
    loop_autonomo, loop_backteste,
)


_MENU = [
    ("1",  "quotex",      "Loop QUOTEX real via websocket"),
    ("2",  "telegram",    "Loop TELEGRAM: sinais automaticos"),
    ("3",  "lista",       "Loop LISTA: sinais de arquivo JSON"),
    ("4",  "autonomo",    "Loop AUTONOMO: estrategias tecnicas"),
    ("5",  "backteste",   "BACKTESTE: acuracia de estrategias"),
    ("6",  "config",      "CONFIGURACOES (Parametros)"),
    ("7",  "historico",   "HISTORICO: ultimas operacoes"),
    ("__section__", "AGENTE ANALISADOR", None),
    ("8",  "analise",     "Analise rapida (metricas, tendencia)"),
    ("9",  "relatorio",   "Relatorio completo (salva em arquivo)"),
    ("__section__", "AGENTE PROTETOR", None),
    ("10", "status",      "STATUS ATUAL"),
    ("r",  "reiniciar",   "Reinicia a sessao"),
    ("0",  "sair",        "Encerra o sistema"),
]


def comando_reiniciar(protetor: AgentProtetor):
    """Reinicia a sessao: zera contadores e desbloqueia. Nao altera configuracoes."""
    print()
    estado = "ATIVADO" if protetor.bloqueado else "DESATIVADO"
    print("  +--------------------------------------------------+")
    print("  |             REINICIAR SESSAO                     |")
    print("  +--------------------------------------------------+")
    print("  |  - Zera contador de ops da sessao                |")
    print(f"  |  - AgentProtetor [{estado}]{'':<{27 - len(estado)}}|")
    print("  +--------------------------------------------------+")
    print()

    resp = input("  Limpar historico de operacoes (operacoes.json)? (s/N): ").strip().lower()

    protetor.forcar_desbloqueio()
    print("  [OK] Sessao reiniciada (contadores zerados)")
    log_s("INFO", "Sessao reiniciada manualmente (contadores zerados)")

    if resp == "s":
        import json as _json
        ops_file = Path(__file__).resolve().parent / "data" / "operacoes.json"
        with open(ops_file, "w", encoding="utf-8") as f:
            _json.dump([], f)
        print("  [OK] Historico de operacoes limpo (operacoes.json)")
        log_s("INFO", "Historico de operacoes limpo (operacoes.json)")

    print()


def comando_historico():
    """Exibe historico de operacoes agrupado por sequencia_id."""
    from datetime import datetime as _dt
    from collections import defaultdict

    resultado = skill_read_history(limit=500)
    ops_all = resultado.get("operacoes", [])

    if not ops_all:
        print("\n  [!] Nenhuma operacao registrada.\n")
        return

    # Agrupar por sequencia_id
    grupos = defaultdict(list)
    for op in ops_all:
        chave = op.get("sequencia_id") or op.get("timestamp", str(id(op)))
        grupos[chave].append(op)

    # Montar lista de sequencias
    sequencias = []
    for seq_id, ops_seq in grupos.items():
        ops_seq = sorted(ops_seq, key=lambda x: x.get("timestamp", ""))
        ts = ops_seq[0].get("timestamp", "")
        try:
            hora = _dt.fromisoformat(ts).strftime("%H:%M")
        except Exception:
            hora = "--:--"
        profit = sum(o.get("profit", 0) for o in ops_seq)
        sequencias.append({
            "hora":      hora,
            "ativo":     ops_seq[0].get("asset", "?")[:16],
            "dir":       ops_seq[0].get("direction", "?")[:4],
            "res":       "WIN" if profit > 0 else "LOSS",
            "profit":    profit,
            "ops":       ops_seq,
            "timestamp": ts,
        })

    sequencias.sort(key=lambda x: x["timestamp"], reverse=True)
    sequencias = sequencias[:20]

    # Separadores da lista (largura total = 63 chars)
    L_SEP  = "  +----+-------+------------------+------+------+-------------+"
    L_SEPF = "  +" + "=" * 59 + "+"

    def _exibir_lista():
        wins    = sum(1 for s in sequencias if s["res"] == "WIN")
        losses  = len(sequencias) - wins
        lucro   = sum(s["profit"] for s in sequencias)
        lucro_s = f"+R${lucro:.2f}" if lucro >= 0 else f"-R${abs(lucro):.2f}"

        print()
        print(L_SEPF)
        print(f"  |{'ULTIMAS OPERACOES REGISTRADAS':^59}|")
        print(L_SEP)
        print(f"  | {'#':<2} | {'Hora':<5} | {'Ativo':<16} | {'Dir':<4} | {'Res':<4} | {'Profit':<11} |")
        print(L_SEP)
        for i, seq in enumerate(sequencias, 1):
            p  = seq["profit"]
            ps = f"+R${p:.2f}" if p >= 0 else f"-R${abs(p):.2f}"
            print(f"  | {i:<2} | {seq['hora']:<5} | {seq['ativo']:<16} | {seq['dir']:<4} | {seq['res']:<4} | {ps:<11} |")
        print(L_SEP)
        rod = f" {len(sequencias)} entradas | Wins: {wins} | Losses: {losses} | Lucro: {lucro_s}"
        print(f"  |{rod:<59}|")
        print(L_SEPF)
        print()

    FONTE_MAP = {"quotex": "QUOTEX", "telegram": "TELEGRAM", "lista": "LISTA", "autonomo": "AUTONOMO"}
    # Separadores do detalhe (largura total = 67 chars)
    D_SEP  = "  +------+-------+----------+--------+------+-------+-------------+"
    D_SEPF = "  +" + "=" * 63 + "+"

    def _exibir_detalhe(idx):
        seq   = sequencias[idx]
        ops_s = seq["ops"]
        ativo = ops_s[0].get("asset", "?")
        fonte_raw = ops_s[0].get("fonte") or ""
        fonte = FONTE_MAP.get(fonte_raw, fonte_raw.upper() or "N/A")
        modo  = ops_s[0].get("modo", "N/A")
        dur   = ops_s[0].get("duracao", 0)
        try:
            dur_s = f"{dur}s (M{dur // 60})" if dur and dur >= 60 else f"{dur}s"
        except Exception:
            dur_s = str(dur)
        cenario = ops_s[-1].get("cenario", "N/A")
        ts_raw  = ops_s[0].get("timestamp", "")
        try:
            data_s = _dt.fromisoformat(ts_raw).strftime("%Y-%m-%d %H:%M")
        except Exception:
            data_s = ts_raw[:16]
        lucro_total = sum(o.get("profit", 0) for o in ops_s)
        lucro_s = f"+R$ {lucro_total:.2f}" if lucro_total >= 0 else f"-R$ {abs(lucro_total):.2f}"

        print()
        print(D_SEPF)
        print(f"  |{'DETALHE DA OPERACAO':^63}|")
        print(f"  | {'Loop: ' + fonte + '   |   Modo: ' + modo:<61} |")
        print(f"  | {'Ativo: ' + ativo + '   |   Duracao: ' + dur_s:<61} |")
        print(f"  | {'Cenario: ' + str(cenario) + '   |   Data: ' + data_s:<61} |")
        print(D_SEP)
        print(f"  | {'Niv':<4} | {'Hora':<5} | {'Valor':>8} | {'Payout':<6} | {'Dir':<4} | {'Res':<5} | {'Profit':<11} |")
        print(D_SEP)
        for op in ops_s:
            nivel    = (op.get("nivel_mg") or "entr")[:4]
            ts_op    = op.get("timestamp", "")
            try:
                hora_op = _dt.fromisoformat(ts_op).strftime("%H:%M")
            except Exception:
                hora_op = "--:--"
            amount    = op.get("amount", 0)
            res_op    = (op.get("result") or "?")[:5]
            dir_op    = (op.get("direction") or "?")[:4]
            profit_op = op.get("profit", 0)
            payout_s  = f"{round(profit_op / amount * 100)}%" if res_op.upper() == "WIN" and amount > 0 else "N/A"
            ps = f"+R${profit_op:.2f}" if profit_op >= 0 else f"-R${abs(profit_op):.2f}"
            print(f"  | {nivel:<4} | {hora_op:<5} | R${amount:>6.2f} | {payout_s:<6} | {dir_op:<4} | {res_op:<5} | {ps:<11} |")
        print(D_SEP)
        print(f"  | {f'Lucro da sequencia: {lucro_s}':<61} |")
        print(D_SEPF)
        print()

    while True:
        _exibir_lista()
        entrada = input("  Numero para detalhar | x: voltar: ").strip().lower()
        if entrada in ("x", ""):
            break
        try:
            idx = int(entrada) - 1
            if 0 <= idx < len(sequencias):
                _exibir_detalhe(idx)
                input("  [Enter para voltar a lista] ")
            else:
                print(f"  [!] Numero invalido (1-{len(sequencias)})\n")
        except ValueError:
            print("  [!] Digite um numero ou 'x' para sair\n")


def _exibir_menu(cfg: dict, protetor: "AgentProtetor" = None):
    """Imprime o menu numerado com resumo da config atual."""
    modo    = cfg.get("account_mode", "PRACTICE")
    mercado = cfg.get("tipo_mercado", "AMBOS")
    ativo   = cfg.get("tipo_ativo", "AMBOS")
    pay     = f"{cfg['payout_minimo_pct']}%"
    sl_r    = f"R${cfg['stop_loss_reais']}" if cfg.get('stop_loss_reais') else "OFF"
    tp      = f"R${cfg['take_profit_reais']}" if cfg.get('take_profit_reais') else "OFF"
    maxops  = str(cfg['max_ops_sessao']) if cfg.get('max_ops_sessao') else "ilimit"
    niveis  = str(cfg.get('niveis_mg', 3))

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
    print(f"  |{'TRADING SYSTEM QUOTEX':^49}|")
    print(SEP_S)
    _linha("Modo",    modo,    "Mercado",  mercado)
    _linha("MaxOps",  maxops,  "Ativos",   ativo)
    _linha("SL -R$",  sl_r,    "Pay min",  pay)
    _linha("TP +R$",  tp,      "Niv MG",   niveis)
    print(SEP_F)
    for num, cmd, descricao in _MENU:
        if num == "__section__":
            print(f"  |  {'-- ' + cmd + ' --':<47}|")
            continue
        if num in ("r", "0"):
            continue
        if cmd == "status" and protetor is not None:
            est = "BLOQUEADO" if protetor.bloqueado else "LIVRE"
            descricao = f"STATUS ATUAL [{est}]"
        print(f"  |  [{num}] {descricao:<43}|")
    print(SEP_F)
    print(f"  |  {'[r]: Reinicia sistema':<22} | {'[0]: Encerra sistema':<22}|")
    print(SEP_F)
    print()


def modo_manual(protetor: AgentProtetor, analisador: AgentAnalisador, verificador: AgentVerificador):
    """CLI interativo com menu numerado."""
    cfg = carregar_config()
    _exibir_menu(cfg, protetor)

    # Mapa numero/texto -> acao
    _alias = {num: cmd for num, cmd, _ in _MENU if num != "__section__"}
    _alias.update({cmd: cmd for num, cmd, _ in _MENU if num != "__section__"})
    _alias.update({"exit": "sair", "quit": "sair"})

    while True:
        try:
            entrada = input("  Opcao: ").strip().lower()
        except KeyboardInterrupt:
            print("\n  Saindo...")
            break

        if not entrada:
            continue

        cmd = _alias.get(entrada)

        if cmd is None:
            print(f"  Opcao invalida: '{entrada}'. Digite o numero ou nome do comando.")
            continue

        if cmd == "sair":
            print("\n  Ate mais!\n")
            break

        elif cmd == "quotex":
            loop_quotex(protetor, analisador)

        elif cmd == "telegram":
            loop_telegram(protetor, analisador)

        elif cmd == "lista":
            loop_lista(protetor, analisador)

        elif cmd == "autonomo":
            loop_autonomo(protetor, analisador, verificador)

        elif cmd == "config":
            comando_config(protetor)
            protetor    = AgentProtetor.from_config()
            verificador = AgentVerificador.from_config()
            cfg = carregar_config()
            print("  [Protetor e Verificador recarregados com nova config]\n")

        elif cmd == "analise":
            print(f"\n{analisador.resumo()}\n")

        elif cmd == "relatorio":
            print()
            analisador.gerar_relatorio(salvar=True, imprimir=True)
            print("\n  [Salvo em relatorio.txt]\n")

        elif cmd == "status":
            print(f"\n{protetor.status()}\n")

        elif cmd == "historico":
            comando_historico()

        elif cmd == "backteste":
            loop_backteste(protetor, analisador, verificador)

        elif cmd == "reiniciar":
            comando_reiniciar(protetor)

        # Reexibe o menu sempre apos qualquer acao
        cfg = carregar_config()
        _exibir_menu(cfg, protetor)


async def _check_saldo_quotex() -> dict:
    """Helper async para consultar saldo Quotex."""
    cfg = carregar_config()
    quotex = AgentQuotex(account_mode=cfg.get("account_mode", "PRACTICE"))
    await quotex.conectar()
    saldo = await quotex.get_saldo()
    await quotex.desconectar()
    return saldo
