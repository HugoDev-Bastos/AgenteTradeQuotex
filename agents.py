"""
Agents - AgentTrading e AgentGerenciador.

Fluxo do tool use Anthropic:
  1. User envia mensagem
  2. Claude responde com content blocks (text + tool_use)
  3. Para cada tool_use, executamos a skill e devolvemos tool_result
  4. Claude recebe os resultados e decide: responder ou chamar mais tools
  5. Loop ate stop_reason == "end_turn"
"""

import os
import json
from pathlib import Path
from anthropic import Anthropic
from dotenv import dotenv_values
from skills import (
    TOOLS_SCHEMA, executar_tool,
    skill_check_protection, skill_log_alert, skill_read_balance,
    skill_read_history, skill_generate_report,
)

# Carrega .env com dotenv_values (mais robusto que load_dotenv)
_env = dotenv_values(Path(__file__).resolve().parent / ".env")
os.environ.update(_env)


# ============================================================
# BASE - Agentic loop reutilizavel
# ============================================================

class _BaseAgent:
    """Loop agentic base com tool use Anthropic."""

    def __init__(self, system_prompt: str, modelo: str = "claude-sonnet-4-5-20250929"):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key.startswith("sk-ant-..."):
            raise ValueError("Configure ANTHROPIC_API_KEY no .env")

        self.client = Anthropic(api_key=api_key)
        self.modelo = modelo
        self.system_prompt = system_prompt
        self.historico: list[dict] = []

    def chat(self, mensagem: str) -> str:
        """Envia mensagem e processa agentic loop com tool use."""
        self.historico.append({"role": "user", "content": mensagem})

        iteracao = 0
        max_iteracoes = 15

        while iteracao < max_iteracoes:
            iteracao += 1

            resposta = self.client.messages.create(
                model=self.modelo,
                max_tokens=4096,
                system=self.system_prompt,
                tools=TOOLS_SCHEMA,
                messages=self.historico,
            )

            texto_final = ""
            tool_results = []

            for bloco in resposta.content:
                if bloco.type == "text":
                    texto_final += bloco.text

                elif bloco.type == "tool_use":
                    input_preview = json.dumps(bloco.input, ensure_ascii=False)
                    if len(input_preview) > 100:
                        input_preview = input_preview[:100] + "..."
                    print(f"  [{iteracao}] tool_use: {bloco.name}({input_preview})")

                    try:
                        resultado = executar_tool(bloco.name, bloco.input)
                    except Exception as e:
                        resultado = json.dumps({"erro": str(e)}, ensure_ascii=False)

                    print(f"  [{iteracao}] result:   {resultado[:120]}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": bloco.id,
                        "content": resultado,
                    })

            content_serializado = _serializar_content(resposta.content)
            self.historico.append({"role": "assistant", "content": content_serializado})

            if resposta.stop_reason == "end_turn" or not tool_results:
                return texto_final

            self.historico.append({"role": "user", "content": tool_results})

        return texto_final or "[AVISO] Limite de iteracoes atingido."

    def reset(self):
        """Limpa historico para nova conversa."""
        self.historico.clear()

    def ver_historico(self) -> list[dict]:
        """Retorna historico legivel."""
        resumo = []
        for msg in self.historico:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                resumo.append({"role": role, "texto": content})
            elif isinstance(content, list):
                partes = []
                for item in content:
                    if isinstance(item, dict):
                        tipo = item.get("type", "")
                        if tipo == "text":
                            partes.append(item["text"][:100])
                        elif tipo == "tool_use":
                            partes.append(f"[call:{item['name']}]")
                        elif tipo == "tool_result":
                            partes.append(f"[result:{item['content'][:60]}]")
                if partes:
                    resumo.append({"role": role, "texto": " | ".join(partes)})
        return resumo


# ============================================================
# AGENT TRADING (simples)
# ============================================================

TRADING_PROMPT = """Voce e um agente de trading de opcoes binarias.
Use as tools para consultar saldo, calcular MG, executar e registrar operacoes.
Seja direto e objetivo.
"""


class AgentTrading(_BaseAgent):
    def __init__(self, modelo: str = "claude-sonnet-4-5-20250929"):
        super().__init__(system_prompt=TRADING_PROMPT, modelo=modelo)


# ============================================================
# AGENT GERENCIADOR (completo)
# ============================================================

