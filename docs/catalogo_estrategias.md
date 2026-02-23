# CATALOGO DE ESTRATEGIAS DE TRADING
**Fontes:** CME Group PDF + EBC + LiteFinance + Axi + TradeNation + Web Search
**Data:** 2026-02-20

---

## LEGENDA

| Campo        | Descricao                                              |
|--------------|--------------------------------------------------------|
| Perfil       | C = Conservador | M = Moderado | A = Agressivo         |
| MG           | Martingale recomendado: Sim / Nao / Opcional           |
| Implementada | Se ja existe em estrategias.py                         |
| TF rec       | Timeframe recomendado                                  |

---

## GRUPO 1 — SEGUIMENTO DE TENDENCIA

### EST-01: EMA_CROSSOVER
- **Indicadores:** EMA(9) + EMA(21) ou EMA(10) + EMA(50)
- **CALL:** EMA rapida cruza acima da EMA lenta (golden cross)
- **PUT:** EMA rapida cruza abaixo da EMA lenta (death cross)
- **Filtro:** Aguardar candle fechar confirmando a direcao
- **TF rec:** M5, M15, M30
- **Perfil:** C-M
- **MG:** Opcional (~55-60% acerto)
- **Implementada:** Sim — `EMA_RSI` (versao com RSI)

---

### EST-02: TRIPLE_EMA
- **Indicadores:** EMA(5) + EMA(25) + EMA(50) + Parabolic SAR
- **CALL:** EMA5 > EMA25 > EMA50 (alinhamento altista) + PSAR abaixo do preco
- **PUT:** EMA5 < EMA25 < EMA50 (alinhamento baixista) + PSAR acima do preco
- **TF rec:** M15, M30
- **Perfil:** M
- **MG:** Sim (mais filtros = menos sinais)
- **Implementada:** Nao
- **Fonte:** LiteFinance "Parabolic Profit"

---

### EST-03: MACD_CROSSOVER
- **Indicadores:** MACD(12,26,9)
- **CALL:** Linha MACD cruza acima da linha de sinal + histograma positivo
- **PUT:** Linha MACD cruza abaixo da linha de sinal + histograma negativo
- **TF rec:** M5, M15, M30, H1
- **Perfil:** M
- **MG:** Sim (sinal atrasado, ~55% acerto)
- **Implementada:** Parcial — logica MACD usada em `PROFITX_FRACTAL` e `MACD_EMA_M`

---

### EST-04: ADX_DIRECIONAL
- **Indicadores:** ADX(14) + DI+ + DI-
- **CALL:** ADX > 25 (tendencia forte) + DI+ acima de DI-
- **PUT:** ADX > 25 + DI- acima de DI+
- **Obs:** Evitar entradas quando ADX < 20 (mercado lateral)
- **TF rec:** M15, M30, H1
- **Perfil:** C
- **MG:** Nao necessario (filtra laterais, alta precisao em tendencia)
- **Implementada:** Nao

---

## GRUPO 2 — REVERSAO / OSCILADORES

### EST-05: RSI_EXTREMOS
- **Indicadores:** RSI(14)
- **CALL:** RSI cruza de volta acima de 30 (saindo da sobrevenda)
- **PUT:** RSI cruza de volta abaixo de 70 (saindo da sobrecompra)
- **Cuidado:** Em tendencia forte, RSI pode permanecer em extremos
- **TF rec:** M5, M15
- **Perfil:** M-A
- **MG:** Sim (funciona bem com MG em mercado lateral)
- **Implementada:** Parcial — RSI usado como filtro em varias estrategias

---

### EST-06: EMA_RSI_C ★ IMPLEMENTADA
- **Indicadores:** EMA(50) + RSI(14)
- **CALL:** Preco acima da EMA50 E RSI saindo de abaixo de 40 (pullback em tendencia de alta)
- **PUT:** Preco abaixo da EMA50 E RSI saindo de acima de 60 (pullback em tendencia de baixa)
- **TF rec:** M5, M15
- **Perfil:** C
- **MG:** Nao necessario (~60-65% acerto)
- **Implementada:** Sim — `EMA_RSI_C`

---

### EST-07: ESTOCASTICO_A ★ IMPLEMENTADA
- **Indicadores:** Stochastic(5,3,3) ou (14,3,3)
- **CALL:** %K cruza acima de %D na zona de sobrevenda (<20)
- **PUT:** %K cruza abaixo de %D na zona de sobrecompra (>80)
- **TF rec:** M1, M5
- **Perfil:** A
- **MG:** Sim (sinal frequente, ~52-55% acerto)
- **Implementada:** Sim — `ESTOCASTICO_A`

