"""
Skills - funcoes utilitarias que os agentes utilizam.
Cada skill e uma funcao pura que recebe dados e retorna resultado.
"""

import json
import random
from datetime import datetime
from pathlib import Path

# Arquivo de historico de operacoes
OPERATIONS_FILE = Path(__file__).resolve().parent / "operacoes.json"


# ============================================================
# SKILLS DE ANALISE TECNICA
# ============================================================

def analisar_sentimento(texto: str) -> dict:
    """Analisa sentimento de um texto sobre mercado."""
    palavras_positivas = ["alta", "bull", "compra", "lucro", "sobe", "valoriza", "otimismo"]
    palavras_negativas = ["baixa", "bear", "venda", "perda", "cai", "desvaloriza", "pessimismo"]

    texto_lower = texto.lower()
    score_pos = sum(1 for p in palavras_positivas if p in texto_lower)
    score_neg = sum(1 for p in palavras_negativas if p in texto_lower)
    total = score_pos + score_neg

    if total == 0:
        return {"sentimento": "neutro", "confianca": 0.0}

    score = (score_pos - score_neg) / total
    sentimento = "positivo" if score > 0 else "negativo" if score < 0 else "neutro"
    return {"sentimento": sentimento, "confianca": round(abs(score), 2)}


def calcular_media_movel(precos: list[float], periodo: int = 20) -> list[float]:
    """Calcula media movel simples."""
    if len(precos) < periodo:
        return []
    return [
        round(sum(precos[i:i + periodo]) / periodo, 2)
        for i in range(len(precos) - periodo + 1)
    ]


def detectar_cruzamento(curta: list[float], longa: list[float]) -> str | None:
    """Detecta cruzamento entre duas medias moveis."""
    if len(curta) < 2 or len(longa) < 2:
        return None
    if curta[-2] <= longa[-2] and curta[-1] > longa[-1]:
        return "golden_cross"
    if curta[-2] >= longa[-2] and curta[-1] < longa[-1]:
        return "death_cross"
    return None


def calcular_rsi(precos: list[float], periodo: int = 14) -> float | None:
    """Calcula o RSI (Relative Strength Index)."""
    if len(precos) < periodo + 1:
        return None

    deltas = [precos[i + 1] - precos[i] for i in range(len(precos) - 1)]
    ganhos = [d for d in deltas[-periodo:] if d > 0]
    perdas = [-d for d in deltas[-periodo:] if d < 0]

    media_ganho = sum(ganhos) / periodo if ganhos else 0
    media_perda = sum(perdas) / periodo if perdas else 0

    if media_perda == 0:
        return 100.0
    rs = media_ganho / media_perda
    return round(100 - (100 / (1 + rs)), 2)


def gerar_sinal(rsi: float | None, cruzamento: str | None, sentimento: dict) -> dict:
    """Gera sinal de trading combinando indicadores."""
    pontos = 0
    razoes = []

    if rsi is not None:
        if rsi < 30:
            pontos += 1
            razoes.append(f"RSI sobrevendido ({rsi})")
        elif rsi > 70:
            pontos -= 1
            razoes.append(f"RSI sobrecomprado ({rsi})")

    if cruzamento == "golden_cross":
        pontos += 1
        razoes.append("Golden cross detectado")
    elif cruzamento == "death_cross":
        pontos -= 1
        razoes.append("Death cross detectado")

    if sentimento["sentimento"] == "positivo":
        pontos += 1
        razoes.append(f"Sentimento positivo ({sentimento['confianca']})")
    elif sentimento["sentimento"] == "negativo":
        pontos -= 1
        razoes.append(f"Sentimento negativo ({sentimento['confianca']})")

    if pontos >= 2:
        acao = "COMPRAR"
    elif pontos <= -2:
        acao = "VENDER"
    else:
        acao = "AGUARDAR"

    return {"acao": acao, "pontos": pontos, "razoes": razoes}


# ============================================================
# SKILLS DE OPERACAO (TRADING)
# ============================================================