GERENCIADOR_PROMPT = """Voce e o GERENCIADOR de operacoes de trading em opcoes binarias.
Voce controla todo o ciclo: saldo, calculo, execucao, registro, historico e analise.

=============================================
SUAS TOOLS
=============================================

OPERACAO:
- read_balance: consultar saldo (saldo_atual, lucro_acumulado, total_operacoes)
- calculate_mg: calcular tabela Martingale (Entrada, MG1, MG2, perda total)
- execute_operation: executar trade (asset, direction call/put, amount)
- register_operation: salvar no operacoes.json com TODOS os campos

HISTORICO E RELATORIO:
- read_history: ler ultimas operacoes do operacoes.json (filtro por asset, limit)
- generate_report: relatorio completo (taxa acerto, lucro, cenarios 3, stats)

PROTECAO:
- check_protection: verifica se pode operar (perda%, loss streak, cenarios 3)
- log_alert: registra alerta em alertas.json (STOP_LOSS, CENARIO_3, etc)

ANALISE:
- analisar_sentimento: sentimento de texto de mercado
- calcular_indicadores: RSI, medias moveis, cruzamentos
- gerar_sinal_trading: sinal de compra/venda/aguardar

=============================================
CONCEITOS IMPORTANTES
=============================================

MARTINGALE (MG):
- Estrategia de recuperacao apos LOSS
- Entrada base: valor inicial (ex: R$ 10)
- MG1: recupera perda da entrada + garante lucro original
- MG2: recupera perda da entrada + MG1 + garante lucro original
- Formula: proximo = (acumulado_perdido + lucro_desejado) / payout
- Lucro em qualquer nivel = SEMPRE igual ao lucro da entrada original

CENARIOS:
- Cenario 1: WIN na entrada. Lucro direto. Melhor caso.
- Cenario 2: LOSS entrada -> WIN no MG1 ou MG2. Recuperou + lucro.
- Cenario 3: LOSS entrada -> LOSS MG1 -> LOSS MG2. PERDA TOTAL. Pior caso.

=============================================
FLUXO OBRIGATORIO DE OPERACAO
=============================================

PASSO 0 - PROTECAO
  -> Chame check_protection
  -> Se pode_continuar=false: PARE, chame log_alert e reporte o motivo
  -> Se pode_continuar=true: continue

PASSO 1 - SALDO
  -> Chame read_balance
  -> Verifique se ha saldo suficiente para operacao + MG completo
  -> Se insuficiente: PARE e alerte

PASSO 2 - CALCULO MG
  -> Chame calculate_mg com entrada, payout e nivel
  -> Verifique se saldo cobre perda_total_se_perder_tudo
  -> Se nao cobre: PARE e alerte

PASSO 3 - EXECUCAO (entrada base)
  -> Chame execute_operation
  -> Chame register_operation com TODOS os campos:
     {asset, direction, amount, result, profit, timestamp,
      cenario: 1, nivel_mg: "entrada", sequencia_id: "<timestamp_da_entrada>"}
  -> Se WIN: va ao PASSO 6
  -> Se LOSS: va ao PASSO 4

PASSO 4 - MG1 (somente se LOSS no passo 3)
  -> Chame execute_operation com valor de MG1
  -> Chame register_operation:
     {asset, direction, amount, result, profit, timestamp,
      cenario: 2, nivel_mg: "mg1", sequencia_id: "<mesmo_da_entrada>"}
  -> Se WIN: va ao PASSO 6
  -> Se LOSS: va ao PASSO 5

PASSO 5 - MG2 (somente se LOSS no passo 4)
  -> Chame execute_operation com valor de MG2
  -> Determine cenario: se WIN = cenario 2, se LOSS = cenario 3
  -> Chame register_operation:
     {asset, direction, amount, result, profit, timestamp,
      cenario: <2 ou 3>, nivel_mg: "mg2", sequencia_id: "<mesmo_da_entrada>"}
  -> Se LOSS: marque CENARIO 3

PASSO 6 - ANALISE FINAL
  -> Chame read_balance para saldo atualizado
  -> Chame generate_report para estatisticas
  -> Apresente:

  ========== RELATORIO DA OPERACAO ==========
  Asset:          [par]
  Direcao:        [call/put]
  Cenario:        [1, 2 ou 3]
  Resultado:      [WIN/LOSS total]
  Entrada:        R$ [valor] -> [WIN/LOSS]
  MG1:            R$ [valor] -> [WIN/LOSS] (se executado)
  MG2:            R$ [valor] -> [WIN/LOSS] (se executado)
  Lucro/Prejuizo: R$ [valor]
  Saldo anterior: R$ [valor]
  Saldo atual:    R$ [valor]

  ========== ESTATISTICAS GERAIS ============
  Total operacoes: [N]
  Taxa de acerto:  [X]%
  Lucro acumulado: R$ [valor]
  Cenarios 3:      [N] (perda: R$ [valor])
  Maior win streak:  [N]
  Maior loss streak: [N]
  ============================================

  Se CENARIO 3:
  -> ALERTA: "*** CENARIO 3 - Perda total na sequencia ***"
  -> Chame read_history para ver ultimas operacoes
  -> Analise padroes: asset, direcao, horario
  -> Sugira: parar, reduzir entrada, trocar ativo, ou inverter direcao

=============================================
COMO REGISTRAR OPERACOES (IMPORTANTE!)
=============================================

Ao chamar register_operation, SEMPRE inclua estes campos no data:
- asset: par de moedas (ex: "EURUSD")
- direction: "call" ou "put"
- amount: valor em R$
- result: "WIN" ou "LOSS"
- profit: valor numerico (positivo se WIN, negativo se LOSS)
- timestamp: da operacao
- cenario: numero 1, 2 ou 3
- nivel_mg: "entrada", "mg1" ou "mg2"
- sequencia_id: timestamp da entrada base (agrupa entrada+MGs)

O cenario e atribuido assim:
- Na ENTRADA: registre cenario=1 (sera atualizado se perder)
- No MG1: se WIN, registre cenario=2
- No MG2: se WIN, registre cenario=2. Se LOSS, registre cenario=3

=============================================
QUANDO O USUARIO PEDIR RELATORIO/HISTORICO
=============================================

- "relatorio" ou "como estou": chame generate_report e apresente
- "historico" ou "ultimas operacoes": chame read_history
- "como foi [ASSET]": chame read_history com filtro de asset + generate_report
- Use os dados para identificar padroes e sugerir melhorias

=============================================
REGRAS ABSOLUTAS
=============================================

1. SEMPRE use as tools. NUNCA invente dados ou resultados.
2. SEMPRE consulte saldo ANTES de operar.
3. SEMPRE registre CADA operacao imediatamente apos executar.
4. SEMPRE inclua cenario, nivel_mg e sequencia_id no registro.
5. SEMPRE siga a sequencia: entrada -> MG1 (se loss) -> MG2 (se loss).
6. NUNCA pule niveis de MG.
7. SEMPRE apresente relatorio final com cenario e estatisticas.
8. Payout padrao: 0.85 (85%). Niveis MG padrao: 2 (MG1 + MG2).
9. Seja DIRETO. Execute e reporte.
10. Apos CENARIO 3, consulte historico e analise padroes.
11. APOS cada operacao (entrada/MG1/MG2), chame check_protection.
12. Se check_protection retornar pode_continuar=false, PARE IMEDIATAMENTE.
    -> Chame log_alert com tipo apropriado e reporte ao usuario.
"""


class AgentGerenciador(_BaseAgent):
    """Agente completo: saldo -> MG -> operacao -> registro -> analise.

    Trabalha com AgentProtetor que verifica ANTES e DEPOIS de cada operacao.
    """

    def __init__(self, modelo: str = "claude-sonnet-4-5-20250929",
                 limite_perda_pct: float = 20.0):
        super().__init__(system_prompt=GERENCIADOR_PROMPT, modelo=modelo)
        self.protetor = AgentProtetor(limite_perda_pct=limite_perda_pct)

    def operar(self, asset: str, direction: str, entrada: float = 10.0,
               payout: float = 0.85, niveis_mg: int = 2) -> str:
        """Operacao completa com protecao.

        1. Protetor verifica ANTES
        2. Gerenciador executa via Claude
        3. Protetor verifica DEPOIS
        """
        # === PRE-CHECK: Protetor verifica antes ===
        pre = self.protetor.verificar()
        if not pre["pode_continuar"]:
            bloqueio = (
                f"*** OPERACAO BLOQUEADA PELO PROTETOR ***\n\n"
                f"Motivo: {pre.get('motivo_bloqueio', 'Limite atingido')}\n"
                f"Saldo atual: R$ {pre['saldo_atual']}\n"
                f"Perda: {pre['perda_pct']}% (limite: {pre['limite_perda_pct']}%)\n"
                f"Loss streak: {pre['loss_streak_atual']}\n"
                f"Cenarios 3: {pre['cenarios_3']}\n\n"
                f"Para desbloquear: use 'desbloquear' (voce assume o risco)"
            )
            return bloqueio

        # === EXECUCAO: Claude faz o fluxo completo ===
        prompt = (
            f"Execute operacao completa com Martingale:\n"
            f"- Asset: {asset}\n"
            f"- Direcao: {direction}\n"
            f"- Entrada: R$ {entrada}\n"
            f"- Payout: {payout}\n"
            f"- Niveis MG: {niveis_mg}\n\n"
            f"Siga o fluxo completo: saldo -> calculo MG -> execucao -> registro -> analise.\n"
            f"Se tiver LOSS, execute os MGs automaticamente.\n"
            f"Apos CADA operacao, chame check_protection para verificar se pode continuar.\n"
            f"Se check_protection retornar pode_continuar=false, PARE imediatamente e reporte."
        )
        resposta = self.chat(prompt)

        # === POS-CHECK: Protetor verifica depois ===
        pos = self.protetor.verificar()
        if not pos["pode_continuar"]:
            resposta += (
                f"\n\n*** ALERTA DO PROTETOR ***\n"
                f"{pos.get('motivo_bloqueio', 'Limite atingido')}\n"
                f"Saldo: R$ {pos['saldo_atual']} | Perda: {pos['perda_pct']}%\n"
                f"Operacoes BLOQUEADAS ate desbloqueio manual."
            )

        return resposta