---

### EST-08: ESTOCASTICO_RSI
- **Indicadores:** Stoch(5,3,3) + RSI(14)
- **CALL:** Stoch cruzando em sobrevenda E RSI < 35
- **PUT:** Stoch cruzando em sobrecompra E RSI > 65
- **TF rec:** M5, M15
- **Perfil:** C
- **MG:** Opcional (dupla confirmacao eleva precisao)
- **Implementada:** Nao

---

## GRUPO 3 — VOLATILIDADE (BOLLINGER BANDS)

### EST-09: BB_RSI_C ★ IMPLEMENTADA
- **Indicadores:** BB(20,2) + RSI(14)
- **CALL:** Preco toca/penetra banda inferior E RSI < 35 E RSI subindo
- **PUT:** Preco toca/penetra banda superior E RSI > 65 E RSI caindo
- **TF rec:** M5, M15 (mercado lateral/range)
- **Perfil:** C
- **MG:** Nao necessario (~62-68% acerto)
- **Implementada:** Sim — `BB_RSI_C`

---

### EST-10: BB_SQUEEZE_A ★ IMPLEMENTADA
- **Indicadores:** BB(20,2) + Volume ou MACD como confirmacao
- **CALL:** Bandas contraem (baixa volatilidade) + preco rompe banda superior com candle forte
- **PUT:** Bandas contraem + preco rompe banda inferior com candle forte
- **Obs:** Squeeze = distancia entre bandas menor que media historica
- **TF rec:** M15, M30, H1
- **Perfil:** A
- **MG:** Nao recomendado (sinal explosivo, alto potencial)
- **Implementada:** Sim — `BB_SQUEEZE_A`

---

## GRUPO 4 — COMBINADAS (ALTA CONFIABILIDADE)

### EST-11: TRIPLE_CONFIRM_C ★ IMPLEMENTADA
- **Indicadores:** RSI(14) + MACD(12,26,9) + BB(20,2)
- **CALL:** Preco na BB inferior + RSI < 35 subindo + MACD cruzou positivo
- **PUT:** Preco na BB superior + RSI > 65 caindo + MACD cruzou negativo
- **TF rec:** M15, M30
- **Perfil:** C
- **MG:** Nao necessario (~65%+ acerto estimado)
- **Frequencia:** Baixa (~1-3 sinais/dia por ativo)
- **Implementada:** Sim — `TRIPLE_CONFIRM_C`

---

### EST-12: MACD_EMA_M ★ IMPLEMENTADA
- **Indicadores:** MACD(12,26,9) + EMA(50)
- **CALL:** Preco acima da EMA50 + MACD cruzou acima da linha de sinal
- **PUT:** Preco abaixo da EMA50 + MACD cruzou abaixo da linha de sinal
- **TF rec:** M5, M15, H1
- **Perfil:** M
- **MG:** Opcional
- **Implementada:** Sim — `MACD_EMA_M`

---

### EST-13: PARABOLIC_PROFIT
- **Indicadores:** EMA(5) + EMA(25) + EMA(50) + Parabolic SAR
- **CALL:** EMA5 cruza EMA25 para cima + PSAR abaixo do preco
- **PUT:** EMA5 cruza EMA25 para baixo + PSAR acima do preco
- **TF rec:** M15, M30
- **Perfil:** M
- **MG:** Sim
- **Implementada:** Nao (similar a EST-02)
- **Fonte:** LiteFinance "Parabolic Profit"

---

## GRUPO 5 — PRICE ACTION (CANDLES)

### EST-14: ENGOLFO_M ★ IMPLEMENTADA
- **Indicadores:** Padrao de 2 candles + RSI(14) como filtro
- **CALL:** Candle bearish + candle bullish que engolfa completamente + RSI < 60
- **PUT:** Candle bullish + candle bearish que engolfa completamente + RSI > 40
- **TF rec:** M1, M5
- **Perfil:** M
- **MG:** Sim (confirmacao de reversao)
- **Filtro ideal:** Usar em suporte/resistencia ou extremo de RSI
- **Implementada:** Sim — `ENGOLFO_M`

---

### EST-15: PIN_BAR
- **Indicadores:** Formato do candle (price action)
- **CALL:** Candle com sombra inferior longa (>= 2x corpo) + corpo no topo (martelo)
- **PUT:** Candle com sombra superior longa + corpo na base (estrela cadente)
- **Filtro ideal:** Ocorrer em suporte/resistencia + RSI em extremo
- **TF rec:** M5, M15, M30
- **Perfil:** M
- **MG:** Opcional
- **Implementada:** Nao