def skill_read_balance() -> dict:
    """Retorna saldo simulado da conta."""
    saldo_inicial = 1000.0
    operacoes = _load_operations()

    lucro_total = sum(op.get("profit", 0) for op in operacoes)
    saldo_atual = saldo_inicial + lucro_total

    return {
        "saldo_inicial": saldo_inicial,
        "saldo_atual": round(saldo_atual, 2),
        "total_operacoes": len(operacoes),
        "lucro_acumulado": round(lucro_total, 2),
    }


def skill_calculate_mg(
    entrada: float = 10.0,
    payout: float = 0.85,
    nivel: int = 2,
    fator_correcao: float = 1.0,
) -> dict:
    """Calcula Martingale ate o nivel especificado.

    MG formula: proxima_entrada = (acumulado_perdido + lucro_desejado) / payout
    Onde lucro_desejado = entrada_original * payout * fator_correcao

    fator_correcao=1.0  -> comportamento padrao (lucro = entrada * payout)
    fator_correcao=1/payout -> correcao completa (lucro nos MGs = entrada)
    """
    lucro_desejado = entrada * payout * fator_correcao
    resultados = []
    acumulado_perdido = 0.0

    for n in range(nivel):
        if n == 0:
            valor = entrada
        else:
            valor = (acumulado_perdido + lucro_desejado) / payout

        valor = round(valor, 2)
        lucro_se_ganhar = round(valor * payout - acumulado_perdido, 2)

        resultados.append({
            "nivel": f"MG{n}" if n > 0 else "Entrada",
            "valor": valor,
            "acumulado_perdido": round(acumulado_perdido, 2),
            "lucro_se_ganhar": lucro_se_ganhar,
        })

        acumulado_perdido += valor

    return {
        "entrada_base": entrada,
        "payout": payout,
        "fator_correcao": round(fator_correcao, 4),
        "niveis": resultados,
        "perda_total_se_perder_tudo": round(acumulado_perdido, 2),
    }


def skill_execute_operation(asset: str, direction: str, amount: float) -> dict:
    """Simula execucao de operacao binaria.

    Simula resultado com 55% de chance de win (leve edge).
    """
    direction = direction.lower()
    if direction not in ("call", "put"):
        return {"success": False, "erro": "direction deve ser 'call' ou 'put'"}

    if amount <= 0:
        return {"success": False, "erro": "amount deve ser positivo"}

    payout = 0.85
    win = random.random() < 0.55
    profit = round(amount * payout, 2) if win else round(-amount, 2)

    return {
        "success": True,
        "asset": asset.upper(),
        "direction": direction,
        "amount": amount,
        "result": "WIN" if win else "LOSS",
        "profit": profit,
        "timestamp": datetime.now().isoformat(),
    }