# ============================================================
# AGENT PROTETOR (guardian - sem Claude API)
# ============================================================

class AgentProtetor:
    """Guardiao local que monitora saldo e bloqueia operacoes perigosas.

    NAO usa Claude API - roda localmente para ser rapido e sem custo.
    Trabalha em paralelo com AgentGerenciador: verifica ANTES e DEPOIS.

    Regras de bloqueio (todas configuráveis via config.json):
    - Perda > stop_loss_pct (% do saldo inicial)
    - Perda > stop_loss_reais (valor absoluto em R$)
    - Lucro >= take_profit_reais (encerra no lucro alvo)
    - Loss streak >= max_loss_streak consecutivas
    - 3+ Cenarios 3 com perda > 10%
    - Ops sessao >= max_ops_sessao
    """

    def __init__(
        self,
        limite_perda_pct: float = 20.0,
        stop_loss_reais: float | None = None,
        take_profit_reais: float | None = None,
        max_ops_sessao: int | None = None,
        max_loss_streak: int = 5,
        saldo_inicial: float = 1000.0,
    ):
        self.limite_perda_pct = limite_perda_pct
        self.stop_loss_reais = stop_loss_reais
        self.take_profit_reais = take_profit_reais
        self.max_ops_sessao = max_ops_sessao
        self.max_loss_streak = max_loss_streak
        self.saldo_inicial = saldo_inicial
        self.bloqueado = False
        self.motivo_bloqueio: str | None = None
        self._ops_sessao: int = 0       # contador de ops desta sessao
        self._saldo_real: float | None = None  # saldo real da Quotex (quando conectado)

    @classmethod
    def from_config(cls) -> "AgentProtetor":
        """Cria AgentProtetor lendo parametros do config.json."""
        try:
            config_path = Path(__file__).resolve().parent / "config.json"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cls(
                limite_perda_pct=float(cfg.get("stop_loss_pct", 20.0)),
                stop_loss_reais=cfg.get("stop_loss_reais"),
                take_profit_reais=cfg.get("take_profit_reais"),
                max_ops_sessao=cfg.get("max_ops_sessao"),
                max_loss_streak=int(cfg.get("max_loss_streak", 5)),
                saldo_inicial=float(cfg.get("saldo_inicial", 1000.0)),
            )
        except Exception:
            return cls()

    def sincronizar_saldo(self, saldo_real: float):
        """Sincroniza o saldo real da Quotex.

        Chamado no inicio da sessao e apos cada operacao.
        Quando definido, o verificar() usa este valor em vez de
        calcular pelo historico de operacoes.json.
        """
        if self._saldo_real is None:
            # Primeira sincronizacao: define o saldo de referencia da sessao
            self.saldo_inicial = saldo_real
            print(f"  [PROTETOR] Saldo sincronizado com Quotex: R${saldo_real:.2f}")
            print(f"  [PROTETOR] Stop Loss {self.limite_perda_pct}% = R${saldo_real * self.limite_perda_pct / 100:.2f}")
            if self.stop_loss_reais:
                print(f"  [PROTETOR] Stop Loss R${self.stop_loss_reais} ativo")
            if self.take_profit_reais:
                print(f"  [PROTETOR] Take Profit R${self.take_profit_reais} ativo")
        self._saldo_real = saldo_real

    def incrementar_ops(self):
        """Registra uma operacao concluida nesta sessao."""
        self._ops_sessao += 1

    def verificar(self) -> dict:
        """Verifica se pode continuar operando. Retorna diagnostico completo."""
        resultado = skill_check_protection(
            limite_perda_pct=self.limite_perda_pct,
            saldo_inicial=self.saldo_inicial,
            max_loss_streak=self.max_loss_streak,
            saldo_atual_override=self._saldo_real,  # None em modo sim, real em modo Quotex
        )

        # Adiciona campos extras ao resultado
        resultado["ops_sessao"] = self._ops_sessao
        resultado["max_ops_sessao"] = self.max_ops_sessao
        resultado["stop_loss_reais"] = self.stop_loss_reais
        resultado["take_profit_reais"] = self.take_profit_reais

        # Cheques adicionais (stop_loss_reais, take_profit_reais, max_ops_sessao)
        if resultado["pode_continuar"]:
            perda_reais = self.saldo_inicial - resultado["saldo_atual"]

            if self.stop_loss_reais and perda_reais >= self.stop_loss_reais:
                resultado["pode_continuar"] = False
                resultado["motivo_bloqueio"] = (
                    f"Stop Loss R$ atingido: perdeu R${perda_reais:.2f} "
                    f"(limite: R${self.stop_loss_reais})"
                )

            elif self.take_profit_reais and resultado["lucro_total_reais"] >= self.take_profit_reais:
                resultado["pode_continuar"] = False
                resultado["motivo_bloqueio"] = (
                    f"Take Profit atingido: lucro R${resultado['lucro_total_reais']:.2f} "
                    f">= alvo R${self.take_profit_reais}"
                )

            elif self.max_ops_sessao and self._ops_sessao >= self.max_ops_sessao:
                resultado["pode_continuar"] = False
                resultado["motivo_bloqueio"] = (
                    f"Max ops/sessao atingido: {self._ops_sessao}/{self.max_ops_sessao}"
                )

        if not resultado["pode_continuar"]:
            self.bloqueado = True
            self.motivo_bloqueio = resultado.get("motivo_bloqueio", "Limite atingido")
            skill_log_alert(
                tipo="STOP_LOSS",
                mensagem=self.motivo_bloqueio,
                dados={
                    "saldo_atual": resultado["saldo_atual"],
                    "perda_pct": resultado["perda_pct"],
                    "loss_streak": resultado["loss_streak_atual"],
                    "cenarios_3": resultado["cenarios_3"],
                    "ops_sessao": self._ops_sessao,
                },
            )

        return resultado

    @property
    def pode_continuar(self) -> bool:
        """Verifica e retorna bool direto."""
        return self.verificar()["pode_continuar"]

    def forcar_desbloqueio(self):
        """Desbloqueia manualmente (usuario assume risco)."""
        self.bloqueado = False
        self.motivo_bloqueio = None
        self._ops_sessao = 0
        skill_log_alert(tipo="WARNING", mensagem="Desbloqueio manual pelo usuario")

    def status(self) -> str:
        """Retorna status legivel."""
        r = self.verificar()
        lucro = r.get("lucro_total_reais", 0)
        tp_info = f"R${self.take_profit_reais}" if self.take_profit_reais else "OFF"
        sl_r_info = f"R${self.stop_loss_reais}" if self.stop_loss_reais else "OFF"
        ops_info = f"{self._ops_sessao}/{self.max_ops_sessao}" if self.max_ops_sessao else f"{self._ops_sessao}/ilimitado"
        linhas = [
            f"Saldo:         R$ {r['saldo_atual']} (inicial: R${r['saldo_inicial']})",
            f"Lucro sessao:  R$ {lucro}",
            f"Perda:         {r['perda_pct']}% (Stop Loss: {r['limite_perda_pct']}%)",
            f"Stop Loss R$:  {sl_r_info}",
            f"Take Profit:   {tp_info}",
            f"Ops sessao:    {ops_info}",
            f"Loss streak:   {r['loss_streak_atual']} (max: {r['max_loss_streak']})",
            f"Cenarios 3:    {r['cenarios_3']}",
            f"Status:        {'BLOQUEADO - ' + r.get('motivo_bloqueio', '') if not r['pode_continuar'] else 'OK - pode operar'}",
        ]
        return "\n".join(linhas)


