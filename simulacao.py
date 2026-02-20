"""
Simulacao Visual - Roda o sistema completo SEM Claude API.

Simula o fluxo real: Protetor -> Gerenciador (MG local) -> Analisador -> Relatorio.
Tudo visual no terminal com delays para acompanhar.
"""

import time
import random
from datetime import datetime
from pathlib import Path
from skills import (
    skill_read_balance, skill_calculate_mg, skill_execute_operation,
    skill_register_operation, skill_read_history, skill_generate_report,
    skill_check_protection, skill_log_alert,
)
from agents import AgentProtetor, AgentAnalisador


# ============================================================
# GERENCIADOR LOCAL (simula o que Claude faria)
# ============================================================

def gerenciador_local(asset: str, direction: str, entrada: float = 10.0,
                      payout: float = 0.85, niveis_mg: int = 3) -> dict:
    """Simula o fluxo completo do AgentGerenciador sem Claude API.

    Executa: saldo -> calculo MG -> operacao -> MGs se LOSS -> registro -> analise.
    Retorna dicionario com resultado completo da sequencia.
    """
    seq_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    resultados = []
    cenario_final = 1
    lucro_sequencia = 0.0

    # --- PASSO 1: Saldo ---
    saldo = skill_read_balance()
    print(f"    Saldo: R$ {saldo['saldo_atual']}")
    time.sleep(0.3)

    # --- PASSO 2: Calculo MG ---
    mg = skill_calculate_mg(entrada=entrada, payout=payout, nivel=niveis_mg)
    print(f"    Tabela MG calculada:")
    for n in mg["niveis"]:
        print(f"      {n['nivel']}: R$ {n['valor']} (lucro se WIN: R$ {n['lucro_se_ganhar']})")
    print(f"      Perda total se perder tudo: R$ {mg['perda_total_se_perder_tudo']}")
    time.sleep(0.3)

    # Verifica se saldo cobre
    if saldo["saldo_atual"] < mg["perda_total_se_perder_tudo"]:
        print(f"    [!] Saldo insuficiente para cobrir MG completo!")
        return {"cenario": 0, "lucro": 0, "motivo": "saldo_insuficiente"}

    # --- PASSO 3-5: Execucao com MG ---
    for i, nivel_info in enumerate(mg["niveis"]):
        nivel_nome = "entrada" if i == 0 else f"mg{i}"
        valor_op = nivel_info["valor"]

        print()
        print(f"    --- {nivel_info['nivel'].upper()} ---")
        print(f"    Executando {asset} {direction} R$ {valor_op}...")
        time.sleep(0.8)  # Suspense

        # Executa
        result = skill_execute_operation(asset, direction, valor_op)

        # Visual do resultado
        if result["result"] == "WIN":
            print(f"    >>> WIN! Profit: R$ {result['profit']} <<<")
        else:
            print(f"    >>> LOSS! Prejuizo: R$ {result['profit']} <<<")

        lucro_sequencia += result["profit"]

        # Determina cenario
        if i == 0 and result["result"] == "WIN":
            cenario_final = 1
        elif i > 0 and result["result"] == "WIN":
            cenario_final = 2
        elif i == len(mg["niveis"]) - 1 and result["result"] == "LOSS":
            cenario_final = 3

        # Registra
        reg_data = {
            "asset": asset.upper(),
            "direction": direction,
            "amount": valor_op,
            "result": result["result"],
            "profit": result["profit"],
            "timestamp": result["timestamp"],
            "cenario": cenario_final,
            "nivel_mg": nivel_nome,
            "sequencia_id": seq_id,
        }
        skill_register_operation(reg_data)
        resultados.append(reg_data)

        time.sleep(0.3)

        # Se WIN, para a sequencia
        if result["result"] == "WIN":
            break

        # Se LOSS e tem proximo nivel, continua MG
        if i < len(mg["niveis"]) - 1:
            print(f"    LOSS -> Avancando para {mg['niveis'][i+1]['nivel']}...")
            time.sleep(0.5)

    return {
        "cenario": cenario_final,
        "lucro": round(lucro_sequencia, 2),
        "operacoes": resultados,
        "sequencia_id": seq_id,
    }


# ============================================================
# LOOP DE SIMULACAO VISUAL
# ============================================================

