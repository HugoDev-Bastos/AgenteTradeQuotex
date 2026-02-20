"""
Estrategias de trading para o Loop Autonomo.

Interface de uma estrategia:
    func(candles: list[dict], cfg: dict) -> dict

    Retorno obrigatorio:
        {
            "sinal":       "call" | "put" | None,  # None = sem sinal, aguarda proximo candle
            "motivo":      str,                     # descricao da decisao (exibida no terminal)
            "indicadores": dict,                    # valores calculados (ex: {"RSI": 28.5, "EMA9": 1.085})
        }

Formato dos candles (Quotex via get_candles):
    [{"open": float, "close": float, "high": float, "low": float, "time": int}, ...]
    Ordenados do mais ANTIGO ao mais RECENTE.
    O ultimo elemento [-1] e o candle mais recente (fechado).

Para adicionar uma nova estrategia:
    1. Implemente a funcao seguindo a interface acima
    2. Registre no dicionario ESTRATEGIAS com um nome unico
    3. O Loop Autonomo vai exibi-la automaticamente na lista de selecao

Exemplo de estrategia minima:
    def minha_estrategia(candles, cfg):
        closes = [c["close"] for c in candles]
        rsi = calcular_rsi(closes)
        sinal = "call" if rsi < 30 else "put" if rsi > 70 else None
        return {"sinal": sinal, "motivo": f"RSI={rsi}", "indicadores": {"RSI": rsi}}
"""


# ============================================================
# FUNCOES AUXILIARES
# ============================================================

def calcular_ema(precos: list[float], periodo: int) -> list[float]:
    """Calcula EMA (Exponential Moving Average) para uma lista de precos.

    Retorna lista de floats do mesmo tamanho que `precos`.
    Os primeiros `periodo-1` valores sao preenchidos com None.
    """
    if len(precos) < periodo:
        return [None] * len(precos)

    k = 2.0 / (periodo + 1)
    emas = [None] * (periodo - 1)

    # Primeira EMA = media simples dos primeiros `periodo` valores
    sma_inicial = sum(precos[:periodo]) / periodo
    emas.append(sma_inicial)

    for preco in precos[periodo:]:
        ema_anterior = emas[-1]
        emas.append(preco * k + ema_anterior * (1 - k))

    return emas


def calcular_sma(precos: list[float], periodo: int) -> list[float]:
    """Calcula SMA (Simple Moving Average) para uma lista de precos.

    Retorna lista de floats do mesmo tamanho que `precos`.
    Os primeiros `periodo-1` valores sao preenchidos com None.
    """
    if len(precos) < periodo:
        return [None] * len(precos)

    smas = [None] * (periodo - 1)
    for i in range(periodo - 1, len(precos)):
        smas.append(sum(precos[i - periodo + 1 : i + 1]) / periodo)
    return smas


def calcular_wma(valores: list[float], periodo: int) -> list[float]:
    """Calcula WMA (Weighted Moving Average) para uma lista de valores.

    Pesos lineares: o valor mais recente tem peso `periodo`, o mais antigo peso 1.
    Retorna lista do mesmo tamanho. Os primeiros `periodo-1` valores sao None.
    Ignora entradas None na lista de entrada.
    """
    pesos = list(range(1, periodo + 1))
    soma_pesos = sum(pesos)
    resultado = [None] * (periodo - 1)

    for i in range(periodo - 1, len(valores)):
        janela = valores[i - periodo + 1 : i + 1]
        if any(v is None for v in janela):
            resultado.append(None)
        else:
            resultado.append(sum(v * p for v, p in zip(janela, pesos)) / soma_pesos)
    return resultado