# ============================================================
# AGENT ANALISADOR (pensador - sem Claude API)
# ============================================================

class AgentAnalisador:
    """Analisa historico e recomenda acao com logica local.

    NAO usa Claude API. Le operacoes, calcula metricas, detecta
    tendencias e retorna recomendacao: CONTINUAR, PAUSAR ou AJUSTAR.
    """

    def __init__(self, janela: int = 20):
        self.janela = janela  # ultimas N operacoes para analisar

    def analisar(self) -> dict:
        """Analise completa: metricas + tendencia + recomendacao."""

        historico = skill_read_history(limit=self.janela)
        report = skill_generate_report()
        ops = historico.get("operacoes", [])

        # --- Metricas da janela ---
        metricas = self._calcular_metricas(ops)

        # --- Tendencia ---
        tendencia = self._detectar_tendencia(ops)

        # --- Recomendacao ---
        recomendacao = self._recomendar(metricas, tendencia, report)

        return {
            "janela": self.janela,
            "operacoes_analisadas": len(ops),
            "metricas": metricas,
            "tendencia": tendencia,
            "recomendacao": recomendacao,
            "stats_gerais": {
                "total_operacoes": report.get("total_operacoes", 0),
                "saldo_atual": report.get("saldo_atual", 1000),
                "lucro_total": report.get("lucro_total", 0),
                "cenarios_3_total": report.get("cenarios_3_total", 0),
            },
        }

    def _calcular_metricas(self, ops: list) -> dict:
        """Calcula metricas da janela de operacoes."""
        if not ops:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "taxa_acerto": 0, "lucro_janela": 0,
                "cenarios_3": 0, "media_profit": 0,
            }

        wins = [op for op in ops if op.get("result") == "WIN"]
        losses = [op for op in ops if op.get("result") == "LOSS"]
        cenarios_3 = [op for op in ops if op.get("cenario") == 3]
        total = len(ops)
        lucro = sum(op.get("profit", 0) for op in ops)

        return {
            "total": total,
            "wins": len(wins),
            "losses": len(losses),
            "taxa_acerto": round((len(wins) / total) * 100, 1) if total > 0 else 0,
            "lucro_janela": round(lucro, 2),
            "cenarios_3": len(cenarios_3),
            "media_profit": round(lucro / total, 2) if total > 0 else 0,
        }

    def _detectar_tendencia(self, ops: list) -> dict:
        """Detecta tendencia recente: subindo, caindo ou lateral."""
        if len(ops) < 6:
            return {"direcao": "indefinida", "forca": 0, "detalhe": "Poucas operacoes para tendencia"}

        # Divide em 2 metades e compara
        meio = len(ops) // 2
        primeira = ops[:meio]
        segunda = ops[meio:]

        lucro_1 = sum(op.get("profit", 0) for op in primeira)
        lucro_2 = sum(op.get("profit", 0) for op in segunda)

        wins_1 = sum(1 for op in primeira if op.get("result") == "WIN")
        wins_2 = sum(1 for op in segunda if op.get("result") == "WIN")

        taxa_1 = (wins_1 / len(primeira) * 100) if primeira else 0
        taxa_2 = (wins_2 / len(segunda) * 100) if segunda else 0

        # Tendencia por lucro
        diff_lucro = lucro_2 - lucro_1
        diff_taxa = taxa_2 - taxa_1

        # Limiares escalam com tamanho da amostra (mais ops = mais confiavel)
        limiar_taxa = max(15, 30 - len(ops))   # 15-30% dependendo da amostra
        limiar_lucro = max(5, 10 - len(ops) // 2)

        if diff_lucro > limiar_lucro and diff_taxa > limiar_taxa:
            direcao = "subindo"
            forca = min(round(abs(diff_taxa) / 15, 1), 5)
        elif diff_lucro < -limiar_lucro and diff_taxa < -limiar_taxa:
            direcao = "caindo"
            forca = min(round(abs(diff_taxa) / 15, 1), 5)
        else:
            direcao = "lateral"
            forca = 0

        # Ultimas 5 operacoes (momentum)
        ult5 = ops[-5:] if len(ops) >= 5 else ops
        wins_ult5 = sum(1 for op in ult5 if op.get("result") == "WIN")
        momentum = "positivo" if wins_ult5 >= 3 else "negativo" if wins_ult5 <= 1 else "neutro"

        return {
            "direcao": direcao,
            "forca": forca,
            "momentum": momentum,
            "lucro_metade_1": round(lucro_1, 2),
            "lucro_metade_2": round(lucro_2, 2),
            "taxa_metade_1": round(taxa_1, 1),
            "taxa_metade_2": round(taxa_2, 1),
            "detalhe": (
                f"1a metade: {round(taxa_1,1)}% acerto, R${round(lucro_1,2)} | "
                f"2a metade: {round(taxa_2,1)}% acerto, R${round(lucro_2,2)} | "
                f"Momentum: {momentum}"
            ),
        }

    def _recomendar(self, metricas: dict, tendencia: dict, report: dict) -> dict:
        """Gera recomendacao baseada nas metricas e tendencia."""
        acao = "CONTINUAR"
        motivos = []
        ajustes = []

        total = metricas["total"]
        taxa = metricas["taxa_acerto"]
        c3 = metricas["cenarios_3"]
        lucro = metricas["lucro_janela"]
        direcao = tendencia["direcao"]
        momentum = tendencia.get("momentum", "neutro")
        perda_total = report.get("cenarios_3_perda", 0)

        # Minimo de operacoes para recomendar pausa (evita falso alarme)
        MIN_OPS_PARA_AVALIAR = 5

        if total < MIN_OPS_PARA_AVALIAR:
            motivos.append(f"Amostra pequena ({total} ops) - coletando dados")
            return {"acao": "CONTINUAR", "motivos": motivos}

        # --- Regras de PAUSAR ---
        if taxa < 40:
            acao = "PAUSAR"
            motivos.append(f"Taxa de acerto baixa: {taxa}% (< 40%)")

        if c3 >= 2:
            acao = "PAUSAR"
            motivos.append(f"{c3} Cenarios 3 nas ultimas {metricas['total']} operacoes")

        if direcao == "caindo" and tendencia["forca"] >= 3:
            acao = "PAUSAR"
            motivos.append(f"Tendencia fortemente de queda (forca {tendencia['forca']})")

        if momentum == "negativo" and lucro < -20:
            acao = "PAUSAR"
            motivos.append(f"Momentum negativo + prejuizo de R${lucro}")

        # --- Regras de AJUSTAR ---
        if acao != "PAUSAR":
            if 40 <= taxa < 50:
                acao = "AJUSTAR"
                motivos.append(f"Taxa de acerto marginal: {taxa}%")
                ajustes.append("Reduzir valor de entrada")

            if c3 == 1:
                acao = "AJUSTAR"
                motivos.append("1 Cenario 3 recente")
                ajustes.append("Considerar trocar ativo ou direcao")

            if direcao == "caindo" and tendencia["forca"] >= 1:
                acao = "AJUSTAR"
                motivos.append("Tendencia de queda leve")
                ajustes.append("Reduzir niveis de MG ou valor de entrada")

            if momentum == "negativo":
                if acao == "CONTINUAR":
                    acao = "AJUSTAR"
                motivos.append("Momentum negativo nas ultimas 5 ops")
                ajustes.append("Esperar reverter antes de operar")

        # --- CONTINUAR ---
        if acao == "CONTINUAR":
            motivos.append(f"Taxa {taxa}%, tendencia {direcao}, momentum {momentum}")

            if taxa >= 60:
                motivos.append("Performance excelente")
            if momentum == "positivo":
                motivos.append("Bom momento para operar")

        resultado = {
            "acao": acao,
            "motivos": motivos,
        }

        if ajustes:
            resultado["ajustes_sugeridos"] = ajustes

        return resultado

    def gerar_relatorio(self, salvar: bool = True, imprimir: bool = True) -> str:
        """Gera relatorio completo, salva em arquivo e imprime no console.

        Combina dados do report geral + analise do analisador.
        Salva em relatorio.txt com timestamp.
        """
        from datetime import datetime as _dt

        report = skill_generate_report()
        analise = self.analisar()
        rec = analise["recomendacao"]
        tend = analise["tendencia"]

        total_ops = report.get("total_operacoes", 0)
        wins = report.get("wins", 0)
        losses = report.get("losses", 0)
        taxa = report.get("taxa_acerto_pct", 0)
        lucro_total = report.get("lucro_total", 0)
        total_ganho = report.get("total_ganho", 0)
        total_perdido = report.get("total_perdido", 0)
        saldo_inicial = report.get("saldo_inicial", 1000)
        saldo_atual = report.get("saldo_atual", 1000)
        c3_total = report.get("cenarios_3_total", 0)
        c3_perda = report.get("cenarios_3_perda", 0)
        cenarios = report.get("cenarios", {})
        assets = report.get("stats_por_asset", {})
        niveis = report.get("stats_por_nivel_mg", {})
        win_streak = report.get("maior_win_streak", 0)
        loss_streak = report.get("maior_loss_streak", 0)

        agora = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        SEP = "+" + "=" * 50 + "+"
        DIV = "+" + "-" * 50 + "+"

        linhas = [
            SEP,
            f"|{'RELATORIO DE PERFORMANCE':^50}|",
            f"|  {agora:<48}|",
            SEP,
            f"|  Total operacoes:    {total_ops:<28}|",
            f"|  Wins / Losses:      {wins} / {losses:<24}|",
            f"|  Taxa de acerto:     {taxa}%{'':<26}|",
            DIV,
            f"|{'FINANCEIRO':^50}|",
            DIV,
            f"|  Saldo inicial:      R$ {saldo_inicial:<24}|",
            f"|  Saldo atual:        R$ {saldo_atual:<24}|",
            f"|  Lucro total:        R$ {lucro_total:<24}|",
            f"|  Total ganho:        R$ {total_ganho:<24}|",
            f"|  Total perdido:      R$ {total_perdido:<24}|",
            DIV,
            f"|{'CENARIOS':^50}|",
            DIV,
        ]

        for c_num in sorted(cenarios.keys()):
            label = {
                1: "Cenario 1 (WIN direto)",
                2: "Cenario 2 (WIN no MG)",
                3: "Cenario 3 (LOSS total)",
            }.get(c_num, f"Cenario {c_num}")
            linhas.append(f"|  {label}: {cenarios[c_num]:<13}|")

        linhas.append(f"|  Cenarios 3 total:   {c3_total:<28}|")
        linhas.append(f"|  Perda Cenarios 3:   R$ {c3_perda:<24}|")

        # Stats por asset
        if assets:
            linhas.append(DIV)
            linhas.append(f"|{'POR ATIVO':^50}|")
            linhas.append(DIV)
            for asset_name, stats in assets.items():
                a_total = stats["wins"] + stats["losses"]
                a_taxa = round((stats["wins"] / a_total) * 100, 1) if a_total > 0 else 0
                info = f"{stats['wins']}W/{stats['losses']}L ({a_taxa}%)  R$ {stats['profit']}"
                linhas.append(f"|  {asset_name:<10} {info:<38}|")

        # Stats por nivel MG
        if niveis:
            linhas.append(DIV)
            linhas.append(f"|{'POR NIVEL MG':^50}|")
            linhas.append(DIV)
            for nivel_name, stats in niveis.items():
                n_total = stats["wins"] + stats["losses"]
                n_taxa = round((stats["wins"] / n_total) * 100, 1) if n_total > 0 else 0
                info = f"{stats['wins']}W/{stats['losses']}L ({n_taxa}%)  R$ {stats['profit']}"
                linhas.append(f"|  {nivel_name:<10} {info:<38}|")

        # Streaks
        linhas.append(DIV)
        linhas.append(f"|{'SEQUENCIAS':^50}|")
        linhas.append(DIV)
        linhas.append(f"|  Maior win streak:   {win_streak:<28}|")
        linhas.append(f"|  Maior loss streak:  {loss_streak:<28}|")

        # Tendencia
        linhas.append(DIV)
        linhas.append(f"|{'TENDENCIA':^50}|")
        linhas.append(DIV)
        linhas.append(f"|  Direcao:   {tend['direcao']:<37}|")
        linhas.append(f"|  Forca:     {tend['forca']:<37}|")
        linhas.append(f"|  Momentum:  {tend.get('momentum', 'N/A'):<37}|")

        # Recomendacao
        linhas.append(SEP)
        linhas.append(f"|  RECOMENDACAO:  {rec['acao']:<33}|")
        linhas.append(SEP)

        for motivo in rec["motivos"]:
            m_text = motivo[:46]
            linhas.append(f"|  -> {m_text:<46}|")

        if "ajustes_sugeridos" in rec:
            linhas.append(f"|{' ':50}|")
            linhas.append(f"|  Ajustes sugeridos:{' ':31}|")
            for aj in rec["ajustes_sugeridos"]:
                aj_text = aj[:44]
                linhas.append(f"|    - {aj_text:<44}|")

        linhas.append(SEP)

        texto = "\n".join(linhas)

        # --- Salvar em arquivo ---
        if salvar:
            from pathlib import Path as _Path
            arquivo = _Path(__file__).resolve().parent / "relatorio.txt"
            timestamp_header = f"Gerado em: {agora}\n{'=' * 50}\n\n"
            with open(arquivo, "w", encoding="utf-8") as f:
                f.write(timestamp_header + texto + "\n")

        # --- Imprimir no console ---
        if imprimir:
            print(texto)

        return texto

    def resumo(self) -> str:
        """Retorna analise em texto legivel."""
        a = self.analisar()
        m = a["metricas"]
        t = a["tendencia"]
        r = a["recomendacao"]
        s = a["stats_gerais"]

        linhas = [
            "========== ANALISE DO SISTEMA ==========",
            f"Janela: ultimas {a['operacoes_analisadas']} operacoes",
            "",
            "--- Metricas ---",
            f"  Taxa acerto:  {m['taxa_acerto']}%",
            f"  Wins/Losses:  {m['wins']}/{m['losses']}",
            f"  Lucro janela: R$ {m['lucro_janela']}",
            f"  Cenarios 3:   {m['cenarios_3']}",
            f"  Media profit: R$ {m['media_profit']}/op",
            "",
            "--- Tendencia ---",
            f"  Direcao:  {t['direcao']} (forca: {t['forca']})",
            f"  Momentum: {t.get('momentum', 'N/A')}",
            f"  {t['detalhe']}",
            "",
            "--- Geral ---",
            f"  Saldo: R$ {s['saldo_atual']}  |  Lucro total: R$ {s['lucro_total']}",
            f"  Total ops: {s['total_operacoes']}  |  Cenarios 3 total: {s['cenarios_3_total']}",
            "",
            f"======= RECOMENDACAO: {r['acao']} =======",
        ]

        for m in r["motivos"]:
            linhas.append(f"  -> {m}")

        if "ajustes_sugeridos" in r:
            linhas.append("")
            linhas.append("  Ajustes sugeridos:")
            for aj in r["ajustes_sugeridos"]:
                linhas.append(f"    - {aj}")

        linhas.append("=" * 40)
        return "\n".join(linhas)


# ============================================================
# AGENT QUOTEX (conexao websocket com corretora)
# ============================================================

class AgentQuotex:
    """Agente responsavel pela conexao websocket com a Quotex via PyQuotex.

    NAO usa Claude API. Gerencia conexao, saldo real, trades e resultados.
    Funciona como a "ponte" entre o sistema de agentes e a corretora.

    Modos: PRACTICE (demo) ou REAL (dinheiro real).
    """

    def __init__(self, email: str | None = None, password: str | None = None,
                 account_mode: str | None = None):
        from pyquotex.stable_api import Quotex

        self.email = email or os.environ.get("QUOTEX_EMAIL", "")
        self.password = password or os.environ.get("QUOTEX_PASSWORD", "")
        # Prioridade: parametro > config (passado pelo loop) > .env > PRACTICE
        self.account_mode = (
            account_mode or os.environ.get("QUOTEX_ACCOUNT_MODE", "PRACTICE")
        ).upper()

        if not self.email or not self.password:
            raise ValueError(
                "Credenciais Quotex nao encontradas. "
                "Preencha QUOTEX_EMAIL e QUOTEX_PASSWORD no .env"
            )

        self.client = Quotex(
            email=self.email,
            password=self.password,
            lang="pt",
        )
        self.conectado = False

    async def conectar(self) -> bool:
        """Conecta na Quotex via websocket. Retorna True se sucesso."""
        check, message = await self.client.connect()
        if check:
            self.conectado = True
            self.client.set_account_mode(self.account_mode)
            return True
        else:
            self.conectado = False
            raise ConnectionError(f"Falha ao conectar na Quotex: {message}")

    async def desconectar(self):
        """Fecha conexao websocket."""
        if self.conectado:
            await self.client.close()
            self.conectado = False

    def _checar_conexao(self):
        """Verifica se esta conectado. Levanta erro se nao."""
        if not self.conectado:
            raise ConnectionError("Nao conectado na Quotex. Chame conectar() primeiro.")

    # --- Saldo ---
    async def get_saldo(self) -> dict:
        """Retorna saldo da conta (demo ou real conforme account_mode)."""
        self._checar_conexao()
        balance = await self.client.get_balance()
        try:
            profile = await self.client.get_profile()
            demo_balance = float(profile.demo_balance)
            live_balance = float(profile.live_balance)
            usuario = profile.nick_name
        except Exception:
            # get_profile() pode falhar com resposta HTTP vazia (bug PyQuotex)
            # Usa balance como fallback para o campo relevante ao account_mode
            demo_balance = float(balance) if self.account_mode == "PRACTICE" else 0.0
            live_balance = float(balance) if self.account_mode == "REAL" else 0.0
            usuario = "N/A"
        return {
            "saldo": float(balance),
            "modo": self.account_mode,
            "demo_balance": demo_balance,
            "live_balance": live_balance,
            "usuario": usuario,
        }

    # --- Verificar asset ---
    async def check_asset(self, asset: str) -> dict:
        """Verifica se um ativo esta disponivel e aberto para trading.

        asset_data retorna: (id, nome_display, is_open)
        """
        self._checar_conexao()
        asset_name, asset_data = await self.client.get_available_asset(
            asset, force_open=True,
        )
        is_open = asset_data[2] if len(asset_data) > 2 else False
        nome_display = asset_data[1] if len(asset_data) > 1 else asset
        return {
            "asset_original": asset,
            "asset_resolvido": asset_name,
            "nome_display": nome_display,
            "aberto": is_open,
        }

    # --- Payout ---
    def get_payout(self, asset: str) -> dict:
        """Retorna payout atual de um ativo.

        Usa get_payment() (sync) que retorna todos os payouts.
        Busca pelo nome display do asset (ex: 'EUR/USD (OTC)').
        """
        self._checar_conexao()
        payments = self.client.get_payment()

        # Mapeia asset code para nome display
        # EURUSD_otc -> EUR/USD (OTC), EURUSD -> EUR/USD
        nome = asset.replace("_otc", "").upper()
        # Insere / entre as moedas: EURUSD -> EUR/USD
        if len(nome) == 6 and nome.isalpha():
            nome = f"{nome[:3]}/{nome[3:]}"
        # Adiciona (OTC) se _otc
        if "_otc" in asset.lower():
            nome_display = f"{nome} (OTC)"
        else:
            nome_display = nome

        payout_data = payments.get(nome_display, {})
        payout_pct = payout_data.get("payment", 0)
        is_open = payout_data.get("open", False)

        return {
            "asset": asset,
            "nome_display": nome_display,
            "payout": round(payout_pct / 100, 4),
            "payout_pct": payout_pct,
            "aberto": is_open,
        }

    # --- Executar trade ---
    async def buy(self, asset: str, direction: str, amount: float,
                  duration: int = 60) -> dict:
        """Executa trade na Quotex.

        Args:
            asset: Par de moedas (ex: EURUSD, EURUSD_otc)
            direction: 'call' (alta) ou 'put' (baixa)
            amount: Valor em R$ (ou moeda da conta)
            duration: Tempo de expiracao em segundos (padrao 60)

        Returns:
            Dict com status, trade_id e info do trade
        """
        self._checar_conexao()

        # Verifica asset: asset_data = (id, nome_display, is_open)
        asset_name, asset_data = await self.client.get_available_asset(
            asset, force_open=True,
        )
        is_open = asset_data[2] if len(asset_data) > 2 else False

        if not is_open:
            return {
                "success": False,
                "erro": f"Ativo {asset} esta fechado no momento",
                "asset_tentado": asset_name,
            }

        # Executa trade
        status, buy_info = await self.client.buy(
            amount, asset_name, direction.lower(), duration,
        )

        if not status:
            return {
                "success": False,
                "erro": "Falha ao executar trade",
                "detalhes": str(buy_info),
            }

        return {
            "success": True,
            "trade_id": buy_info.get("id"),
            "asset": asset_name,
            "direction": direction.lower(),
            "amount": amount,
            "duration": duration,
            "info": buy_info,
        }

    # --- Aguardar resultado ---
    async def check_result(self, trade_id) -> dict:
        """Aguarda resultado do trade (bloqueia ate expirar).

        Returns:
            Dict com result ("WIN" | "LOSS" | "DOJI") e profit.
            DOJI: empate — capital devolvido, profit == 0.0.
        """
        self._checar_conexao()

        win = await self.client.check_win(trade_id)
        profit = float(self.client.get_profit())

        if win:
            result = "WIN"
        elif profit == 0.0:
            result = "DOJI"   # empate: dinheiro devolvido, sem ganho nem perda
        else:
            result = "LOSS"

        return {
            "result": result,
            "profit": profit,
            "trade_id": trade_id,
        }

    # --- Trade completo (buy + wait result) ---
    async def operar(self, asset: str, direction: str, amount: float,
                     duration: int = 60) -> dict:
        """Executa trade e aguarda resultado. Retorno completo.

        Combina buy() + check_result() em uma unica chamada.
        """
        buy_result = await self.buy(asset, direction, amount, duration)

        if not buy_result["success"]:
            return buy_result

        trade_id = buy_result["trade_id"]
        resultado = await self.check_result(trade_id)

        return {
            "success": True,
            "asset": buy_result["asset"],
            "direction": direction.lower(),
            "amount": amount,
            "duration": duration,
            "result": resultado["result"],
            "profit": resultado["profit"],
        }

    # --- Candles ---
    async def get_candles(self, asset: str, period: int = 60,
                          offset: int = 86400) -> list:
        """Busca candles historicos de um ativo.

        Args:
            asset: Par de moedas
            period: Periodo do candle em segundos (60 = 1min)
            offset: Janela de tempo em segundos (86400 = 1 dia)
        """
        import time as _time
        self._checar_conexao()

        asset_name, asset_data = await self.client.get_available_asset(
            asset, force_open=True,
        )

        raw = await self.client.get_candles(
            asset_name, _time.time(), offset, period,
        )
        # PyQuotex pode retornar dict {"data": [...]} ou lista diretamente
        if isinstance(raw, dict):
            return raw.get("data", [])
        if isinstance(raw, list):
            return raw
        return []

    # --- Status ---
    def status(self) -> str:
        """Retorna status legivel da conexao."""
        linhas = [
            f"Conectado:  {'Sim' if self.conectado else 'Nao'}",
            f"Email:      {self.email[:3]}***{self.email[self.email.index('@'):]}" if self.email else "N/A",
            f"Modo:       {self.account_mode}",
        ]
        return "\n".join(linhas)


# ============================================================
# AGENT TELEGRAM (receptor de sinais via Telegram)
# ============================================================

class AgentTelegram:
    """Agente que monitora sinais do Telegram e os parseia com IA.

    Usa Telethon para conexao websocket com Telegram e Claude Haiku
    para parsing inteligente de qualquer formato de sinal.

    Fluxo:
      1. Conecta ao Telegram com a conta do usuario
      2. Monitora mensagens do bot/canal configurado
      3. Usa Claude para extrair ativo, direcao, duracao, horario
      4. Aplica offset de tempo ao horario do sinal
      5. Enfileira sinais validos para execucao
    """

    SISTEMA_PARSER = """Voce e um parser especializado em sinais de trading de opcoes binarias.

Extraia as seguintes informacoes do texto da mensagem:
- ativo: par de ativos no formato interno. Converta: EUR/USD -> EURUSD, adicione _otc se for OTC/digital.
  Exemplos: EURUSD_otc, GBPUSD_otc, EURUSD, GBPUSD, EURJPY_otc
- direcao: "call" (compra/alta/UP/seta cima) ou "put" (venda/baixa/DOWN/seta baixo)
- duracao: duracao em segundos. M1=60, M5=300, M15=900, M30=1800, H1=3600. Default 300.
- horario: horario de entrada no formato HH:MM, se mencionado. null se nao tiver.
- payout: percentual de retorno numerico, se mencionado. null se nao tiver.

Responda APENAS com JSON valido. Nada mais.

Se nao for possivel extrair sinal de trading, retorne: {"valido": false}

Se for sinal de trading, retorne:
{"valido": true, "ativo": "EURUSD_otc", "direcao": "call", "duracao": 300, "horario": "14:30", "payout": 85}

Exemplos:
- "EURUSD CALL M5 85%" -> {"valido": true, "ativo": "EURUSD_otc", "direcao": "call", "duracao": 300, "horario": null, "payout": 85}
- "EUR/USD - COMPRA - 5 minutos 14:30" -> {"valido": true, "ativo": "EURUSD_otc", "direcao": "call", "duracao": 300, "horario": "14:30", "payout": null}
- "GBP/USD PUT 1 minuto" -> {"valido": true, "ativo": "GBPUSD_otc", "direcao": "put", "duracao": 60, "horario": null, "payout": null}
- "Bom dia! Resultados de ontem foram otimos" -> {"valido": false}
"""

    # Duracoes validas Quotex (segundos)
    _DURACOES_VALIDAS = {5, 10, 15, 30, 60, 120, 180, 240, 300, 600, 900, 1800, 3600}

    def __init__(self):
        self.api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
        self.api_hash = os.environ.get("TELEGRAM_API_HASH", "")
        self.phone = os.environ.get("TELEGRAM_PHONE", "")
        self.bot_username = os.environ.get("TELEGRAM_BOT", "@QuotexSignalsBot_M5")
        self.time_offset = int(os.environ.get("TELEGRAM_TIME_OFFSET", "-60"))  # minutos

        if not self.api_id or not self.api_hash or not self.phone:
            raise ValueError(
                "Credenciais Telegram nao encontradas. "
                "Configure TELEGRAM_API_ID, TELEGRAM_API_HASH e TELEGRAM_PHONE no .env"
            )

        self._client_telegram = None
        self._client_ai = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        self.fila: "asyncio.Queue" = None  # inicializada no conectar()
        self.conectado = False

        # Contadores
        self.sinais_recebidos = 0
        self.sinais_validos = 0
        self.sinais_descartados = 0

    async def conectar(self) -> bool:
        """Conecta ao Telegram. Na primeira execucao pede codigo SMS."""
        import asyncio
        from telethon import TelegramClient, events

        self.fila = asyncio.Queue()

        session_path = str(Path(__file__).resolve().parent / "telegram_session")
        self._client_telegram = TelegramClient(session_path, self.api_id, self.api_hash)

        # start() pede codigo SMS se necessario (interativo no terminal)
        await self._client_telegram.start(phone=self.phone)
        self.conectado = True

        # Registra handler de mensagens do bot
        @self._client_telegram.on(events.NewMessage(chats=[self.bot_username]))
        async def _handler(event):
            texto = event.message.text or ""
            await self._processar_mensagem(texto)

        return True

    async def desconectar(self):
        """Desconecta do Telegram."""
        if self._client_telegram and self.conectado:
            await self._client_telegram.disconnect()
            self.conectado = False

    def _parsear_sinal(self, texto: str) -> dict:
        """Usa Claude Haiku para parsear o sinal. Retorna dict estruturado."""
        import json

        response = self._client_ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=self.SISTEMA_PARSER,
            messages=[{"role": "user", "content": texto}],
        )

        raw = response.content[0].text.strip()
        return json.loads(raw)

    def _ajustar_duracao(self, duracao: int) -> int:
        """Arredonda duracao para o valor valido mais proximo."""
        if duracao in self._DURACOES_VALIDAS:
            return duracao
        return min(self._DURACOES_VALIDAS, key=lambda x: abs(x - duracao))

    def _aplicar_offset(self, horario_str) -> str | None:
        """Aplica offset de tempo ao horario do sinal."""
        if not horario_str:
            return None
        from datetime import datetime as _dt, timedelta
        hoje = _dt.now().date()
        try:
            dt = _dt.strptime(f"{hoje} {horario_str}", "%Y-%m-%d %H:%M")
            dt_ajustado = dt + timedelta(minutes=self.time_offset)
            return dt_ajustado.strftime("%H:%M")
        except ValueError:
            return None

    async def _processar_mensagem(self, texto: str):
        """Processa mensagem recebida: parseia, valida e enfileira."""
        from datetime import datetime as _dt

        self.sinais_recebidos += 1

        if not texto or len(texto.strip()) < 5:
            self.sinais_descartados += 1
            return

        try:
            sinal = self._parsear_sinal(texto)
        except Exception:
            self.sinais_descartados += 1
            return

        if not sinal.get("valido"):
            self.sinais_descartados += 1
            return

        # Ajusta duracao para valor valido
        sinal["duracao"] = self._ajustar_duracao(int(sinal.get("duracao", 300)))

        # Aplica offset de tempo
        sinal["horario_original"] = sinal.get("horario")
        sinal["horario"] = self._aplicar_offset(sinal.get("horario"))

        # Metadados
        sinal["timestamp"] = _dt.now().isoformat()
        sinal["texto_original"] = texto[:200]

        self.sinais_validos += 1
        await self.fila.put(sinal)

        print(f"\n  [TELEGRAM] >>> Sinal recebido!")
        print(f"  Ativo: {sinal['ativo']} | {sinal['direcao'].upper()} | {sinal['duracao']}s")
        if sinal.get("horario"):
            h_orig = sinal.get("horario_original", "?")
            print(f"  Entrada: {sinal['horario']} (original: {h_orig}, offset: {self.time_offset}min)")
        if sinal.get("payout"):
            print(f"  Payout sinal: {sinal['payout']}%")

    async def proximo_sinal(self) -> dict:
        """Aguarda e retorna o proximo sinal valido da fila."""
        return await self.fila.get()

    def sinais_pendentes(self) -> int:
        """Retorna quantos sinais estao aguardando execucao."""
        return self.fila.qsize() if self.fila else 0

    async def escutar(self):
        """Mantém cliente ativo escutando. Chame em uma task separada."""
        await self._client_telegram.run_until_disconnected()

    def status(self) -> str:
        return (
            f"AgentTelegram | Bot: {self.bot_username} | "
            f"Offset: {self.time_offset}min | "
            f"Recebidos: {self.sinais_recebidos} | "
            f"Validos: {self.sinais_validos} | "
            f"Descartados: {self.sinais_descartados} | "
            f"Fila: {self.sinais_pendentes()}"
        )


# ============================================================
# UTILS
# ============================================================

def _serializar_content(content_blocks) -> list[dict]:
    """Converte content blocks da API em dicts serializaveis."""
    resultado = []
    for bloco in content_blocks:
        if bloco.type == "text":
            resultado.append({"type": "text", "text": bloco.text})
        elif bloco.type == "tool_use":
            resultado.append({
                "type": "tool_use",
                "id": bloco.id,
                "name": bloco.name,
                "input": bloco.input,
            })
    return resultado