def simulacao_visual():
    """Loop principal da simulacao visual."""

    print()
    print("=" * 55)
    print("  SIMULACAO VISUAL - Trading Agent System")
    print("  (Sem Claude API - 100% local)")
    print("=" * 55)
    print()

    # --- Configuracao ---
    asset = input("  Asset (ex: EURUSD): ").strip() or "EURUSD"
    direction = input("  Direcao (call/put): ").strip().lower() or "call"
    valor = input("  Entrada R$ (10): ").strip()
    valor = float(valor) if valor else 10.0
    max_ops = input("  Max sequencias (10): ").strip()
    max_ops = int(max_ops) if max_ops else 10
    niveis = input("  Niveis MG - entrada+MGs (3): ").strip()
    niveis = int(niveis) if niveis else 3
    intervalo = input("  Intervalo entre ops em seg (2): ").strip()
    intervalo = float(intervalo) if intervalo else 2.0
    limpar = input("  Limpar historico anterior? (s/N): ").strip().lower()

    if limpar == "s":
        ops_file = Path(__file__).resolve().parent / "operacoes.json"
        ops_file.write_text("[]", encoding="utf-8")
        alerts_file = Path(__file__).resolve().parent / "alertas.json"
        if alerts_file.exists():
            alerts_file.write_text("[]", encoding="utf-8")
        print("  [Historico limpo]")

    protetor = AgentProtetor(limite_perda_pct=20.0)
    analisador = AgentAnalisador(janela=20)

    print()
    print("+" + "=" * 53 + "+")
    print(f"|  INICIANDO SIMULACAO{' ':33}|")
    print(f"|  {asset} | {direction} | R${valor} | {niveis} niveis MG{' ':14}|")
    print(f"|  Max {max_ops} sequencias | Intervalo {intervalo}s{' ':16}|")
    print(f"|  Ctrl+C para parar{' ':34}|")
    print("+" + "=" * 53 + "+")
    print()
    time.sleep(1)

    seq_num = 0
    cenarios_3_total = 0
    lucro_session = 0.0
    saldo_inicial_session = skill_read_balance()["saldo_atual"]

    try:
        while seq_num < max_ops:
            seq_num += 1
            hora = datetime.now().strftime("%H:%M:%S")

            print()
            print("#" * 55)
            print(f"#  [{hora}] SEQUENCIA {seq_num}/{max_ops}")
            print("#" * 55)

            # ====== FASE 1: PROTETOR ======
            print()
            print("  [PROTETOR] Verificando risco...")
            time.sleep(0.5)
            pre = protetor.verificar()

            if not pre["pode_continuar"]:
                print()
                print("  +--------------------------------------+")
                print("  |  *** OPERACAO BLOQUEADA ***           |")
                print(f"  |  Motivo: {pre.get('motivo_bloqueio', '?')[:28]:<29}|")
                print(f"  |  Saldo:  R$ {pre['saldo_atual']:<25}|")
                print(f"  |  Perda:  {pre['perda_pct']}%{' ':27}|")
                print(f"  |  Streak: {pre['loss_streak_atual']:<28}|")
                print("  +--------------------------------------+")
                print()
                print("  Loop ENCERRADO pelo Protetor.")
                break

            barra_risco = int(pre["perda_pct"] / 2)  # max 10 blocos para 20%
            barra_visual = "#" * barra_risco + "-" * (10 - barra_risco)
            print(f"  -> OK | Saldo: R${pre['saldo_atual']} | Risco: [{barra_visual}] {pre['perda_pct']}%")

            # ====== FASE 2: GERENCIADOR (local) ======
            print()
            print(f"  [GERENCIADOR] Operando {asset} {direction} R${valor}...")
            print(f"  {'.' * 40}")
            time.sleep(0.3)

            resultado = gerenciador_local(
                asset=asset,
                direction=direction,
                entrada=valor,
                payout=0.85,
                niveis_mg=niveis,
            )

            cenario = resultado["cenario"]
            lucro_seq = resultado["lucro"]
            lucro_session += lucro_seq

            if cenario == 3:
                cenarios_3_total += 1

            # Visual do cenario
            print()
            if cenario == 1:
                print("  +--------------------------------------+")
                print("  |  CENARIO 1 - WIN DIRETO!             |")
                print(f"  |  Lucro: R$ {lucro_seq:<27}|")
                print("  +--------------------------------------+")
            elif cenario == 2:
                print("  +--------------------------------------+")
                print("  |  CENARIO 2 - RECUPEROU NO MG!        |")
                print(f"  |  Lucro: R$ {lucro_seq:<27}|")
                print("  +--------------------------------------+")
            elif cenario == 3:
                print("  +--------------------------------------+")
                print("  |  *** CENARIO 3 - PERDA TOTAL ***     |")
                print(f"  |  Prejuizo: R$ {lucro_seq:<24}|")
                print("  +--------------------------------------+")
                skill_log_alert(
                    tipo="CENARIO_3",
                    mensagem=f"Cenario 3 em {asset} - perda R${lucro_seq}",
                    dados={"asset": asset, "perda": lucro_seq},
                )

            # ====== FASE 3: ANALISADOR ======
            print()
            print("  [ANALISADOR] Avaliando performance...")
            time.sleep(0.5)

            analise = analisador.analisar()
            rec = analise["recomendacao"]
            met = analise["metricas"]
            tend = analise["tendencia"]

            # Barra visual de taxa de acerto
            taxa = met["taxa_acerto"]
            barra_taxa = int(taxa / 5)  # 20 blocos = 100%
            barra_cor = "#" * barra_taxa + "-" * (20 - barra_taxa)

            print(f"  -> Taxa:      [{barra_cor}] {taxa}%")
            print(f"  -> Tendencia: {tend['direcao']} | Momentum: {tend.get('momentum', 'N/A')}")
            print(f"  -> Acao:      {rec['acao']}")
            for m in rec["motivos"]:
                print(f"                {m}")

            # ====== FASE 4: DASHBOARD ======
            pos = protetor.verificar()
            saldo_atual = pos["saldo_atual"]
            variacao = round(saldo_atual - saldo_inicial_session, 2)
            sinal = "+" if variacao >= 0 else ""

            print()
            print("  +------------------------------------------+")
            print(f"  |  DASHBOARD - Seq {seq_num}/{max_ops}{' ':23}|")
            print("  +------------------------------------------+")
            print(f"  |  Saldo:       R$ {saldo_atual:<24}|")
            print(f"  |  Variacao:    {sinal}R$ {variacao:<24}|")
            print(f"  |  Lucro sessao: R$ {round(lucro_session, 2):<22}|")
            print(f"  |  Cenarios 3:  {cenarios_3_total:<27}|")
            print(f"  |  Taxa acerto: {taxa}%{' ':25}|")
            print(f"  |  Loss streak: {pos['loss_streak_atual']:<27}|")
            print(f"  |  Risco:       {pos['perda_pct']}% / {pos['limite_perda_pct']}%{' ':17}|")
            print("  +------------------------------------------+")

            # ====== DECISAO AUTOMATICA ======
            if rec["acao"] == "PAUSAR":
                print()
                print("  *** ANALISADOR RECOMENDA: PAUSAR ***")
                for m in rec["motivos"]:
                    print(f"  -> {m}")
                print()
                print("  Loop PAUSADO pelo Analisador.")
                break

            if rec["acao"] == "AJUSTAR":
                print()
                print("  [!] Analisador sugere ajustes:")
                for aj in rec.get("ajustes_sugeridos", []):
                    print(f"      - {aj}")
                print("  Continuando...")

            # ====== INTERVALO ======
            if seq_num < max_ops:
                print()
                for s in range(int(intervalo), 0, -1):
                    print(f"  Proxima sequencia em {s}s...", end="\r")
                    time.sleep(1)
                # Pausa fracionada
                frac = intervalo - int(intervalo)
                if frac > 0:
                    time.sleep(frac)
                print(" " * 40, end="\r")  # Limpa linha

    except KeyboardInterrupt:
        print(f"\n\n  [!] Interrompido pelo usuario (Ctrl+C)")

    # ====== RELATORIO FINAL ======
    print()
    print()
    print("=" * 55)
    print("  RELATORIO FINAL DA SIMULACAO")
    print("=" * 55)
    print()

    analisador.gerar_relatorio(salvar=True, imprimir=True)

    final = protetor.verificar()
    saldo_final = final["saldo_atual"]
    var_total = round(saldo_final - saldo_inicial_session, 2)
    sinal_final = "+" if var_total >= 0 else ""

    print()
    print("  --- Resumo da Sessao ---")
    print(f"  Sequencias executadas: {seq_num}")
    print(f"  Lucro da sessao:      R$ {round(lucro_session, 2)}")
    print(f"  Cenarios 3:           {cenarios_3_total}")
    print(f"  Saldo inicial sessao: R$ {saldo_inicial_session}")
    print(f"  Saldo final:          R$ {saldo_final}")
    print(f"  Variacao:             {sinal_final}R$ {var_total}")
    print(f"  Status protetor:      {'BLOQUEADO' if not final['pode_continuar'] else 'OK'}")
    print(f"  [Relatorio salvo em relatorio.txt]")
    print("=" * 55)
    print()


if __name__ == "__main__":
    simulacao_visual()