def skill_register_operation(data: dict) -> dict:
    """Salva operacao no arquivo operacoes.json.

    Aceita campos extras como: cenario, nivel_mg, sequencia_id.
    """
    operacoes = _load_operations()

    if "timestamp" not in data:
        data["timestamp"] = datetime.now().isoformat()

    operacoes.append(data)

    try:
        with open(OPERATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(operacoes, f, ensure_ascii=False, indent=2)
        return {"success": True, "total_operacoes": len(operacoes)}
    except Exception as e:
        return {"success": False, "erro": str(e)}


# ============================================================
# SKILLS DE HISTORICO E RELATORIO
# ============================================================

def skill_read_history(limit: int = 50, asset: str | None = None) -> dict:
    """Le historico de operacoes com filtros.

    Retorna as ultimas 'limit' operacoes, opcionalmente filtradas por asset.
    """
    operacoes = _load_operations()

    if asset:
        operacoes = [op for op in operacoes if op.get("asset", "").upper() == asset.upper()]

    ultimas = operacoes[-limit:]

    return {
        "total_registros": len(operacoes),
        "exibindo": len(ultimas),
        "operacoes": ultimas,
    }


def skill_generate_report() -> dict:
    """Gera relatorio completo com estatisticas de todas as operacoes.

    Calcula: taxa de acerto, lucro total, cenarios 3, stats por asset, etc.
    """
    operacoes = _load_operations()

    if not operacoes:
        return {
            "total_operacoes": 0,
            "mensagem": "Nenhuma operacao registrada ainda.",
        }

    # --- Metricas gerais ---
    total = len(operacoes)
    wins = [op for op in operacoes if op.get("result") == "WIN"]
    losses = [op for op in operacoes if op.get("result") == "LOSS"]
    total_wins = len(wins)
    total_losses = len(losses)
    taxa_acerto = round((total_wins / total) * 100, 1) if total > 0 else 0

    lucro_total = round(sum(op.get("profit", 0) for op in operacoes), 2)
    total_ganho = round(sum(op.get("profit", 0) for op in wins), 2)
    total_perdido = round(sum(op.get("profit", 0) for op in losses), 2)

    # --- Deteccao de Cenarios 3 ---
    cenarios_3 = [op for op in operacoes if op.get("cenario") == 3]
    total_cenarios_3 = len(cenarios_3)
    perda_cenarios_3 = round(sum(op.get("profit", 0) for op in cenarios_3), 2)

    # --- Contagem por cenario ---
    cenarios = {}
    for op in operacoes:
        c = op.get("cenario")
        if c is not None:
            cenarios[c] = cenarios.get(c, 0) + 1

    # --- Stats por asset ---
    assets = {}
    for op in operacoes:
        a = op.get("asset", "UNKNOWN")
        if a not in assets:
            assets[a] = {"wins": 0, "losses": 0, "profit": 0}
        if op.get("result") == "WIN":
            assets[a]["wins"] += 1
        elif op.get("result") == "LOSS":
            assets[a]["losses"] += 1
        assets[a]["profit"] = round(assets[a]["profit"] + op.get("profit", 0), 2)

    # --- Stats por nivel MG ---
    niveis = {}
    for op in operacoes:
        n = op.get("nivel_mg", "entrada")
        if n not in niveis:
            niveis[n] = {"wins": 0, "losses": 0, "profit": 0}
        if op.get("result") == "WIN":
            niveis[n]["wins"] += 1
        elif op.get("result") == "LOSS":
            niveis[n]["losses"] += 1
        niveis[n]["profit"] = round(niveis[n]["profit"] + op.get("profit", 0), 2)

    # --- Sequencias (streaks) ---
    maior_win_streak = 0
    maior_loss_streak = 0
    win_streak = 0
    loss_streak = 0
    for op in operacoes:
        if op.get("result") == "WIN":
            win_streak += 1
            loss_streak = 0
            maior_win_streak = max(maior_win_streak, win_streak)
        elif op.get("result") == "LOSS":
            loss_streak += 1
            win_streak = 0
            maior_loss_streak = max(maior_loss_streak, loss_streak)

    # --- Saldo ---
    saldo_inicial = 1000.0
    saldo_atual = round(saldo_inicial + lucro_total, 2)

    return {
        "total_operacoes": total,
        "wins": total_wins,
        "losses": total_losses,
        "taxa_acerto_pct": taxa_acerto,
        "lucro_total": lucro_total,
        "total_ganho": total_ganho,
        "total_perdido": total_perdido,
        "saldo_inicial": saldo_inicial,
        "saldo_atual": saldo_atual,
        "cenarios": cenarios,
        "cenarios_3_total": total_cenarios_3,
        "cenarios_3_perda": perda_cenarios_3,
        "stats_por_asset": assets,
        "stats_por_nivel_mg": niveis,
        "maior_win_streak": maior_win_streak,
        "maior_loss_streak": maior_loss_streak,
    }


# ============================================================
# SKILLS DE PROTECAO
# ============================================================

ALERTS_FILE = Path(__file__).resolve().parent / "alertas.json"
SALDO_INICIAL = 1000.0  # fallback - sobrescrito por config.json


def _get_saldo_inicial() -> float:
    """Le saldo_inicial do config.json. Fallback para SALDO_INICIAL global."""
    try:
        config_path = Path(__file__).resolve().parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return float(cfg.get("saldo_inicial", SALDO_INICIAL))
    except Exception:
        return SALDO_INICIAL


def skill_check_protection(
    limite_perda_pct: float = 20.0,
    saldo_inicial: float | None = None,
    max_loss_streak: int = 5,
    saldo_atual_override: float | None = None,
) -> dict:
    """Verifica se o saldo caiu alem do limite permitido.

    Retorna pode_continuar=True/False e diagnostico completo.

    saldo_atual_override: quando informado, usa o saldo real da Quotex
    em vez de calcular pelo historico de operacoes.json.
    """
    if saldo_inicial is None:
        saldo_inicial = _get_saldo_inicial()

    operacoes = _load_operations()

    if saldo_atual_override is not None:
        # Modo Quotex: usa saldo real da corretora
        saldo_atual = saldo_atual_override
    else:
        # Modo simulacao: calcula pelo historico local
        lucro_total = sum(op.get("profit", 0) for op in operacoes)
        saldo_atual = saldo_inicial + lucro_total

    perda_pct = ((saldo_inicial - saldo_atual) / saldo_inicial) * 100 if saldo_atual < saldo_inicial else 0
    lucro_total_reais = round(saldo_atual - saldo_inicial, 2)

    # Contagem de cenarios 3
    cenarios_3 = sum(1 for op in operacoes if op.get("cenario") == 3)

    # Loss streak atual (ultimas operacoes consecutivas)
    loss_streak_atual = 0
    for op in reversed(operacoes):
        if op.get("result") == "LOSS":
            loss_streak_atual += 1
        else:
            break

    # Decisao
    pode_continuar = perda_pct < limite_perda_pct
    motivo = None

    if not pode_continuar:
        motivo = f"Perda atingiu {perda_pct:.1f}% (limite: {limite_perda_pct}%)"
    elif loss_streak_atual >= max_loss_streak:
        pode_continuar = False
        motivo = f"Loss streak de {loss_streak_atual} consecutivas (limite: {max_loss_streak})"
    elif cenarios_3 >= 3 and perda_pct > 10:
        pode_continuar = False
        motivo = f"{cenarios_3} Cenarios 3 + perda de {perda_pct:.1f}%"

    resultado = {
        "pode_continuar": pode_continuar,
        "saldo_inicial": saldo_inicial,
        "saldo_atual": round(saldo_atual, 2),
        "lucro_total_reais": lucro_total_reais,
        "perda_pct": round(perda_pct, 1),
        "limite_perda_pct": limite_perda_pct,
        "total_operacoes": len(operacoes),
        "cenarios_3": cenarios_3,
        "loss_streak_atual": loss_streak_atual,
        "max_loss_streak": max_loss_streak,
    }

    if motivo:
        resultado["motivo_bloqueio"] = motivo

    return resultado


def skill_log_alert(tipo: str, mensagem: str, dados: dict | None = None) -> dict:
    """Salva alerta no arquivo alertas.json.

    Tipos: STOP_LOSS, CENARIO_3, LOSS_STREAK, WARNING, INFO.
    """
    alertas = _load_alerts()

    alerta = {
        "tipo": tipo.upper(),
        "mensagem": mensagem,
        "timestamp": datetime.now().isoformat(),
    }
    if dados:
        alerta["dados"] = dados

    alertas.append(alerta)

    try:
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(alertas, f, ensure_ascii=False, indent=2)
        return {"success": True, "total_alertas": len(alertas)}
    except Exception as e:
        return {"success": False, "erro": str(e)}


def _load_alerts() -> list:
    """Carrega alertas do arquivo JSON."""
    if not ALERTS_FILE.exists():
        return []
    try:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


# ============================================================
# INTERNO
# ============================================================

def _load_operations() -> list:
    """Carrega operacoes do arquivo JSON."""
    if not OPERATIONS_FILE.exists():
        return []
    try:
        with open(OPERATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


# ============================================================
# TOOLS SCHEMA (para Claude tool use)
# ============================================================

TOOLS_SCHEMA = [
    # --- Analise ---
    {
        "name": "analisar_sentimento",
        "description": "Analisa o sentimento de um texto sobre mercado financeiro",
        "input_schema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string", "description": "Texto para analisar"}
            },
            "required": ["texto"],
        },
    },
    {
        "name": "calcular_indicadores",
        "description": "Calcula indicadores tecnicos (RSI, medias moveis, cruzamentos) a partir de precos",
        "input_schema": {
            "type": "object",
            "properties": {
                "precos": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Lista de precos historicos",
                },
                "periodo_curto": {"type": "integer", "default": 9},
                "periodo_longo": {"type": "integer", "default": 21},
            },
            "required": ["precos"],
        },
    },
    {
        "name": "gerar_sinal_trading",
        "description": "Gera sinal de compra/venda/aguardar com base nos indicadores e sentimento",
        "input_schema": {
            "type": "object",
            "properties": {
                "rsi": {"type": "number", "description": "Valor do RSI"},
                "cruzamento": {"type": "string", "description": "golden_cross, death_cross ou null"},
                "sentimento": {"type": "object", "description": "Resultado da analise de sentimento"},
            },
            "required": ["rsi", "cruzamento", "sentimento"],
        },
    },
    # --- Operacao ---
    {
        "name": "read_balance",
        "description": "Consulta saldo atual da conta, incluindo saldo inicial, lucro acumulado e total de operacoes",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "calculate_mg",
        "description": "Calcula tabela de Martingale. Retorna valor de cada nivel (Entrada, MG1, MG2...), acumulado perdido e lucro se ganhar em cada nivel",
        "input_schema": {
            "type": "object",
            "properties": {
                "entrada": {"type": "number", "default": 10, "description": "Valor da entrada base em R$"},
                "payout": {"type": "number", "default": 0.85, "description": "Payout da operacao (ex: 0.85 = 85%)"},
                "nivel": {"type": "integer", "default": 2, "description": "Quantos niveis de MG calcular (ex: 2 = MG1 e MG2)"},
                "fator_correcao": {"type": "number", "default": 1.0, "description": "Fator de correcao do payout. Use 1/payout para garantir lucro = entrada nos niveis MG. Padrao 1.0 = sem correcao"},
            },
        },
    },
    {
        "name": "execute_operation",
        "description": "Executa uma operacao binaria simulada. Retorna resultado (WIN/LOSS) e lucro/prejuizo",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "description": "Par de moedas (ex: EURUSD, GBPJPY)"},
                "direction": {"type": "string", "enum": ["call", "put"], "description": "Direcao: call (alta) ou put (baixa)"},
                "amount": {"type": "number", "description": "Valor da operacao em R$"},
            },
            "required": ["asset", "direction", "amount"],
        },
    },
    {
        "name": "register_operation",
        "description": "Registra uma operacao no historico (operacoes.json). Passar dados completos incluindo: asset, direction, amount, result, profit, timestamp. Campos extras recomendados: cenario (1/2/3), nivel_mg ('entrada'/'mg1'/'mg2'), sequencia_id (para agrupar entrada+MGs da mesma operacao)",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "Dados completos da operacao. Campos obrigatorios: asset, direction, amount, result, profit. Extras: cenario, nivel_mg, sequencia_id",
                },
            },
            "required": ["data"],
        },
    },
    # --- Historico e Relatorio ---
    {
        "name": "read_history",
        "description": "Le historico de operacoes do arquivo operacoes.json. Retorna as ultimas N operacoes com todos os campos (result, profit, cenario, nivel_mg, etc). Use para analisar padroes e aprender com resultados anteriores",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50, "description": "Numero maximo de operacoes a retornar"},
                "asset": {"type": "string", "description": "Filtrar por asset especifico (ex: EURUSD). Omita para ver todos"},
            },
        },
    },
    {
        "name": "generate_report",
        "description": "Gera relatorio completo de performance com: taxa de acerto (%), lucro/prejuizo total, total de Cenarios 3 detectados e sua perda, stats por asset, stats por nivel MG (entrada/mg1/mg2), maior sequencia de wins e losses, e saldo atual",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # --- Protecao ---
    {
        "name": "check_protection",
        "description": "Verifica se e seguro continuar operando. Retorna pode_continuar (true/false), perda percentual, loss streak atual, total de Cenarios 3. Bloqueia se: perda > 20%, loss streak >= 5, ou muitos Cenarios 3",
        "input_schema": {
            "type": "object",
            "properties": {
                "limite_perda_pct": {"type": "number", "default": 20, "description": "Limite maximo de perda em % do saldo inicial (padrao: 20%)"},
            },
        },
    },
    {
        "name": "log_alert",
        "description": "Registra um alerta no arquivo alertas.json. Tipos: STOP_LOSS, CENARIO_3, LOSS_STREAK, WARNING, INFO",
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["STOP_LOSS", "CENARIO_3", "LOSS_STREAK", "WARNING", "INFO"], "description": "Tipo do alerta"},
                "mensagem": {"type": "string", "description": "Descricao do alerta"},
                "dados": {"type": "object", "description": "Dados extras do alerta (saldo, perda, etc)"},
            },
            "required": ["tipo", "mensagem"],
        },
    },
]


