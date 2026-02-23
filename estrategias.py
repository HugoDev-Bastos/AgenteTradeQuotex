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
        closes = pd.Series([c["close"] for c in candles])
        rsi = ta.rsi(closes, length=14)
        val = rsi.iloc[-1]
        if pd.isna(val):
            return {"sinal": None, "motivo": "dados insuficientes", "indicadores": {}}
        sinal = "call" if val < 30 else "put" if val > 70 else None
        return {"sinal": sinal, "motivo": f"RSI={val:.1f}", "indicadores": {"RSI": val}}
"""

import pandas as pd
import pandas_ta as ta


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

    closes = pd.Series([c["close"] for c in candles])
    ema9  = ta.ema(closes, length=9)
    ema21 = ta.ema(closes, length=21)
    rsi   = ta.rsi(closes, length=14)

    if any(pd.isna(v) for v in [ema9.iloc[-1], ema9.iloc[-2], ema21.iloc[-1], ema21.iloc[-2], rsi.iloc[-1], rsi.iloc[-2]]):
        return {
            "sinal": None,
            "motivo": "Indicadores ainda aquecendo",
            "indicadores": {},
        }

    vela_atual   = candles[-1]
    ema9_atual   = ema9.iloc[-1]
    ema9_ant     = ema9.iloc[-2]
    ema21_atual  = ema21.iloc[-1]
    ema21_ant    = ema21.iloc[-2]
    rsi_atual    = rsi.iloc[-1]
    rsi_ant      = rsi.iloc[-2]

    vela_verde    = vela_atual["close"] > vela_atual["open"]
    vela_vermelha = vela_atual["close"] < vela_atual["open"]

    cruzou_acima  = (ema9_ant <= ema21_ant) and (ema9_atual > ema21_atual)
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
# ESTRATEGIA REVERSAO_SMA_M — Reversao + SMA5/21 + Corpo + Movimento
# ============================================================

def estrategia_reversao_sma_m(candles: list[dict], cfg: dict) -> dict:
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

    closes_s  = pd.Series(closes)
    corpos_s  = pd.Series(corpos)
    ranges_s  = pd.Series(ranges)

    sma5      = ta.sma(closes_s, length=5)
    sma21     = ta.sma(closes_s, length=21)
    avg_corpo = ta.sma(corpos_s, length=5)
    avg_range = ta.sma(ranges_s, length=5)

    if any(pd.isna(v) for v in [sma5.iloc[-1], sma21.iloc[-1], avg_corpo.iloc[-1], avg_range.iloc[-1]]):
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

    range_3    = max(highs[-3:]) - min(lows[-3:])
    consolidado = range_3 < (avg_range.iloc[-1] * 0.5)

    indicadores = {
        "SMA5":        round(sma5.iloc[-1],       5),
        "SMA21":       round(sma21.iloc[-1],      5),
        "Corpo":       round(corpo_0,             5),
        "AvgCorpo":    round(avg_corpo.iloc[-1],  5),
        "Consolidado": consolidado,
    }

    # --- CALL ---
    if (vermelho_1 and verde_0
            and close_0 > close_1
            and close_0 > sma5.iloc[-1] > sma21.iloc[-1]
            and corpo_0 > avg_corpo.iloc[-1]
            and not consolidado):
        return {
            "sinal": "call",
            "motivo": f"Reversao alta | SMA5={sma5.iloc[-1]:.5f}>SMA21={sma21.iloc[-1]:.5f} | corpo forte | movimento ok",
            "indicadores": indicadores,
        }

    # --- PUT ---
    if (verde_1 and vermelho_0
            and close_0 < close_1
            and close_0 < sma5.iloc[-1] < sma21.iloc[-1]
            and corpo_0 > avg_corpo.iloc[-1]
            and not consolidado):
        return {
            "sinal": "put",
            "motivo": f"Reversao baixa | SMA5={sma5.iloc[-1]:.5f}<SMA21={sma21.iloc[-1]:.5f} | corpo forte | movimento ok",
            "indicadores": indicadores,
        }

    tendencia = "ALTA" if sma5.iloc[-1] > sma21.iloc[-1] else "BAIXA"
    return {
        "sinal": None,
        "motivo": f"Sem reversao | Tendencia {tendencia} | {'CONSOLIDADO' if consolidado else 'movendo'}",
        "indicadores": indicadores,
    }


# ============================================================
# ESTRATEGIA FRACTAL_MACD_M — Fractal de 3 velas + MACD simplificado
# ============================================================

def estrategia_fractal_macd_m(candles: list[dict], cfg: dict) -> dict:
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

    closes_s = pd.Series([c["close"] for c in candles])
    highs    = [c["high"] for c in candles]
    lows     = [c["low"]  for c in candles]

    sma34   = ta.sma(closes_s, length=34)
    buffer1 = closes_s - sma34          # pd.Series, NaN onde sma34 e NaN
    buffer2 = ta.wma(buffer1, length=5) # pd.Series

    if any(pd.isna(v) for v in [buffer1.iloc[-1], buffer1.iloc[-2], buffer2.iloc[-1], buffer2.iloc[-2]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    # Cruzamento do MACD mini
    cruzou_acima  = buffer1.iloc[-2] <= buffer2.iloc[-2] and buffer1.iloc[-1] > buffer2.iloc[-1]
    cruzou_abaixo = buffer1.iloc[-2] >= buffer2.iloc[-2] and buffer1.iloc[-1] < buffer2.iloc[-1]

    # Fractal de 3 velas centrado em candles[-2]
    fractal_fundo = lows[-2]  < lows[-1]  and lows[-2]  < lows[-3]
    fractal_topo  = highs[-2] > highs[-1] and highs[-2] > highs[-3]

    indicadores = {
        "Buffer1":     round(buffer1.iloc[-1], 5),
        "Buffer2":     round(buffer2.iloc[-1], 5),
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

    posicao = "B1>B2" if buffer1.iloc[-1] > buffer2.iloc[-1] else "B1<B2"
    return {
        "sinal": None,
        "motivo": f"Sem cruzamento+fractal | {posicao}",
        "indicadores": indicadores,
    }


# ============================================================
# ESTRATEGIA MACD_RSI_C — MACD + Corpo + RSI + Movimento (4 filtros)
# ============================================================

def estrategia_macd_rsi_c(candles: list[dict], cfg: dict) -> dict:
    """ProfitX Restrito: MACD simplificado + corpo + RSI(14) + movimento. Entrada na proxima vela.

    MACD simplificado (igual ao FRACTAL_MACD_M):
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

    closes_s  = pd.Series(closes)
    corpos_s  = pd.Series(corpos)
    ranges_s  = pd.Series(ranges)

    sma34     = ta.sma(closes_s, length=34)
    buffer1   = closes_s - sma34
    buffer2   = ta.wma(buffer1, length=5)
    avg_corpo = ta.sma(corpos_s, length=5)
    avg_range = ta.sma(ranges_s, length=5)
    rsi_vals  = ta.rsi(closes_s, length=14)

    if any(pd.isna(v) for v in [
        buffer1.iloc[-1], buffer1.iloc[-2], buffer2.iloc[-1], buffer2.iloc[-2],
        avg_corpo.iloc[-1], avg_range.iloc[-1], rsi_vals.iloc[-1],
    ]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    cruzou_acima  = buffer1.iloc[-2] <= buffer2.iloc[-2] and buffer1.iloc[-1] > buffer2.iloc[-1]
    cruzou_abaixo = buffer1.iloc[-2] >= buffer2.iloc[-2] and buffer1.iloc[-1] < buffer2.iloc[-1]

    corpo_0      = corpos[-1]
    rsi_0        = rsi_vals.iloc[-1]
    range_3      = max(highs[-3:]) - min(lows[-3:])
    com_forca    = corpo_0 > avg_corpo.iloc[-1]
    em_movimento = range_3 > avg_range.iloc[-1] * 0.5

    indicadores = {
        "Buffer1":  round(buffer1.iloc[-1],  5),
        "Buffer2":  round(buffer2.iloc[-1],  5),
        "RSI":      round(rsi_0,             2),
        "Corpo":    round(corpo_0,           5),
        "AvgCorpo": round(avg_corpo.iloc[-1], 5),
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

    posicao   = "B1>B2" if buffer1.iloc[-1] > buffer2.iloc[-1] else "B1<B2"
    filtros_ok = f"corpo={'ok' if com_forca else 'fraco'} | mov={'ok' if em_movimento else 'consolid'} | RSI={rsi_0:.1f}"
    return {
        "sinal": None,
        "motivo": f"Sem sinal | {posicao} | {filtros_ok}",
        "indicadores": indicadores,
    }


# ============================================================
# BB_RSI_C — Bollinger Bands + RSI | Conservador | M5/M15
# ============================================================

def estrategia_bb_rsi_c(candles: list[dict], cfg: dict) -> dict:
    """BB_RSI_C: rebote nas Bandas de Bollinger(20,2) confirmado pelo RSI(14). Conservador.

    CALL quando:
        - close atual <= banda inferior (preco na/abaixo da BB inferior)
        - RSI < 35 (sobrevenda) E subindo (rsi[-1] > rsi[-2])

    PUT quando:
        - close atual >= banda superior (preco na/acima da BB superior)
        - RSI > 65 (sobrecompra) E caindo (rsi[-1] < rsi[-2])

    Requer pelo menos 35 candles.
    """
    MIN_CANDLES = 35

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    bb_df    = ta.bbands(closes_s, length=20, std=2.0)
    rsi      = ta.rsi(closes_s, length=14)

    bb_upper = bb_df["BBU_20_2.0_2.0"]
    bb_mid   = bb_df["BBM_20_2.0_2.0"]
    bb_lower = bb_df["BBL_20_2.0_2.0"]

    if any(pd.isna(v) for v in [bb_upper.iloc[-1], bb_lower.iloc[-1], bb_mid.iloc[-1], rsi.iloc[-1], rsi.iloc[-2]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    close_atual = closes_s.iloc[-1]
    rsi_atual   = rsi.iloc[-1]
    rsi_ant     = rsi.iloc[-2]

    indicadores = {
        "BB_sup": round(bb_upper.iloc[-1], 5),
        "BB_mid": round(bb_mid.iloc[-1],   5),
        "BB_inf": round(bb_lower.iloc[-1], 5),
        "RSI":    round(rsi_atual,         2),
    }

    if close_atual <= bb_lower.iloc[-1] and rsi_atual < 35 and rsi_atual > rsi_ant:
        return {
            "sinal": "call",
            "motivo": f"Preco na BB inferior | RSI={rsi_atual:.1f} subindo",
            "indicadores": indicadores,
        }

    if close_atual >= bb_upper.iloc[-1] and rsi_atual > 65 and rsi_atual < rsi_ant:
        return {
            "sinal": "put",
            "motivo": f"Preco na BB superior | RSI={rsi_atual:.1f} caindo",
            "indicadores": indicadores,
        }

    pos = "inf" if close_atual < bb_mid.iloc[-1] else "sup"
    return {
        "sinal": None,
        "motivo": f"Sem sinal | BB_pos={pos} | RSI={rsi_atual:.1f}",
        "indicadores": indicadores,
    }


# ============================================================
# EMA_RSI_C — EMA(50) filtro de tendencia + RSI pullback | Conservador | M5/M15
# ============================================================

def estrategia_ema_rsi_c(candles: list[dict], cfg: dict) -> dict:
    """EMA_RSI_C: EMA(50) como filtro de tendencia + RSI(14) identificando pullback. Conservador.

    CALL quando:
        - close > EMA50 (tendencia de alta confirmada)
        - RSI anterior (rsi[-2]) < 40 (pullback entrou em sobrevenda)
        - RSI atual (rsi[-1]) > RSI anterior (RSI recuperando = fim do pullback)

    PUT quando:
        - close < EMA50 (tendencia de baixa confirmada)
        - RSI anterior (rsi[-2]) > 60 (pullback entrou em sobrecompra)
        - RSI atual (rsi[-1]) < RSI anterior (RSI caindo = fim do pullback)

    Requer pelo menos 60 candles.
    """
    MIN_CANDLES = 60

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    ema50    = ta.ema(closes_s, length=50)
    rsi      = ta.rsi(closes_s, length=14)

    if any(pd.isna(v) for v in [ema50.iloc[-1], rsi.iloc[-1], rsi.iloc[-2]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    close_atual = closes_s.iloc[-1]
    ema_atual   = ema50.iloc[-1]
    rsi_atual   = rsi.iloc[-1]
    rsi_ant     = rsi.iloc[-2]

    indicadores = {
        "EMA50":       round(ema_atual,  5),
        "RSI":         round(rsi_atual,  2),
        "RSI_ant":     round(rsi_ant,    2),
        "Preco_EMA50": "acima" if close_atual > ema_atual else "abaixo",
    }

    if close_atual > ema_atual and rsi_ant < 40 and rsi_atual > rsi_ant:
        return {
            "sinal": "call",
            "motivo": f"Tendencia ALTA (EMA50) | RSI={rsi_atual:.1f} recuperando de {rsi_ant:.1f}",
            "indicadores": indicadores,
        }

    if close_atual < ema_atual and rsi_ant > 60 and rsi_atual < rsi_ant:
        return {
            "sinal": "put",
            "motivo": f"Tendencia BAIXA (EMA50) | RSI={rsi_atual:.1f} caindo de {rsi_ant:.1f}",
            "indicadores": indicadores,
        }

    tend = "ALTA" if close_atual > ema_atual else "BAIXA"
    return {
        "sinal": None,
        "motivo": f"Sem pullback confirmado | Tendencia {tend} | RSI={rsi_atual:.1f}",
        "indicadores": indicadores,
    }


# ============================================================
# TRIPLE_CONFIRM_C — RSI + MACD + BB triple confirmacao | Conservador | M15/M30
# ============================================================

def estrategia_triple_confirm_c(candles: list[dict], cfg: dict) -> dict:
    """TRIPLE_CONFIRM_C: RSI(14) + MACD(12,26,9) + BB(20,2). Triple confirmacao. Conservador.

    CALL quando (todas as 3 condicoes verdadeiras):
        1. close <= BB inferior (preco em sobrevenda de volatilidade)
        2. RSI < 35 E subindo (sobrevenda de momentum)
        3. MACD cruzou acima da linha de sinal (momentum girando para alta)

    PUT quando (todas as 3 condicoes verdadeiras):
        1. close >= BB superior (preco em sobrecompra de volatilidade)
        2. RSI > 65 E caindo (sobrecompra de momentum)
        3. MACD cruzou abaixo da linha de sinal (momentum girando para baixa)

    Requer pelo menos 65 candles.
    """
    MIN_CANDLES = 65

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    rsi      = ta.rsi(closes_s, length=14)
    bb_df    = ta.bbands(closes_s, length=20, std=2.0)
    macd_df  = ta.macd(closes_s, fast=12, slow=26, signal=9)

    bb_upper    = bb_df["BBU_20_2.0_2.0"]
    bb_lower    = bb_df["BBL_20_2.0_2.0"]
    bb_mid      = bb_df["BBM_20_2.0_2.0"]
    macd_line   = macd_df["MACD_12_26_9"]
    signal_line = macd_df["MACDs_12_26_9"]

    if any(pd.isna(v) for v in [
        rsi.iloc[-1], rsi.iloc[-2],
        bb_upper.iloc[-1], bb_lower.iloc[-1],
        macd_line.iloc[-1], macd_line.iloc[-2],
        signal_line.iloc[-1], signal_line.iloc[-2],
    ]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    close_atual = closes_s.iloc[-1]
    rsi_atual   = rsi.iloc[-1]
    rsi_ant     = rsi.iloc[-2]

    macd_cruzou_acima  = macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]
    macd_cruzou_abaixo = macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]

    indicadores = {
        "RSI":    round(rsi_atual,          2),
        "BB_inf": round(bb_lower.iloc[-1],  5),
        "BB_sup": round(bb_upper.iloc[-1],  5),
        "MACD":   round(macd_line.iloc[-1], 6),
        "Signal": round(signal_line.iloc[-1], 6),
    }

    if (close_atual <= bb_lower.iloc[-1]
            and rsi_atual < 35
            and rsi_atual > rsi_ant
            and macd_cruzou_acima):
        return {
            "sinal": "call",
            "motivo": f"Triple CALL | BB_inf | RSI={rsi_atual:.1f}<35 subindo | MACD cruzou acima",
            "indicadores": indicadores,
        }

    if (close_atual >= bb_upper.iloc[-1]
            and rsi_atual > 65
            and rsi_atual < rsi_ant
            and macd_cruzou_abaixo):
        return {
            "sinal": "put",
            "motivo": f"Triple PUT | BB_sup | RSI={rsi_atual:.1f}>65 caindo | MACD cruzou abaixo",
            "indicadores": indicadores,
        }

    conf_call = sum([close_atual < bb_mid.iloc[-1], rsi_atual < 40, macd_line.iloc[-1] > signal_line.iloc[-1]])
    conf_put  = sum([close_atual > bb_mid.iloc[-1], rsi_atual > 60, macd_line.iloc[-1] < signal_line.iloc[-1]])
    return {
        "sinal": None,
        "motivo": f"Confirmacoes insuficientes | CALL:{conf_call}/3 PUT:{conf_put}/3 | RSI={rsi_atual:.1f}",
        "indicadores": indicadores,
    }


# ============================================================
# MACD_EMA_M — MACD + EMA(50) | Moderado | M5/M15/H1
# ============================================================

def estrategia_macd_ema_m(candles: list[dict], cfg: dict) -> dict:
    """MACD_EMA_M: EMA(50) como filtro de tendencia + cruzamento do MACD(12,26,9). Moderado.

    CALL quando:
        - close > EMA50 (tendencia de alta)
        - MACD cruzou acima da linha de sinal (momentum confirmando alta)

    PUT quando:
        - close < EMA50 (tendencia de baixa)
        - MACD cruzou abaixo da linha de sinal (momentum confirmando baixa)

    Requer pelo menos 65 candles.
    """
    MIN_CANDLES = 65

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    ema50    = ta.ema(closes_s, length=50)
    macd_df  = ta.macd(closes_s, fast=12, slow=26, signal=9)

    macd_line   = macd_df["MACD_12_26_9"]
    signal_line = macd_df["MACDs_12_26_9"]
    hist        = macd_df["MACDh_12_26_9"]

    if any(pd.isna(v) for v in [
        ema50.iloc[-1],
        macd_line.iloc[-1], macd_line.iloc[-2],
        signal_line.iloc[-1], signal_line.iloc[-2],
        hist.iloc[-1],
    ]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    close_atual = closes_s.iloc[-1]
    ema_atual   = ema50.iloc[-1]

    macd_cruzou_acima  = macd_line.iloc[-2] <= signal_line.iloc[-2] and macd_line.iloc[-1] > signal_line.iloc[-1]
    macd_cruzou_abaixo = macd_line.iloc[-2] >= signal_line.iloc[-2] and macd_line.iloc[-1] < signal_line.iloc[-1]

    indicadores = {
        "EMA50":       round(ema_atual,          5),
        "MACD":        round(macd_line.iloc[-1], 6),
        "Signal":      round(signal_line.iloc[-1], 6),
        "Hist":        round(hist.iloc[-1],      6),
        "Preco_EMA50": "acima" if close_atual > ema_atual else "abaixo",
    }

    if close_atual > ema_atual and macd_cruzou_acima:
        return {
            "sinal": "call",
            "motivo": f"Tendencia ALTA (EMA50) | MACD cruzou acima do sinal",
            "indicadores": indicadores,
        }

    if close_atual < ema_atual and macd_cruzou_abaixo:
        return {
            "sinal": "put",
            "motivo": f"Tendencia BAIXA (EMA50) | MACD cruzou abaixo do sinal",
            "indicadores": indicadores,
        }

    tend     = "ALTA" if close_atual > ema_atual else "BAIXA"
    macd_pos = "MACD>SIG" if macd_line.iloc[-1] > signal_line.iloc[-1] else "MACD<SIG"
    return {
        "sinal": None,
        "motivo": f"Sem cruzamento | Tendencia {tend} | {macd_pos}",
        "indicadores": indicadores,
    }


# ============================================================
# ENGOLFO_M — Candle Engolfante + RSI | Moderado | M1/M5
# ============================================================

def estrategia_engolfo_m(candles: list[dict], cfg: dict) -> dict:
    """ENGOLFO_M: padrao de candle engolfante confirmado pelo RSI(14). Moderado.

    CALL quando (engolfo altista):
        - vela anterior vermelha, vela atual verde
        - close atual > open anterior  (corpo engolfa o da vela previa)
        - open atual < close anterior
        - corpo atual > corpo anterior  (forca do engolfo)
        - RSI < 60 (nao sobrecomprado)

    PUT quando (engolfo baixista):
        - vela anterior verde, vela atual vermelha
        - close atual < open anterior
        - open atual > close anterior
        - corpo atual > corpo anterior
        - RSI > 40 (nao sobrevendido)

    Requer pelo menos 20 candles.
    """
    MIN_CANDLES = 20

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    rsi      = ta.rsi(closes_s, length=14)

    if pd.isna(rsi.iloc[-1]):
        return {"sinal": None, "motivo": "RSI aquecendo", "indicadores": {}}

    vela_0 = candles[-1]
    vela_1 = candles[-2]

    open_0  = vela_0["open"];  close_0 = vela_0["close"]
    open_1  = vela_1["open"];  close_1 = vela_1["close"]
    corpo_0 = abs(close_0 - open_0)
    corpo_1 = abs(close_1 - open_1)
    rsi_atual = rsi.iloc[-1]

    vela_0_alta  = close_0 > open_0
    vela_0_baixa = close_0 < open_0
    vela_1_alta  = close_1 > open_1
    vela_1_baixa = close_1 < open_1
    forca        = corpo_0 > corpo_1

    engolfo_altista = (
        vela_1_baixa and vela_0_alta
        and close_0 > open_1
        and open_0 < close_1
        and forca
    )
    engolfo_baixista = (
        vela_1_alta and vela_0_baixa
        and close_0 < open_1
        and open_0 > close_1
        and forca
    )

    indicadores = {
        "RSI":     round(rsi_atual, 2),
        "Corpo0":  round(corpo_0,   5),
        "Corpo1":  round(corpo_1,   5),
        "Eng_alt": engolfo_altista,
        "Eng_bai": engolfo_baixista,
    }

    if engolfo_altista and rsi_atual < 60:
        return {
            "sinal": "call",
            "motivo": f"Engolfo altista | RSI={rsi_atual:.1f}<60 | corpo {corpo_0:.5f}>{corpo_1:.5f}",
            "indicadores": indicadores,
        }

    if engolfo_baixista and rsi_atual > 40:
        return {
            "sinal": "put",
            "motivo": f"Engolfo baixista | RSI={rsi_atual:.1f}>40 | corpo {corpo_0:.5f}>{corpo_1:.5f}",
            "indicadores": indicadores,
        }

    return {
        "sinal": None,
        "motivo": f"Sem engolfo confirmado | RSI={rsi_atual:.1f}",
        "indicadores": indicadores,
    }


# ============================================================
# ESTOCASTICO_A — Stochastic(5,3,3) cruzamento em extremos | Agressivo | M1/M5
# ============================================================

def estrategia_estocastico_a(candles: list[dict], cfg: dict) -> dict:
    """ESTOCASTICO_A: cruzamento do %K sobre %D nas zonas de extremo. Agressivo.

    CALL quando:
        - %K cruzou acima de %D (%K[-2] <= %D[-2] e %K[-1] > %D[-1])
        - Ambos abaixo de 20 (zona de sobrevenda)

    PUT quando:
        - %K cruzou abaixo de %D (%K[-2] >= %D[-2] e %K[-1] < %D[-1])
        - Ambos acima de 80 (zona de sobrecompra)

    Alta frequencia de sinal. Usar MG. Requer pelo menos 20 candles.
    """
    MIN_CANDLES = 20

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    highs_s  = pd.Series([c["high"]  for c in candles])
    lows_s   = pd.Series([c["low"]   for c in candles])

    stoch_df = ta.stoch(highs_s, lows_s, closes_s, k=5, d=3, smooth_k=3)
    pct_k    = stoch_df["STOCHk_5_3_3"]
    pct_d    = stoch_df["STOCHd_5_3_3"]

    if any(pd.isna(v) for v in [pct_k.iloc[-1], pct_k.iloc[-2], pct_d.iloc[-1], pct_d.iloc[-2]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    k1 = pct_k.iloc[-1]; k2 = pct_k.iloc[-2]
    d1 = pct_d.iloc[-1]; d2 = pct_d.iloc[-2]

    indicadores = {
        "%K": round(k1, 2),
        "%D": round(d1, 2),
    }

    cruzou_acima  = k2 <= d2 and k1 > d1
    cruzou_abaixo = k2 >= d2 and k1 < d1

    if cruzou_acima and k1 < 20 and d1 < 20:
        return {
            "sinal": "call",
            "motivo": f"Stoch cruzou acima em sobrevenda | %K={k1:.1f} %D={d1:.1f}",
            "indicadores": indicadores,
        }

    if cruzou_abaixo and k1 > 80 and d1 > 80:
        return {
            "sinal": "put",
            "motivo": f"Stoch cruzou abaixo em sobrecompra | %K={k1:.1f} %D={d1:.1f}",
            "indicadores": indicadores,
        }

    zona = "SOBREVENDIDO" if k1 < 20 else "SOBRECOMPRADO" if k1 > 80 else "NEUTRO"
    return {
        "sinal": None,
        "motivo": f"Sem cruzamento em extremo | %K={k1:.1f} %D={d1:.1f} | zona={zona}",
        "indicadores": indicadores,
    }


# ============================================================
# BB_SQUEEZE_A — Bollinger Squeeze + Rompimento | Agressivo | M15/M30
# ============================================================

def estrategia_bb_squeeze_a(candles: list[dict], cfg: dict) -> dict:
    """BB_SQUEEZE_A: detecta compressao das bandas (squeeze) e entra no rompimento. Agressivo.

    Squeeze: largura atual das BB < 50% da largura media dos ultimos 20 candles.

    CALL quando:
        - Squeeze ativo no candle anterior ([-2])
        - Candle atual rompe para cima: close[-1] > BB_superior[-1]
        - Vela atual de alta (close > open)

    PUT quando:
        - Squeeze ativo no candle anterior ([-2])
        - Candle atual rompe para baixo: close[-1] < BB_inferior[-1]
        - Vela atual de baixa (close < open)

    Sinal explosivo, nao requer MG. Requer pelo menos 50 candles.
    """
    MIN_CANDLES = 50

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    bb_df    = ta.bbands(closes_s, length=20, std=2.0)

    bb_upper = bb_df["BBU_20_2.0_2.0"]
    bb_lower = bb_df["BBL_20_2.0_2.0"]

    if any(pd.isna(v) for v in [bb_upper.iloc[-1], bb_lower.iloc[-1], bb_upper.iloc[-2], bb_lower.iloc[-2]]):
        return {"sinal": None, "motivo": "Indicadores aquecendo", "indicadores": {}}

    # Largura das bandas (raw, nao normalizada)
    larguras_series = bb_upper - bb_lower  # pd.Series, NaN onde BB e NaN

    # Media das ultimas 20 larguras validas
    larg_validas = larguras_series.iloc[-20:].dropna().tolist()
    if len(larg_validas) < 10:
        return {"sinal": None, "motivo": "Larguras insuficientes", "indicadores": {}}

    media_largura = sum(larg_validas) / len(larg_validas)
    largura_ant   = larguras_series.iloc[-2]
    largura_atual = larguras_series.iloc[-1]

    squeeze_ativo = not pd.isna(largura_ant) and largura_ant < media_largura * 0.5

    close_atual = closes_s.iloc[-1]
    vela_alta   = candles[-1]["close"] > candles[-1]["open"]
    vela_baixa  = candles[-1]["close"] < candles[-1]["open"]

    indicadores = {
        "BB_sup":      round(bb_upper.iloc[-1], 5),
        "BB_inf":      round(bb_lower.iloc[-1], 5),
        "Largura":     round(largura_atual,     6),
        "Med_largura": round(media_largura,     6),
        "Squeeze_ant": squeeze_ativo,
    }

    if squeeze_ativo and close_atual > bb_upper.iloc[-1] and vela_alta:
        return {
            "sinal": "call",
            "motivo": f"Squeeze + rompimento ACIMA | larg={largura_atual:.6f} < med={media_largura:.6f}",
            "indicadores": indicadores,
        }

    if squeeze_ativo and close_atual < bb_lower.iloc[-1] and vela_baixa:
        return {
            "sinal": "put",
            "motivo": f"Squeeze + rompimento ABAIXO | larg={largura_atual:.6f} < med={media_largura:.6f}",
            "indicadores": indicadores,
        }

    estado = "SQUEEZE" if squeeze_ativo else "normal"
    return {
        "sinal": None,
        "motivo": f"Sem rompimento | BB {estado} | close={close_atual:.5f}",
        "indicadores": indicadores,
    }


# ============================================================
# TRES_CANDLES_A — 3 Candles Consecutivos + RSI filtro | Agressivo | M1/M5
# ============================================================

def estrategia_tres_candles_a(candles: list[dict], cfg: dict) -> dict:
    """TRES_CANDLES_A: 3 candles consecutivos na mesma direcao indicam exaustao. Agressivo.

    CALL quando:
        - 3 candles bearish consecutivos ([-4], [-3], [-2] todos fecham abaixo da abertura)
        - RSI nao esta em sobrecompra forte (RSI < 65) — evita entrar contra tendencia violenta
        - Candle [-1] (atual) ainda nao reverteu (close <= close[-2]) — entrada na abertura do proximo

    PUT quando:
        - 3 candles bullish consecutivos
        - RSI nao esta em sobrevenda forte (RSI > 35)
        - Candle atual nao reverteu (close >= close[-2])

    Logica: 3 candles seguidos = exaustao do movimento. Requer pelo menos 20 candles.
    """
    MIN_CANDLES = 20

    if len(candles) < MIN_CANDLES:
        return {
            "sinal": None,
            "motivo": f"Candles insuficientes ({len(candles)}/{MIN_CANDLES})",
            "indicadores": {},
        }

    closes_s = pd.Series([c["close"] for c in candles])
    rsi      = ta.rsi(closes_s, length=14)

    if pd.isna(rsi.iloc[-1]):
        return {"sinal": None, "motivo": "RSI aquecendo", "indicadores": {}}

    # Verifica cor dos 3 candles anteriores ao atual
    def is_bearish(c): return c["close"] < c["open"]
    def is_bullish(c): return c["close"] > c["open"]

    c4 = candles[-4]  # mais antigo dos 3
    c3 = candles[-3]
    c2 = candles[-2]
    c1 = candles[-1]  # candle atual (aguardando fechamento)

    tres_bearish = is_bearish(c4) and is_bearish(c3) and is_bearish(c2)
    tres_bullish = is_bullish(c4) and is_bullish(c3) and is_bullish(c2)

    rsi_atual = rsi.iloc[-1]

    indicadores = {
        "RSI":         round(rsi_atual,    2),
        "3_bearish":   tres_bearish,
        "3_bullish":   tres_bullish,
        "Close_atual": round(c1["close"],  5),
        "Close_ant":   round(c2["close"],  5),
    }

    # CALL: 3 bearish + RSI nao sobrecomprado + candle atual ainda nao reverteu
    if tres_bearish and rsi_atual < 65 and c1["close"] <= c2["close"]:
        return {
            "sinal": "call",
            "motivo": f"3 candles bearish | RSI={rsi_atual:.1f}<65 | exaustao de baixa",
            "indicadores": indicadores,
        }

    # PUT: 3 bullish + RSI nao sobrevendido + candle atual ainda nao reverteu
    if tres_bullish and rsi_atual > 35 and c1["close"] >= c2["close"]:
        return {
            "sinal": "put",
            "motivo": f"3 candles bullish | RSI={rsi_atual:.1f}>35 | exaustao de alta",
            "indicadores": indicadores,
        }

    seq = "3_BEAR" if tres_bearish else "3_BULL" if tres_bullish else "sem_seq"
    return {
        "sinal": None,
        "motivo": f"Sem sinal | seq={seq} | RSI={rsi_atual:.1f}",
        "indicadores": indicadores,
    }


# ============================================================
# FILTRO DE VOLATILIDADE
# ============================================================

def calcular_volatilidade(candles: list[dict], periodo: int = 20) -> dict:
    """Calcula a volatilidade relativa do mercado pelo range dos candles.

    Compara o range (high-low) do candle mais recente com a media dos
    ultimos `periodo` candles.

    ratio_pct = 100% -> volatilidade normal
    ratio_pct < 30%  -> mercado quieto / comprimido

    Retorna dict com:
        range_atual  : range do ultimo candle
        range_medio  : media dos ultimos `periodo` candles
        ratio_pct    : range_atual / range_medio * 100
    """
    if len(candles) < periodo:
        return {"range_atual": 0.0, "range_medio": 0.0, "ratio_pct": 100.0}

    ranges = [c["high"] - c["low"] for c in candles]
    range_atual = ranges[-1]
    range_medio = sum(ranges[-periodo:]) / periodo

    if range_medio == 0:
        return {"range_atual": 0.0, "range_medio": 0.0, "ratio_pct": 100.0}

    return {
        "range_atual": round(range_atual, 6),
        "range_medio": round(range_medio, 6),
        "ratio_pct":   round((range_atual / range_medio) * 100, 1),
    }


# ============================================================
# REGISTRO DE ESTRATEGIAS
# Adicione novas estrategias aqui (nome -> funcao).
# O Loop Autonomo le este dicionario automaticamente.
# ============================================================

ESTRATEGIAS: dict = {
    "EMA_RSI":          estrategia_ema_rsi,
    "REVERSAO_SMA_M":   estrategia_reversao_sma_m,
    "FRACTAL_MACD_M":   estrategia_fractal_macd_m,
    "MACD_RSI_C":       estrategia_macd_rsi_c,
    # --- Novas estrategias catalogadas ---
    "BB_RSI_C":         estrategia_bb_rsi_c,
    "EMA_RSI_C":        estrategia_ema_rsi_c,
    "TRIPLE_CONFIRM_C": estrategia_triple_confirm_c,
    "MACD_EMA_M":       estrategia_macd_ema_m,
    "ENGOLFO_M":        estrategia_engolfo_m,
    # --- Estrategias agressivas ---
    "ESTOCASTICO_A":   estrategia_estocastico_a,
    "BB_SQUEEZE_A":    estrategia_bb_squeeze_a,
    "TRES_CANDLES_A":  estrategia_tres_candles_a,
}


# ============================================================
# METADADOS DAS ESTRATEGIAS
# Informacoes exibidas na selecao do Loop Autonomo.
#
# timeframe_rec : duracao em segundos recomendada (None = sem preferencia)
# descricao     : resumo exibido na lista de selecao
# ============================================================

ESTRATEGIAS_META: dict = {
    "EMA_RSI": {
        "timeframe_rec": 60,
        "descricao": "[M] EMA 9/21 + RSI(14) | Reversao de tendencia | M1 (60s)",
    },
    "REVERSAO_SMA_M": {
        "timeframe_rec": 60,
        "descricao": "[M] Reversao candle + SMA5/21 + corpo + movimento | M1 (60s)",
    },
    "FRACTAL_MACD_M": {
        "timeframe_rec": 60,
        "descricao": "[M] Fractal de 3 velas + MACD simplificado | M1 (60s)",
    },
    "MACD_RSI_C": {
        "timeframe_rec": 60,
        "descricao": "[C] MACD mini + corpo + RSI(14) + movimento | 4 filtros | M1 (60s)",
    },
    # --- Novas estrategias catalogadas ---
    "BB_RSI_C": {
        "timeframe_rec": 300,
        "descricao": "[C] BB(20,2) + RSI(14) | Rebote nas bandas | M5 (300s)",
    },
    "EMA_RSI_C": {
        "timeframe_rec": 300,
        "descricao": "[C] EMA(50) + RSI(14) | Pullback em tendencia | M5 (300s)",
    },
    "TRIPLE_CONFIRM_C": {
        "timeframe_rec": 900,
        "descricao": "[C] RSI + MACD + BB | Triple confirmacao | M15 (900s)",
    },
    "MACD_EMA_M": {
        "timeframe_rec": 300,
        "descricao": "[M] MACD(12,26,9) + EMA(50) | Tendencia + momentum | M5 (300s)",
    },
    "ENGOLFO_M": {
        "timeframe_rec": 60,
        "descricao": "[M] Candle engolfante + RSI(14) | Price action | M1 (60s)",
    },
    # --- Estrategias agressivas ---
    "ESTOCASTICO_A": {
        "timeframe_rec": 60,
        "descricao": "[A] Stoch(5,3,3) cruzamento em extremos (<20/>80) | M1 (60s)",
    },
    "BB_SQUEEZE_A": {
        "timeframe_rec": 900,
        "descricao": "[A] BB squeeze + rompimento com candle forte | M15 (900s)",
    },
    "TRES_CANDLES_A": {
        "timeframe_rec": 60,
        "descricao": "[A] 3 candles consecutivos + RSI filtro | Exaustao | M1 (60s)",
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

        # Filtro de volatilidade: bloqueia sinal se mercado muito quieto
        vol_min = float(cfg.get("volatilidade_minima_pct", 0))
        if vol_min > 0 and resultado["sinal"] is not None:
            vol = calcular_volatilidade(candles)
            resultado["indicadores"]["VOL_ratio"] = vol["ratio_pct"]
            if vol["ratio_pct"] < vol_min:
                resultado["sinal"] = None
                resultado["motivo"] = (
                    f"[VOL] Mercado quieto: range={vol['ratio_pct']:.0f}% "
                    f"da media (min={vol_min:.0f}%)"
                )

        return resultado
    except Exception as e:
        return {
            "sinal": None,
            "motivo": f"Erro na estrategia '{nome}': {e}",
            "indicadores": {},
        }