---

### EST-16: TRES_CANDLES_A ★ IMPLEMENTADA
- **Indicadores:** Price action (contagem de candles)
- **CALL:** 3 candles bearish consecutivos -> entrada CALL na abertura do 4o
- **PUT:** 3 candles bullish consecutivos -> entrada PUT na abertura do 4o
- **TF rec:** M1, M5
- **Perfil:** A
- **MG:** Sim (~52-55% acerto)
- **Implementada:** Sim — `TRES_CANDLES_A`
- **Fonte:** LiteFinance "Last Resort" adaptation

---

## GRUPO 6 — RANGE / LATERAL

### EST-17: RANGE_TRADING
- **Indicadores:** BB(20,2) + Stoch(14,3,3) + RSI(14)
- **CALL:** Preco em suporte identificado + Stoch e RSI em sobrevenda
- **PUT:** Preco em resistencia + Stoch e RSI em sobrecompra
- **Cuidado:** Nao usar quando ADX > 25 (mercado com tendencia forte)
- **TF rec:** M15, M30
- **Perfil:** C
- **MG:** Nao necessario
- **Implementada:** Nao

---

### EST-18: BALI_SCALPING
- **Indicadores:** LWMA(48) + Trend Envelopes v2 (periodo 2) + DSS Momentum
- **CALL:** Preco rompe linha laranja Trend Envelopes para cima + candle fecha acima LWMA + DSS verde acima sinal
- **PUT:** Preco rompe linha azul Trend Envelopes para baixo + candle fecha abaixo LWMA + DSS laranja abaixo sinal
- **TF rec:** H1
- **Perfil:** A
- **MG:** Opcional (20-25 pips SL / 40-50 pips TP)
- **Implementada:** Nao
- **Fonte:** LiteFinance "Bali"

---

## ESTRATEGIAS JA EXISTENTES NO SISTEMA

| Nome            | Descricao                                      | TF  | Perfil |
|-----------------|------------------------------------------------|-----|--------|
| `NENHUMA`       | Placeholder sem sinal                          | -   | -      |
| `EMA_RSI`       | EMA 9/21 crossover + RSI(14) + cor da vela     | M1  | M      |
| `PROFITX_E1`    | Reversao + SMA5/21 + corpo + consolidacao      | M1  | M      |
| `PROFITX_FRACTAL` | MACD simplificado + fractal de 3 velas       | M1  | M      |
| `PROFITX_RESTRITO` | MACD + corpo + RSI + movimento (mais seletivo) | M1 | C-M  |

---

## RESUMO — PRIORIDADE DE BACKTESTE

| Prioridade | Estrategia       | TF     | Perfil | MG       | Status        |
|------------|------------------|--------|--------|----------|---------------|
| 1          | TRIPLE_CONFIRM_C | M15    | C      | Nao      | Implementada  |
| 2          | BB_RSI_C         | M5     | C      | Nao      | Implementada  |
| 3          | EMA_RSI_C        | M5     | C      | Nao      | Implementada  |
| 4          | MACD_EMA_M       | M5     | M      | Opcional | Implementada  |
| 5          | ENGOLFO_M        | M1     | M      | Sim      | Implementada  |
| 6          | ESTOCASTICO_A    | M1     | A      | Sim      | Implementada  |
| 7          | BB_SQUEEZE_A     | M15    | A      | Nao      | Implementada  |
| 8          | TRES_CANDLES_A   | M1     | A      | Sim      | Implementada  |
| 9          | ADX_DIRECIONAL   | M15/H1 | C      | Nao      | Nao impl.     |
| 10         | PIN_BAR          | M5/M15 | M      | Opcional | Nao impl.     |

---

## CONCEITOS DO CME GROUP (PDF) — APLICAVEIS AO SISTEMA

| Conceito         | Aplicacao no sistema                                             |
|------------------|------------------------------------------------------------------|
| Mercado direcional | Confirmar tendencia com EMA/MACD antes de entrar              |
| Mercado de precisao | Usar RSI extremo + Bollinger para timing de reversao         |
| Volatilidade impl. | Evitar entradas em mercado "quieto" (BB estreita sem squeeze) |
| Depreciacao temporal | No binario: entrar proximo a abertura do candle            |
| Bull/Bear Spread  | Equivalente a CALL/PUT conservador com filtro de tendencia      |
| Straddle/Strangle | Movimento iminente sem direcao — nao aplicavel em binario       |
