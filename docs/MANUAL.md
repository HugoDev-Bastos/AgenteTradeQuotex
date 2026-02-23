# AgentTradSyst — Manual do Sistema

Documento de referencia completo: arquitetura, funcionalidades, loops e comportamentos.

---

## Sumario

1. [Visao Geral](#1-visao-geral)
2. [Os 6 Agentes](#2-os-6-agentes)
3. [Configuracoes](#3-configuracoes)
4. [Sistema de Protecao](#4-sistema-de-protecao)
5. [Sistema de Analise](#5-sistema-de-analise)
6. [Agente Verificador de Mercado](#6-agente-verificador-de-mercado)
7. [Martingale (MG)](#7-martingale-mg)
8. [Loop Quotex](#8-loop-quotex)
9. [Loop Telegram](#9-loop-telegram)
10. [Loop Lista](#10-loop-lista)
11. [Loop Autonomo](#11-loop-autonomo)
12. [Backteste](#12-backteste)
13. [Estrategias Tecnicas](#13-estrategias-tecnicas)
14. [Estabilidade e Resiliencia](#14-estabilidade-e-resiliencia)
15. [Arquivos do Sistema](#15-arquivos-do-sistema)

---

## 1. Visao Geral

O AgentTradSyst e um sistema de trading automatizado que combina:

- **Conexao real** com a corretora Quotex via websocket (PyQuotex)
- **Inteligencia artificial** via API Anthropic (Claude) para analise e gerenciamento
- **4 loops de operacao** para diferentes estrategias de uso
- **Protecao de capital** automatica com multiplas camadas de seguranca

### Fluxo geral de uma operacao

```text
Sinal de entrada
      |
      v
AgentProtetor verifica limites    -->  BLOQUEADO?  --> Para o loop
      |
      v (pode continuar)
AgentVerificador verifica mercado -->  BLOQUEADO?  --> Descarta sinal
      |
      v (mercado favoravel)
AgentAnalisador avalia metricas   -->  PAUSAR?     --> Pausa sequencia
      |
      v (recomenda continuar)
AgentQuotex executa trade na Quotex
      |
      v
Aguarda resultado (check_win)
      |
      v
WIN / LOSS / DOJI
      |
      v
Se LOSS e tem niveis MG restantes --> MG
      |
      v
Registra resultado --> data/operacoes.json
      |
      v
Proxima iteracao do loop
```

---

## 2. Os 6 Agentes

### AgentTrading

- **Funcao:** Chat interativo com o Claude via API Anthropic
- **Usa API:** Sim (Claude Sonnet)
- **Modo:** Conversa simples com ferramentas (tool use)
- **Quando e usado:** Funcionalidades de relatorio e analise via IA

### AgentGerenciador

- **Funcao:** Executa o ciclo completo de uma sequencia MG com raciocinio da IA
- **Usa API:** Sim (Claude Sonnet com loop agetico de ate 15 iteracoes)
- **Ferramentas disponiveis:** calcular_mg, registrar_operacao, ler_saldo, verificar_protecao, logar_alerta, gerar_relatorio
- **Quando e usado:** Loop Quotex (gerenciamento de MG com IA)

### AgentProtetor

- **Funcao:** Guardiao do capital — bloqueia operacoes quando limites sao atingidos
- **Usa API:** Nao (100% local, zero latencia)
- **Verifica antes de cada sequencia:**
  - Stop Loss % (ex: perda > 20% do saldo inicial)
  - Stop Loss R$ (ex: perdeu mais de R$300 na sessao)
  - Take Profit R$ (ex: lucrou R$500, para de operar)
  - Max sequencias de LOSS consecutivas (ex: 5 losses seguidos)
  - 3 ou mais Cenario 3 (loss completo do MG) na sessao
  - Max operacoes por sessao

### AgentAnalisador

- **Funcao:** Analisa metricas e recomenda continuar ou pausar
- **Usa API:** Nao (100% local)
- **Analisa:** taxa de acerto, drawdown, risco atual, tendencia da sessao
- **Resultado:** "continuar" ou "pausar" (nao bloqueia definitivamente, apenas recomenda)

### AgentVerificador

- **Funcao:** Verifica as condicoes do mercado antes de cada entrada
- **Usa API:** Nao (100% local)
- **Pode ser ativado/desativado** via `verificador_ativo` no config
- **Verifica antes de cada sinal:**
  - Janela de execucao: impede entradas fora do timing ideal
  - Volatilidade: bloqueia mercado excessivamente quieto
  - Dojis consecutivos: impede entrada em mercado indeciso
  - Payout em tempo real: bloqueia se payout caiu abaixo do minimo

### AgentQuotex

- **Funcao:** Ponte entre o sistema e a corretora Quotex via websocket
- **Usa API:** Nao (usa PyQuotex)
- **Responsabilidades:**
  - Conectar/desconectar da Quotex
  - Buscar saldo (demo ou real)
  - Buscar lista de ativos e payouts
  - Executar trades (buy)
  - Aguardar resultado (check_win)
  - Buscar candles historicos

---

## 3. Configuracoes

Todas as configuracoes ficam em `data/config.json` e sao editaveis pelo menu **[5] Config**.

### Conta

| Parametro | Padrao | Descricao |
| --- | --- | --- |
| `account_mode` | PRACTICE | Modo da conta: PRACTICE (demo) ou REAL |

### Operacao

| Parametro | Padrao | Descricao |
| --- | --- | --- |
| `entrada_padrao` | 10.0 | Valor base de entrada em R$ |
| `duracao_padrao` | 300 | Duracao padrao das operacoes em segundos |
| `niveis_mg` | 3 | Quantidade de niveis Martingale (entrada + MG1 + MG2 + ...) |
| `fator_correcao_mg` | false | Aplica fator 1/payout no calculo do MG para compensar comissao |
| `estrategia_ativa` | EMA_RSI | Estrategia tecnica usada no Loop Autonomo |

### Filtros de Ativo

| Parametro | Padrao | Descricao |
| --- | --- | --- |
| `tipo_ativo` | AMBOS | Filtrar por: OTC, NAO_OTC, ou AMBOS |
| `tipo_mercado` | AMBOS | Filtrar por: FOREX, CRIPTO, MATERIA_PRIMA, ACAO, ou AMBOS |
| `payout_minimo_pct` | 75 | Payout minimo aceitavel (%) nos loops Quotex e Autonomo |
| `payout_minimo_pct_telegram` | 75 | Payout minimo aceitavel (%) nos loops Telegram e Lista |
| `volatilidade_minima_pct` | 30 | Range atual minimo como % do range medio (0 = desativado) |

### Verificador de Mercado

| Parametro | Padrao | Descricao |
| --- | --- | --- |
| `verificador_ativo` | true | Ativa/desativa o AgentVerificador |
| `max_dojis_consecutivos` | 2 | Bloqueia entrada apos N dojis consecutivos |

### Protecao

| Parametro | Padrao | Descricao |
| --- | --- | --- |
| `stop_loss_pct` | 20.0 | Para se perder mais de X% do saldo inicial da sessao |
| `stop_loss_reais` | OFF | Para se perder mais de R$ X na sessao (0 = desativado) |
| `take_profit_reais` | OFF | Para ao lucrar R$ X na sessao (0 = desativado) |
| `max_loss_streak` | 5 | Para apos X losses consecutivos |
| `max_ops_sessao` | 50 | Maximo de sequencias. 0 = ilimitado |
| `saldo_inicial` | 1000.0 | Referencia para calculo do Stop Loss % |

### Conexao (avancado)

| Parametro | Padrao | Descricao |
| --- | --- | --- |
| `janela_execucao_seg` | 5 | Tolerancia em segundos para executar sinal fora do horario ideal |
| `intervalo_verificacao_seg` | 3 | Intervalo entre tentativas de reconexao |
| `timeout_operacao_seg` | 1800 | Tempo maximo para concluir toda uma sequencia MG |
| `timeout_resultado_seg` | 900 | Tempo maximo para receber resultado de uma operacao individual |
| `timeout_conexao_seg` | 120 | Tempo maximo para estabelecer conexao com a Quotex |
| `tentativas_reconexao` | 3 | Numero de tentativas de reconexao antes de desistir |

---

## 4. Sistema de Protecao

O **AgentProtetor** e verificado **antes de cada sequencia** em todos os loops.
E totalmente local (sem API), portanto e instantaneo.

### Camadas de protecao

#### Camada 1 — Stop Loss %

Compara o saldo atual com o saldo do inicio da sessao.

```text
saldo_atual < saldo_inicial * (1 - stop_loss_pct/100)
Exemplo: saldo_inicial=R$1000, stop_loss=20%
         bloqueia se saldo_atual < R$800
```

#### Camada 2 — Stop Loss R$

Valor absoluto de perda maxima na sessao.

```text
perda = saldo_inicial - saldo_atual
bloqueia se perda >= stop_loss_reais
```

#### Camada 3 — Take Profit R$

Para o sistema quando o objetivo de lucro e atingido.

```text
lucro_total >= take_profit_reais
```

#### Camada 4 — Loss Streak

Conta losses consecutivos. Reseta ao WIN.

```text
losses_consecutivos >= max_loss_streak
```

#### Camada 5 — Cenario 3 acumulados

Conta quantas vezes o MG completo foi perdido (todos os niveis).
Bloqueia ao atingir 3 Cenarios 3 na sessao.

#### Camada 6 — Max operacoes

```text
ops_sessao >= max_ops_sessao
```

### O que acontece ao bloquear

- O loop e **encerrado imediatamente**
- Um alerta e salvo em `data/alertas.json`
- A mensagem exata do motivo e exibida no terminal
- Para desbloquear manualmente: menu **[10] Desbloquear**

### Saldo sincronizado vs estimado

Nos loops que conectam a Quotex, o protetor usa o **saldo real da conta** para comparacao.

---

## 5. Sistema de Analise

O **AgentAnalisador** e consultado apos cada sequencia concluida (nos loops Quotex, Telegram, Lista e Autonomo).
Diferente do Protetor, ele **nao bloqueia** — apenas recomenda pausar.

### Metricas avaliadas

- Taxa de acerto da sessao atual
- Risco percentual de perda atual
- Quantidade de Cenarios 3
- Tendencia (melhorando ou piorando)

### Resultado possivel

- `continuar` — prossegue normalmente para a proxima iteracao
- `pausar` — encerra o loop com mensagem "Loop PAUSADO pelo Analisador"
- `ajustar` — continua mas exibe sugestoes de ajuste de parametros

---

## 6. Agente Verificador de Mercado

O **AgentVerificador** e uma camada adicional entre a analise de sinal e a execucao do trade.
Enquanto o AgentProtetor protege o capital (limites de perda/streak), o Verificador
analisa se as **condicoes do mercado** estao favoraveis para entrar.

### Ativacao

- Controlado por `verificador_ativo` no config (true/false)
- Recarregado automaticamente apos editar configuracoes (menu [5])
- Pode ser desativado para estrategias que ja possuem filtros proprios rigorosos

### Verificacoes realizadas

**1. Janela de execucao** — impede entradas fora do timing ideal definido por `janela_execucao_seg`.

**2. Volatilidade minima** — analisa as ultimas 3 velas. Se o range atual e muito pequeno em relacao
a media, o mercado esta quieto demais e o sinal e descartado.
Threshold: configuravel por `volatilidade_minima_pct` (0 = desativado).

**3. Dojis consecutivos** — conta quantos dojis ocorreram nas ultimas velas.
Doji = candle sem corpo (abertura ~= fechamento), indicando indecisao do mercado.
Bloqueia se >= `max_dojis_consecutivos`.

**4. Payout em tempo real** — verifica se o payout atual do ativo esta acima do minimo configurado.

### Diferenca entre Protetor e Verificador

| Agente | Protege contra | Quando atua | Acao ao bloquear |
| --- | --- | --- | --- |
| AgentProtetor | Perdas acumuladas, limites de risco | Antes de cada sequencia | Encerra o loop |
| AgentVerificador | Condicoes ruins de mercado | Antes de cada sinal | Descarta o sinal atual (loop continua) |

---

## 7. Martingale (MG)

O MG e o sistema de recuperacao de perdas. Ao perder uma entrada, o sistema entra
automaticamente no proximo nivel com um valor calculado para recuperar a perda anterior
e ainda gerar o lucro desejado.

### Formula de calculo

```text
proximo_valor = (perda_acumulada + lucro_desejado) / payout
```

### Cenarios possiveis

| Cenario | Evento | Resultado |
| --- | --- | --- |
| **Cenario 1** | WIN na entrada original | Melhor caso — lucro com menor risco |
| **Cenario 2** | WIN em nivel de MG | Recuperou perda + lucro desejado |
| **Cenario 3** | LOSS em todos os niveis | Pior caso — perda acumulada de todos os niveis |
| **DOJI** | Empate em qualquer nivel | Capital devolvido — sem lucro, sem perda |

### Exemplo com 3 niveis, entrada R$10, payout 85%

```text
Entrada:  R$10.00  --> WIN = +R$8.50 | LOSS = -R$10.00
MG1:      R$22.00  --> WIN = +R$8.70 | LOSS = -R$32.00 (acum)
MG2:      R$48.00  --> WIN = +R$8.80 | LOSS = -R$80.00 (acum)
```

### Impacto do payout no MG

Quanto menor o payout, maiores os valores de MG. Exemplo com mesmo R$10:

| Payout | Entrada | MG1 | MG2 |
| --- | --- | --- | --- |
| 90% | R$10 | R$21 | R$44 |
| 80% | R$10 | R$23 | R$50 |
| 65% | R$10 | R$31 | R$87 |

> Por isso o payout minimo configurado e critico: operar com payout baixo infla
> exponencialmente os valores de MG, aumentando muito o risco.

---

## 8. Loop Quotex

**Acesso:** Menu [1]
**Conexao Quotex:** Sim (websocket)
**Usa API Anthropic:** Sim (AgentGerenciador)
**Objetivo:** Operacao manual — usuario define ativo, direcao e valor; IA gerencia o MG

### Passo a passo (Quotex)

```text
1. Conecta na Quotex (com retry ate tentativas_reconexao)
2. Exibe saldo real da conta
3. Sincroniza AgentProtetor com saldo real
4. Lista ativos disponiveis filtrados por tipo_ativo, tipo_mercado e payout minimo
5. Usuario seleciona o ativo
6. Pergunta: Direcao (CALL ou PUT)
7. Pergunta: Valor de entrada (R$)
8. Pergunta: Max sequencias
9. Pergunta: Niveis MG
10. Pergunta: Duracao (segundos)
11. Busca payout atual do ativo selecionado

LOOP (repete ate max_sequencias ou Ctrl+C):
  a. Verifica AgentProtetor -> para se bloqueado
  b. AgentGerenciador calcula MG e decide entrada
  c. Executa trade na Quotex (buy)
  d. Aguarda resultado com timeout de timeout_resultado_seg
  e. WIN: registra, encerra sequencia
  f. DOJI: registra como empate, encerra sequencia
  g. LOSS: proximo nivel MG ou encerra (Cenario 3)
  h. Atualiza saldo real e sincroniza com Protetor
  i. Registra resultado em data/operacoes.json
  j. Consulta AgentAnalisador -> pausa se recomendado
  k. Proxima sequencia
```

### Notas (Quotex)

- O payout e capturado **uma unica vez** no inicio — nao e reatualizado durante o loop
- A IA (AgentGerenciador) toma decisoes dentro de cada sequencia
- **Shutdown gracioso:** primeiro Ctrl+C aguarda a operacao atual finalizar antes de parar

---

## 9. Loop Telegram

**Acesso:** Menu [2]
**Conexao Quotex:** Sim (websocket)
**Usa API Anthropic:** Sim (AgentTelegram para parsear sinais)
**Objetivo:** Escuta sinais de grupos/canais do Telegram e executa automaticamente

### Passo a passo (Telegram)

```text
1. Pergunta: Valor de entrada (R$)
2. Pergunta: Niveis MG
3. Pergunta: Payout minimo %
4. Pergunta: Duracao padrao (se sinal nao informar)
5. Conecta na Quotex
6. Conecta no Telegram (autenticacao via SMS na primeira vez)

LOOP PRINCIPAL (escuta mensagens indefinidamente):
  Ao receber mensagem:
  a. AgentTelegram parseia o texto (ativo, direcao, duracao, horario)
  b. Se nao for sinal valido -> ignora
  c. Verifica AgentProtetor -> pula sinal se bloqueado
  d. Verifica se ativo esta aberto na Quotex
  e. Verifica payout atual >= payout_minimo -> SKIP se abaixo
  f. Calcula MG com payout atual
  g. Executa trade na Quotex
  h. Aguarda resultado
  i. WIN/LOSS/DOJI -> registra em data/operacoes.json
  j. Consulta AgentAnalisador -> pausa se recomendado
  k. Volta a escutar mensagens
```

### Notas (Telegram)

- Roda **indefinidamente** ate Ctrl+C ou bloqueio do Protetor
- Payout e verificado a cada sinal (se cair, pula aquele sinal — nao troca de ativo)
- Sessao Telegram salva em `data/telegram_session.session` — nao deletar

---

## 10. Loop Lista

**Acesso:** Menu [3]
**Conexao Quotex:** Sim (websocket)
**Usa API Anthropic:** Nao
**Objetivo:** Executa uma lista pre-definida de sinais de um arquivo JSON

### Formato do arquivo sinais.json

```json
[
  {"ativo": "EURUSD_otc", "direcao": "call", "duracao": 60},
  {"ativo": "GBPUSD_otc", "direcao": "put",  "duracao": 300},
  {"ativo": "EURUSD_otc", "direcao": "call", "duracao": 60, "horario": "14:30"}
]
```

### Passo a passo (Lista)

```text
1. Pergunta: arquivo de sinais (padrao: data/sinais.json)
2. Valida e carrega a lista JSON
3. Pergunta: Valor de entrada (R$)
4. Pergunta: Niveis MG
5. Pergunta: Duracao padrao (se sinal nao informar)
6. Pergunta: Intervalo entre sinais (segundos)
7. Conecta na Quotex

LOOP (itera sobre cada sinal da lista):
  a. Verifica AgentProtetor -> para se bloqueado
  b. Se sinal tem horario -> aguarda o horario definido
  c. Verifica se ativo esta aberto
  d. Verifica payout >= payout_minimo -> SKIP se abaixo
  e. Calcula MG com payout atual
  f. Executa trade na Quotex
  g. Aguarda resultado
  h. WIN/LOSS/DOJI -> registra em data/operacoes.json
  i. Consulta AgentAnalisador -> pausa se recomendado
  j. Aguarda intervalo entre sinais
  k. Proximo sinal da lista

FIM DA LISTA -> encerra automaticamente
```

### Notas (Lista)

- Encerra sozinho ao consumir todos os sinais da lista
- Payout verificado a cada sinal — sinais com payout baixo sao pulados

---

## 11. Loop Autonomo

**Acesso:** Menu [4]
**Conexao Quotex:** Sim (websocket)
**Usa API Anthropic:** Nao
**Objetivo:** Analisa candles em tempo real com estrategia tecnica e opera automaticamente

### Passo a passo (Autonomo)

```text
1. Conecta na Quotex
2. Exibe saldo e sincroniza com AgentProtetor
3. Auto-selecao de ativo:
   - Exibe top-3 ativos com maior payout (filtrados por tipo_ativo, tipo_mercado)
   - Pergunta: usar o melhor automaticamente (S) ou escolher manualmente (n)?
4. Lista estrategias disponiveis com descricao e timeframe recomendado
5. Usuario seleciona a estrategia
6. Se duracao configurada difere do timeframe recomendado:
   - Exibe alerta de incompatibilidade de timeframe
   - Confirma se deseja continuar mesmo assim
7. Pergunta: Valor de entrada (R$)
8. Pergunta: Duracao do candle (padrao = timeframe recomendado)
9. Pergunta: Niveis MG

LOOP PRINCIPAL (roda indefinidamente ate Ctrl+C ou bloqueio):

  A. PROTECAO:
     Verifica AgentProtetor -> encerra se bloqueado

  B. SINCRONIZACAO DE CANDLE:
     Calcula tempo ate abertura do proximo candle e aguarda

  C. BUSCA DE DADOS (timeout 30s):
     Chama get_candles() com asyncio.wait_for(timeout=30)
     Se TIMEOUT -> testa internet -> aguarda ou retenta

  D. ANALISE DA ESTRATEGIA:
     Executa estrategia sobre os candles
     Aplica filtro de volatilidade minima (se configurado)
     Se sem sinal -> volta para A

  E. VERIFICACAO DE PAYOUT:
     Atualiza payout atual via get_payout()
     Se abaixo do minimo -> busca novo ativo ou descarta sinal

  F. VERIFICADOR DE MERCADO:
     AgentVerificador.verificar(candles, cfg, payout_atual)
     Se bloqueado -> loga motivo, descarta sinal, continua loop

  G. EXECUCAO (MG):
     Para cada nivel (entrada, mg1, mg2, ...):
       WIN -> encerra sequencia (Cenario 1 ou 2)
       DOJI -> encerra sem perda/lucro
       LOSS -> proximo nivel MG
       LOSS no ultimo nivel -> Cenario 3

  H. POS-OPERACAO:
     Atualiza saldo real e sincroniza com AgentProtetor
     Registra em data/operacoes.json
     Consulta AgentAnalisador -> pausa se recomendado
     Volta para A
```

### Notas (Autonomo)

- Unico loop com analise tecnica automatica de candles
- O ativo e analisado no **horario exato de fechamento do candle** para maxima precisao
- Sincronizacao de tempo: `ceil(seg_atual / duracao) * duracao`
- Roda indefinidamente ate Ctrl+C, bloqueio do Protetor ou pausa do Analisador
- Nao usa a API Anthropic (funciona sem custo de tokens)

---

## 12. Backteste

**Acesso:** Menu [11]
**Conexao Quotex:** Sim (para buscar candles historicos reais)
**Usa API Anthropic:** Nao
**Objetivo:** Validar a acuracia de uma estrategia antes de operar com dinheiro real

O Backteste nao executa nenhum trade real e nao modifica `data/operacoes.json`.

### Passo a passo (Backteste)

```text
1. Conecta na Quotex (conta PRACTICE)
2. Seleciona ativo
3. Seleciona estrategia
4. Pergunta: Duracao do candle (segundos)
5. Pergunta: Entrada simulada (R$)
6. Pergunta: Simular Martingale? (s/N)
7. Busca ~1000 candles historicos reais via get_candles()

WALK-FORWARD SIMULATION:
  Para cada posicao i de 0 ate N-2:
    Executa estrategia sobre candles[:i+1]
    Se gerou sinal: compara close[i] vs close[i+1]
    WIN se direcao correta | LOSS se errada | DOJI se igual
    Se MG ativo: simula ciclo de recuperacao (C1/C2/C3)

RESULTADO EXIBIDO:
  - Candles analisados e periodo coberto
  - Total de sinais e frequencia
  - Wins, Losses, Dojis com percentuais
  - Taxa de acerto e lucro simulado
  - Max sequencia de wins e losses
  - Se MG: ciclos C1, C2, C3 separados
  - Avaliacao automatica

8. Pergunta: Salvar em logs/backteste_NOME_ATIVO.txt? (s/N)
```

### Avaliacao automatica

| Taxa de acerto | Avaliacao | Observacao |
| --- | --- | --- |
| >= 60% | EXCELENTE | Boa margem acima do break-even |
| 55-59% | BOM | Lucrativo com payout tipico (80-85%) |
| 50-54% | NEUTRO | Depende muito do payout |
| < 50% | FRACO | Nao recomendado para uso real |

> **Break-even com payout 85%:** taxa minima para nao perder = 1 / (1 + 0.85) = 54.1%

### Notas (Backteste)

- Usa candles historicos **reais** da Quotex — resultado mais fiel que dados sinteticos
- Walk-forward: cada sinal e decidido apenas com candles passados (sem lookahead)
- O payout usado e o payout real do ativo no momento da consulta

---

## 13. Estrategias Tecnicas

Todas as estrategias vivem em `estrategias.py` e seguem a mesma interface:

```python
def minha_estrategia(candles: list[dict], cfg: dict) -> dict:
    return {
        "sinal":       "call" | "put" | None,
        "motivo":      str,
        "indicadores": dict,
    }
```

### Convencao de nomenclatura

O sufixo indica o perfil de risco da estrategia:

| Sufixo | Perfil | Caracteristica |
| --- | --- | --- |
| `_C` | Conservador | Menos sinais, mais filtros, maior seletividade |
| `_M` | Moderado | Balanco entre frequencia e seletividade |
| `_A` | Agressivo | Mais sinais, menos filtros, maior frequencia |

### Estrategias disponiveis

#### EMA_RSI (Moderado)

- **Indicadores:** EMA9, EMA21, RSI(14)
- **Timeframe:** M1 (60s)
- **Logica:** EMA9 cruza EMA21 + RSI confirma sobrecompra/sobrevenda + vela de confirmacao
- **Uso:** Reversoes em mercados com tendencia clara

#### REVERSAO_SMA_M (Moderado)

- **Indicadores:** SMA5, SMA21, corpo medio, range medio
- **Timeframe:** M1 (60s)
- **Logica:** Reversao de candle + confirmacao de tendencia pelas medias + forca da vela + movimento real
- **Diferencial:** Filtra mercados lateralizados — exige movimento real

#### FRACTAL_MACD_M (Moderado)

- **Indicadores:** Buffer1 (close - SMA34), Buffer2 (WMA5 do Buffer1), Fractal de 3 barras
- **Timeframe:** M1 (60s)
- **Logica:** Cruzamento de buffers (momentum) + padrao geometrico de fractal de reversao
- **Diferencial:** Combina momentum de medias com padrao de preco

#### MACD_RSI_C (Conservador)

- **Indicadores:** MACD mini, RSI(14), corpo, range medio
- **Timeframe:** M1 (60s)
- **Logica:** 4 filtros obrigatorios — MACD + RSI + corpo relevante + movimento real
- **Diferencial:** Menos sinais, mais confiaveis — ideal para quem prefere operar menos

#### BB_RSI_C (Conservador)

- **Indicadores:** Bollinger Bands(20,2), RSI(14)
- **Timeframe:** M5 (300s)
- **Logica:** Preco toca banda superior/inferior + RSI confirma sobrecompra/sobrevenda
- **Uso:** Reversoes nas extremidades das bandas

#### EMA_RSI_C (Conservador)

- **Indicadores:** EMA(50), RSI(14)
- **Timeframe:** M5 (300s)
- **Logica:** Pullback para EMA de longo prazo com RSI favoravel
- **Uso:** Entradas em favor da tendencia principal

#### TRIPLE_CONFIRM_C (Conservador)

- **Indicadores:** RSI(14), MACD, Bollinger Bands
- **Timeframe:** M15 (900s)
- **Logica:** Triple confirmacao — os tres indicadores alinham na mesma direcao
- **Diferencial:** Maxima seletividade, poucos sinais por sessao, alta confiabilidade

#### MACD_EMA_M (Moderado)

- **Indicadores:** MACD(12,26,9), EMA(50)
- **Timeframe:** M5 (300s)
- **Logica:** Cruzamento MACD + preco acima/abaixo da EMA50
- **Uso:** Tendencia com confirmacao de momentum

#### ENGOLFO_M (Moderado)

- **Indicadores:** Padrao de candle engolfante, RSI(14)
- **Timeframe:** M1 (60s)
- **Logica:** Candle atual engloba o anterior (engolfo) + RSI confirma a direcao
- **Uso:** Price action — reversoes rapidas

#### ESTOCASTICO_A (Agressivo)

- **Indicadores:** Estocastico(5,3,3)
- **Timeframe:** M1 (60s)
- **Logica:** Cruzamento das linhas %K e %D em zonas de extremo (abaixo de 20 ou acima de 80)
- **Diferencial:** Alta frequencia de sinais — calibrar com backteste antes de usar

#### BB_SQUEEZE_A (Agressivo)

- **Indicadores:** Bollinger Bands, largura das bandas
- **Timeframe:** M15 (900s)
- **Logica:** Bandas comprimidas (squeeze) seguidas de rompimento com candle de forca
- **Uso:** Captura explosoes de volatilidade apos periodos de consolidacao

#### TRES_CANDLES_A (Agressivo)

- **Indicadores:** 3 candles consecutivos, RSI(14)
- **Timeframe:** M1 (60s)
- **Logica:** 3 velas consecutivas na mesma direcao indicam exaustao — entrada na reversao
- **Diferencial:** Simplicidade e alta frequencia

### Comparativo das estrategias

| Estrategia | Perfil | TF | Freq. sinais | Seletividade |
| --- | --- | --- | --- | --- |
| EMA_RSI | Moderado | M1 | Baixa | Alta |
| REVERSAO_SMA_M | Moderado | M1 | Media | Media |
| FRACTAL_MACD_M | Moderado | M1 | Media | Media |
| MACD_RSI_C | Conservador | M1 | Baixa | Muito alta |
| BB_RSI_C | Conservador | M5 | Baixa | Alta |
| EMA_RSI_C | Conservador | M5 | Baixa | Alta |
| TRIPLE_CONFIRM_C | Conservador | M15 | Muito baixa | Maxima |
| MACD_EMA_M | Moderado | M5 | Media | Media |
| ENGOLFO_M | Moderado | M1 | Media | Media |
| ESTOCASTICO_A | Agressivo | M1 | Alta | Baixa |
| BB_SQUEEZE_A | Agressivo | M15 | Baixa | Media |
| TRES_CANDLES_A | Agressivo | M1 | Alta | Baixa |

### Filtro de volatilidade (transversal)

Independente da estrategia escolhida, o parametro `volatilidade_minima_pct` aplica
um filtro adicional sobre o sinal gerado:

```text
range_atual = high - low da vela atual
range_medio = media dos ranges das ultimas 20 velas
ratio = (range_atual / range_medio) * 100

Se ratio < volatilidade_minima_pct -> sinal descartado
```

Configure `volatilidade_minima_pct = 0` para desativar.

### Como adicionar uma nova estrategia

1. Implemente a funcao em `estrategias.py` seguindo a interface padrao
2. Registre em `ESTRATEGIAS`:

```python
ESTRATEGIAS["MINHA_ESTRATEGIA_M"] = minha_estrategia_m
```

1. Registre metadados em `ESTRATEGIAS_META`:

```python
ESTRATEGIAS_META["MINHA_ESTRATEGIA_M"] = {
    "timeframe_rec": 300,
    "descricao": "[M] Descricao breve | M5 (300s)",
}
```

1. O Loop Autonomo exibe automaticamente na lista de selecao — sem alterar main.py

---

## 14. Estabilidade e Resiliencia

### Verificacao de internet na inicializacao

Ao executar `main.py`, antes de qualquer outra acao, o sistema testa a conexao:

```text
[REDE] Testando conexao com a internet... OK (42ms)
```

Metodo: conexao TCP na porta 53 do DNS publico do Google (8.8.8.8).
Sem internet -> sistema aborta com mensagem clara antes de tentar conectar a Quotex.

### Timeout em get_candles

O PyQuotex pode "travar" silenciosamente se o servidor parar de responder durante
uma requisicao de candles — sem lancar excecao, ficando preso indefinidamente.

Solucao implementada:

```text
get_candles() -> asyncio.wait_for(timeout=30s)
Se nao responder em 30s:
  -> Com internet: "instabilidade no servidor" -> aguarda proximo candle
  -> Sem internet: aguarda 30s -> tenta novamente
```

### Reconexao automatica durante operacao

Se uma operacao ativa perder conexao:

```text
1. [TIMEOUT] Resultado nao recebido em Xs
2. Tenta reconectar com _conectar_com_retry()
3. Se reconectou: pula o nivel atual e continua o loop
4. Se nao reconectou: encerra o loop
```

### Shutdown gracioso (Ctrl+C)

```text
1o Ctrl+C -> flag de encerramento ativada
  -> "Aguardando operacao atual finalizar..."
  -> Operacao em andamento completa normalmente
  -> Resultado registrado em data/operacoes.json
  -> Loop encerra na proxima iteracao

2o Ctrl+C -> saida imediata
```

### Deteccao de DOJI

Quando a corretora devolve o capital (empate):

- `check_win()` retorna falso (mesmo comportamento que LOSS)
- `get_profit()` retorna 0.0 (diferente de LOSS que retorna negativo)
- O sistema detecta: `if not win and profit == 0.0` -> DOJI
- Tratamento: encerra a sequencia MG sem registrar como loss
- Nao afeta loss streak, nao afeta contagem de Cenario 3

---

## 15. Arquivos do Sistema

### Arquivos principais

| Arquivo | Funcao | Editavel? |
| --- | --- | --- |
| `main.py` | Ponto de entrada. CLI, loops, menu | Nao (codigo) |
| `agents.py` | Logica dos 6 agentes | Nao (codigo) |
| `skills.py` | Ferramentas dos agentes (tools) | Nao (codigo) |
| `estrategias.py` | Estrategias tecnicas | Sim (adicionar estrategias) |
| `.env` | Credenciais (API keys, login) | Editor de texto |

### Pasta data/

| Arquivo | Conteudo | Observacao |
| --- | --- | --- |
| `data/config.json` | Parametros de risco e operacao | Via menu [5] |
| `data/operacoes.json` | Historico completo de operacoes | Cresce com o uso |
| `data/alertas.json` | Alertas do AgentProtetor | Registra bloqueios e avisos |
| `data/sinais.json` | Lista de sinais para o Loop Lista | Criado manualmente pelo usuario |
| `data/telegram_session.session` | Autenticacao Telegram | Nao deletar |

### Pasta logs/

| Arquivo | Conteudo | Observacao |
| --- | --- | --- |
| `logs/sessao.log` | Log rotativo diario da sessao | Rotacao automatica a meia-noite |
| `logs/relatorio.txt` | Ultimo relatorio gerado | Sobrescrito a cada geracao |
| `logs/backteste_*.txt` | Resultados de backteste salvos | Criado ao confirmar salvar |

### Pasta docs/

| Arquivo | Conteudo |
| --- | --- |
| `docs/INSTALACAO.md` | Guia de instalacao e configuracao |
| `docs/MANUAL.md` | Este arquivo — manual completo |
| `docs/catalogo_estrategias.md` | Descricao detalhada de cada estrategia |

### Crescimento do operacoes.json

O arquivo cresce a cada operacao executada e nunca e podado automaticamente.

| Operacoes | Tamanho estimado | Impacto |
| --- | --- | --- |
| ate 500 | < 1MB | Imperceptivel |
| 500 a 2.000 | 1-5MB | Minimo |
| 2.000 a 10.000 | 5-25MB | Leve lentidao nos relatorios |
| acima de 10.000 | > 25MB | Recomenda-se arquivar manualmente |

Para arquivar: renomeie `data/operacoes.json` para `data/operacoes_2025.json` e o sistema
criara um novo arquivo vazio na proxima execucao.

---

Ultima atualizacao: fevereiro de 2026