def calcular_rsi(precos: list[float], periodo: int = 14) -> list[float]:
    """Calcula RSI (Relative Strength Index) para uma lista de precos.

    Retorna lista de floats do mesmo tamanho que `precos`.
    Os primeiros `periodo` valores sao preenchidos com None.
    """
    if len(precos) < periodo + 1:
        return [None] * len(precos)

    rsi_values = [None] * periodo

    # Calcula variacoes
    deltas = [precos[i] - precos[i - 1] for i in range(1, len(precos))]

    # Primeiras medias de ganho e perda (media simples)
    ganhos = [d if d > 0 else 0.0 for d in deltas[:periodo]]
    perdas = [abs(d) if d < 0 else 0.0 for d in deltas[:periodo]]

    media_ganho = sum(ganhos) / periodo
    media_perda = sum(perdas) / periodo

    if media_perda == 0:
        rsi_values.append(100.0)
    else:
        rs = media_ganho / media_perda
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    # Metodo Wilder (media suavizada)
    for delta in deltas[periodo:]:
        ganho = delta if delta > 0 else 0.0
        perda = abs(delta) if delta < 0 else 0.0
        media_ganho = (media_ganho * (periodo - 1) + ganho) / periodo
        media_perda = (media_perda * (periodo - 1) + perda) / periodo

        if media_perda == 0:
            rsi_values.append(100.0)
        else:
            rs = media_ganho / media_perda
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


# ============================================================
# ESTRATEGIA PLACEHOLDER
# ============================================================

def estrategia_nenhuma(candles: list[dict], cfg: dict) -> dict:
    """Placeholder: nenhuma estrategia ativa. Nunca gera sinal.

    O loop roda normalmente (protecao, relatorio, etc.) mas nao executa trades.
    Util para testar a infraestrutura do Loop Autonomo.
    """
    return {
        "sinal": None,
        "motivo": "Nenhuma estrategia configurada",
        "indicadores": {},
    }


# ============================================================
# ESTRATEGIA EMA 9/21 + RSI(14)
# ============================================================

def estrategia_ema_rsi(candles: list[dict], cfg: dict) -> dict:
    """EMA 9/21 crossover + RSI(14) + confirmacao de cor da vela. OTC M1.

    CALL quando:
        - EMA9 cruza para CIMA da EMA21 (candle anterior estava abaixo, atual esta acima)
        - RSI abaixo de 30 E subindo (rsi[-1] > rsi[-2])
        - Vela atual de alta (close > open)

    PUT quando:
        - EMA9 cruza para BAIXO da EMA21 (candle anterior estava acima, atual esta abaixo)
        - RSI acima de 70 E caindo (rsi[-1] < rsi[-2])
        - Vela atual de baixa (close < open)

    Requer pelo menos 30 candles para calculo estavel.
    """
    MIN_CANDLES = 30

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes = [c["close"] for c in candles]
    ema9  = calcular_ema(closes, 9)
    ema21 = calcular_ema(closes, 21)
    rsi   = calcular_rsi(closes, 14)

    # Verifica se os valores necessarios estao disponiveis
    if any(v is None for v in [ema9[-1], ema9[-2], ema21[-1], ema21[-2], rsi[-1], rsi[-2]]):
        return {
            "sinal": None,
            "motivo": "Indicadores ainda aquecendo",
            "indicadores": {},
        }

    vela_atual   = candles[-1]
    ema9_atual   = ema9[-1]
    ema9_ant     = ema9[-2]
    ema21_atual  = ema21[-1]
    ema21_ant    = ema21[-2]
    rsi_atual    = rsi[-1]
    rsi_ant      = rsi[-2]

    vela_verde   = vela_atual["close"] > vela_atual["open"]
    vela_vermelha = vela_atual["close"] < vela_atual["open"]

    cruzou_acima = (ema9_ant <= ema21_ant) and (ema9_atual > ema21_atual)
    cruzou_abaixo = (ema9_ant >= ema21_ant) and (ema9_atual < ema21_atual)

    indicadores = {
        "EMA9":  round(ema9_atual, 5),
        "EMA21": round(ema21_atual, 5),
        "RSI":   round(rsi_atual, 2),
    }

    # --- CALL ---
    if cruzou_acima and rsi_atual < 30 and rsi_atual > rsi_ant and vela_verde:
        return {
            "sinal": "call",
            "motivo": (
                f"EMA9 cruzou acima EMA21 | RSI={rsi_atual:.1f} subindo | vela verde"
            ),
            "indicadores": indicadores,
        }

    # --- PUT ---
    if cruzou_abaixo and rsi_atual > 70 and rsi_atual < rsi_ant and vela_vermelha:
        return {
            "sinal": "put",
            "motivo": (
                f"EMA9 cruzou abaixo EMA21 | RSI={rsi_atual:.1f} caindo | vela vermelha"
            ),
            "indicadores": indicadores,
        }

    # Sem sinal: mostra estado atual para monitoramento
    posicao = "EMA9>EMA21" if ema9_atual > ema21_atual else "EMA9<EMA21"
    return {
        "sinal": None,
        "motivo": f"Sem cruzamento confirmado | {posicao} | RSI={rsi_atual:.1f}",
        "indicadores": indicadores,
    }