# ============================================================
# DISPATCHER
# ============================================================

def executar_tool(nome: str, inputs: dict) -> str:
    """Executa uma tool pelo nome e retorna resultado JSON."""

    # --- Analise ---
    if nome == "analisar_sentimento":
        resultado = analisar_sentimento(inputs["texto"])

    elif nome == "calcular_indicadores":
        precos = inputs["precos"]
        pc = inputs.get("periodo_curto", 9)
        pl = inputs.get("periodo_longo", 21)
        mm_curta = calcular_media_movel(precos, pc)
        mm_longa = calcular_media_movel(precos, pl)
        rsi = calcular_rsi(precos)
        cruzamento = detectar_cruzamento(mm_curta, mm_longa)
        resultado = {
            "rsi": rsi,
            "media_movel_curta": mm_curta[-3:] if mm_curta else [],
            "media_movel_longa": mm_longa[-3:] if mm_longa else [],
            "cruzamento": cruzamento,
        }

    elif nome == "gerar_sinal_trading":
        resultado = gerar_sinal(
            inputs.get("rsi"),
            inputs.get("cruzamento"),
            inputs.get("sentimento", {"sentimento": "neutro", "confianca": 0}),
        )

    # --- Operacao ---
    elif nome == "read_balance":
        resultado = skill_read_balance()

    elif nome == "calculate_mg":
        resultado = skill_calculate_mg(
            entrada=inputs.get("entrada", 10.0),
            payout=inputs.get("payout", 0.85),
            nivel=inputs.get("nivel", 2),
            fator_correcao=inputs.get("fator_correcao", 1.0),
        )

    elif nome == "execute_operation":
        resultado = skill_execute_operation(
            asset=inputs["asset"],
            direction=inputs["direction"],
            amount=inputs["amount"],
        )

    elif nome == "register_operation":
        resultado = skill_register_operation(inputs["data"])

    # --- Historico e Relatorio ---
    elif nome == "read_history":
        resultado = skill_read_history(
            limit=inputs.get("limit", 50),
            asset=inputs.get("asset"),
        )

    elif nome == "generate_report":
        resultado = skill_generate_report()

    # --- Protecao ---
    elif nome == "check_protection":
        resultado = skill_check_protection(
            limite_perda_pct=inputs.get("limite_perda_pct", 20.0),
        )

    elif nome == "log_alert":
        resultado = skill_log_alert(
            tipo=inputs["tipo"],
            mensagem=inputs["mensagem"],
            dados=inputs.get("dados"),
        )

    else:
        resultado = {"erro": f"Tool '{nome}' nao encontrada"}

    return json.dumps(resultado, ensure_ascii=False)