# ============================================================
# ESTRATEGIA PROFITX_E1 — Tendencia + Corpo + Consolidacao
# ============================================================

def estrategia_profitx_e1(candles: list[dict], cfg: dict) -> dict:
    """ProfitX E1: reversao confirmada por tendencia (SMA5/21), forca do corpo e movimento.

    CALL quando (vela atual = candles[-1], anterior = candles[-2]):
        - Vela anterior vermelha  E  vela atual verde (reversao)
        - close atual > close anterior
        - close > SMA5 > SMA21                (tendencia de alta)
        - corpo atual > media_corpo(5)         (vela com forca)
        - range(3 velas) > 50% do range medio (mercado em movimento)

    PUT: condicoes inversas.
    Requer pelo menos 30 candles.
    """
    MIN_CANDLES = 30

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes = [c["close"] for c in candles]
    opens  = [c["open"]  for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    corpos = [abs(c - o) for c, o in zip(closes, opens)]
    ranges = [h - l for h, l in zip(highs, lows)]

    sma5  = calcular_sma(closes, 5)
    sma21 = calcular_sma(closes, 21)
    avg_corpo = calcular_sma(corpos, 5)
    avg_range = calcular_sma(ranges, 5)

    if any(v is None for v in [sma5[-1], sma21[-1], avg_corpo[-1], avg_range[-1]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    vela_0 = candles[-1]   # atual
    vela_1 = candles[-2]   # anterior

    close_0  = vela_0["close"]
    open_0   = vela_0["open"]
    close_1  = vela_1["close"]
    open_1   = vela_1["open"]
    corpo_0  = abs(close_0 - open_0)

    verde_0    = close_0 > open_0
    vermelho_0 = close_0 < open_0
    verde_1    = close_1 > open_1
    vermelho_1 = close_1 < open_1

    range_3 = max(highs[-3:]) - min(lows[-3:])
    consolidado = range_3 < (avg_range[-1] * 0.5)

    indicadores = {
        "SMA5":       round(sma5[-1], 5),
        "SMA21":      round(sma21[-1], 5),
        "Corpo":      round(corpo_0, 5),
        "AvgCorpo":   round(avg_corpo[-1], 5),
        "Consolidado": consolidado,
    }

    # --- CALL ---
    if (vermelho_1 and verde_0
            and close_0 > close_1
            and close_0 > sma5[-1] > sma21[-1]
            and corpo_0 > avg_corpo[-1]
            and not consolidado):
        return {
            "sinal": "call",
            "motivo": f"Reversao alta | SMA5={sma5[-1]:.5f}>SMA21={sma21[-1]:.5f} | corpo forte | movimento ok",
            "indicadores": indicadores,
        }

    # --- PUT ---
    if (verde_1 and vermelho_0
            and close_0 < close_1
            and close_0 < sma5[-1] < sma21[-1]
            and corpo_0 > avg_corpo[-1]
            and not consolidado):
        return {
            "sinal": "put",
            "motivo": f"Reversao baixa | SMA5={sma5[-1]:.5f}<SMA21={sma21[-1]:.5f} | corpo forte | movimento ok",
            "indicadores": indicadores,
        }

    tendencia = "ALTA" if sma5[-1] > sma21[-1] else "BAIXA"
    return {
        "sinal": None,
        "motivo": f"Sem reversao | Tendencia {tendencia} | {'CONSOLIDADO' if consolidado else 'movendo'}",
        "indicadores": indicadores,
    }


# ============================================================
# ESTRATEGIA PROFITX_FRACTAL — Fractal + MACD simplificado
# ============================================================

def estrategia_profitx_fractal(candles: list[dict], cfg: dict) -> dict:
    """ProfitX Fractal: cruzamento de MACD simplificado confirmado por fractal de 3 velas.

    MACD simplificado:
        buffer1 = close - SMA(close, 34)
        buffer2 = WMA(buffer1, 5)           <- linha de sinal

    CALL quando:
        - buffer1 cruzou acima buffer2 (buffer1[-1] > buffer2[-1] E buffer1[-2] <= buffer2[-2])
        - Fractal de fundo na vela [-2]: low[-2] e o minimo de 3 velas consecutivas

    PUT quando:
        - buffer1 cruzou abaixo buffer2
        - Fractal de topo na vela [-2]: high[-2] e o maximo de 3 velas consecutivas

    Requer pelo menos 45 candles.
    """
    MIN_CANDLES = 45

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]

    sma34   = calcular_sma(closes, 34)
    buffer1 = [
        (closes[i] - sma34[i]) if sma34[i] is not None else None
        for i in range(len(closes))
    ]
    buffer2 = calcular_wma(buffer1, 5)

    if any(v is None for v in [buffer1[-1], buffer1[-2], buffer2[-1], buffer2[-2]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    # Cruzamento do MACD mini
    cruzou_acima  = buffer1[-2] <= buffer2[-2] and buffer1[-1] > buffer2[-1]
    cruzou_abaixo = buffer1[-2] >= buffer2[-2] and buffer1[-1] < buffer2[-1]

    # Fractal de 3 velas centrado em candles[-2]
    fractal_fundo = lows[-2]  < lows[-1]  and lows[-2]  < lows[-3]
    fractal_topo  = highs[-2] > highs[-1] and highs[-2] > highs[-3]

    indicadores = {
        "Buffer1": round(buffer1[-1], 5),
        "Buffer2": round(buffer2[-1], 5),
        "FractalFundo": fractal_fundo,
        "FractalTopo":  fractal_topo,
    }

    # --- CALL ---
    if cruzou_acima and fractal_fundo:
        return {
            "sinal": "call",
            "motivo": f"MACD cruzou acima | Fractal fundo confirmado",
            "indicadores": indicadores,
        }

    # --- PUT ---
    if cruzou_abaixo and fractal_topo:
        return {
            "sinal": "put",
            "motivo": f"MACD cruzou abaixo | Fractal topo confirmado",
            "indicadores": indicadores,
        }

    posicao = "B1>B2" if buffer1[-1] > buffer2[-1] else "B1<B2"
    return {
        "sinal": None,
        "motivo": f"Sem cruzamento+fractal | {posicao}",
        "indicadores": indicadores,
    }


# ============================================================
# ESTRATEGIA PROFITX_RESTRITO — Proxima vela, maxima restricao
# ============================================================

def estrategia_profitx_restrito(candles: list[dict], cfg: dict) -> dict:
    """ProfitX Restrito: MACD simplificado + corpo + RSI(14) + movimento. Entrada na proxima vela.

    MACD simplificado (igual ao PROFITX_FRACTAL):
        buffer1 = close - SMA(close, 34)
        buffer2 = WMA(buffer1, 5)

    CALL quando (todos os 4 filtros verdadeiros):
        1. buffer1 cruzou acima buffer2 (MACD mini em alta)
        2. corpo da vela atual > media_corpo(5)   (vela com forca)
        3. RSI(14) > 50                           (momento de alta)
        4. range(3 velas) > 50% do range medio(5) (mercado em movimento)

    PUT: condicoes inversas (RSI < 50).
    Requer pelo menos 45 candles.
    """
    MIN_CANDLES = 45

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes = [c["close"] for c in candles]
    opens  = [c["open"]  for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    corpos = [abs(c - o) for c, o in zip(closes, opens)]
    ranges = [h - l for h, l in zip(highs, lows)]

    sma34     = calcular_sma(closes, 34)
    avg_corpo = calcular_sma(corpos, 5)
    avg_range = calcular_sma(ranges, 5)
    rsi_vals  = calcular_rsi(closes, 14)

    buffer1 = [
        (closes[i] - sma34[i]) if sma34[i] is not None else None
        for i in range(len(closes))
    ]
    buffer2 = calcular_wma(buffer1, 5)

    if any(v is None for v in [
        buffer1[-1], buffer1[-2], buffer2[-1], buffer2[-2],
        avg_corpo[-1], avg_range[-1], rsi_vals[-1],
    ]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    cruzou_acima  = buffer1[-2] <= buffer2[-2] and buffer1[-1] > buffer2[-1]
    cruzou_abaixo = buffer1[-2] >= buffer2[-2] and buffer1[-1] < buffer2[-1]

    corpo_0  = corpos[-1]
    rsi_0    = rsi_vals[-1]
    range_3  = max(highs[-3:]) - min(lows[-3:])
    com_forca    = corpo_0 > avg_corpo[-1]
    em_movimento = range_3 > avg_range[-1] * 0.5

    indicadores = {
        "Buffer1":  round(buffer1[-1], 5),
        "Buffer2":  round(buffer2[-1], 5),
        "RSI":      round(rsi_0, 2),
        "Corpo":    round(corpo_0, 5),
        "AvgCorpo": round(avg_corpo[-1], 5),
    }

    # --- CALL ---
    if cruzou_acima and com_forca and rsi_0 > 50 and em_movimento:
        return {
            "sinal": "call",
            "motivo": (
                f"MACD acima | RSI={rsi_0:.1f}>50 | corpo forte | movimento ok"
            ),
            "indicadores": indicadores,
        }

    # --- PUT ---
    if cruzou_abaixo and com_forca and rsi_0 < 50 and em_movimento:
        return {
            "sinal": "put",
            "motivo": (
                f"MACD abaixo | RSI={rsi_0:.1f}<50 | corpo forte | movimento ok"
            ),
            "indicadores": indicadores,
        }

    posicao = "B1>B2" if buffer1[-1] > buffer2[-1] else "B1<B2"
    filtros_ok = f"corpo={'ok' if com_forca else 'fraco'} | mov={'ok' if em_movimento else 'consolid'} | RSI={rsi_0:.1f}"
    return {
        "sinal": None,
        "motivo": f"Sem sinal | {posicao} | {filtros_ok}",
        "indicadores": indicadores,
    }


# ============================================================
# REGISTRO DE ESTRATEGIAS
# Adicione novas estrategias aqui (nome -> funcao).
# O Loop Autonomo le este dicionario automaticamente.
# ============================================================

ESTRATEGIAS: dict = {
    "NENHUMA":          estrategia_nenhuma,
    "EMA_RSI":          estrategia_ema_rsi,
    "PROFITX_E1":       estrategia_profitx_e1,
    "PROFITX_FRACTAL":  estrategia_profitx_fractal,
    "PROFITX_RESTRITO": estrategia_profitx_restrito,
}


# ============================================================
# METADADOS DAS ESTRATEGIAS
# Informacoes exibidas na selecao do Loop Autonomo.
#
# timeframe_rec : duracao em segundos recomendada (None = sem preferencia)
# descricao     : resumo exibido na lista de selecao
# ============================================================

ESTRATEGIAS_META: dict = {
    "NENHUMA": {
        "timeframe_rec": None,
        "descricao": "Sem estrategia - loop roda mas nao opera",
    },
    "EMA_RSI": {
        "timeframe_rec": 60,
        "descricao": "EMA 9/21 + RSI(14) | Reversao de tendencia | Recomendado: M1 (60s)",
    },
    "PROFITX_E1": {
        "timeframe_rec": 60,
        "descricao": "ProfitX E1 | Reversao + SMA5/21 + corpo + consolidacao | M1",
    },
    "PROFITX_FRACTAL": {
        "timeframe_rec": 60,
        "descricao": "ProfitX Fractal | MACD simplificado + fractal de 3 velas | M1",
    },
    "PROFITX_RESTRITO": {
        "timeframe_rec": 60,
        "descricao": "ProfitX Restrito | MACD + corpo + RSI + movimento | M1 (mais seletivo)",
    },
}


# ============================================================
# EXECUTOR
# ============================================================

def executar_estrategia(nome: str, candles: list[dict], cfg: dict) -> dict:
    """Executa a estrategia pelo nome registrado.

    Retorna {"sinal": None, "motivo": ..., "indicadores": {}} em caso de erro.
    """
    func = ESTRATEGIAS.get(nome)
    if func is None:
        return {
            "sinal": None,
            "motivo": f"Estrategia '{nome}' nao encontrada",
            "indicadores": {},
        }
    try:
        resultado = func(candles, cfg)
        # Garante que o retorno tem os campos obrigatorios
        resultado.setdefault("sinal", None)
        resultado.setdefault("motivo", "")
        resultado.setdefault("indicadores", {})
        return resultado
    except Exception as e:
        return {
            "sinal": None,
            "motivo": f"Erro na estrategia '{nome}': {e}",
            "indicadores": {},
        }
