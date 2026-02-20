# AgentTradSyst — Manual do Sistema

Documento de referência completo: arquitetura, funcionalidades, loops e comportamentos.

---

## Sumário

1. [Visão Geral](#1-visao-geral)
2. [Os 5 Agentes](#2-os-5-agentes)
3. [Configurações](#3-configuracoes)
4. [Sistema de Proteção](#4-sistema-de-protecao)
5. [Sistema de Análise](#5-sistema-de-analise)
6. [Martingale (MG)](#6-martingale-mg)
7. [Loop Simulação](#7-loop-simulacao)
8. [Loop Quotex](#8-loop-quotex)
9. [Loop Telegram](#9-loop-telegram)
10. [Loop Lista](#10-loop-lista)
11. [Loop Autônomo](#11-loop-autonomo)
12. [Estratégias Técnicas](#12-estrategias-tecnicas)
13. [Estabilidade e Resiliência](#13-estabilidade-e-resiliencia)
14. [Arquivos do Sistema](#14-arquivos-do-sistema)

---

## 1. Visao Geral

O AgentTradSyst é um sistema de trading automatizado que combina:
- **Conexão real** com a corretora Quotex via websocket (PyQuotex)
- **Inteligência artificial** via API Anthropic (Claude) para análise e gerenciamento
- **5 modos de operação** (loops) para diferentes estratégias de uso
- **Proteção de capital** automática com múltiplas camadas de segurança

### Fluxo geral de uma operação

```
Sinal de entrada
      |
      v
AgentProtetor verifica limites  -->  BLOQUEADO? --> Para o loop
      |
      v (pode continuar)
AgentAnalisador avalia métricas -->  PAUSAR?    --> Pausa sequência
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
Registra resultado --> operacoes.json
      |
      v
Próxima iteração do loop
```

---

## 2. Os 5 Agentes

### AgentTrading
- **Função:** Chat interativo com o Claude via API Anthropic
- **Usa API:** Sim (Claude Sonnet)
- **Modo:** Conversa simples com ferramentas (tool use)
- **Quando é usado:** Modo Chat (opção 6 do menu)

### AgentGerenciador
- **Função:** Executa o ciclo completo de uma sequência MG com raciocínio da IA
- **Usa API:** Sim (Claude Sonnet com loop agentico de até 15 iterações)
- **Ferramentas disponíveis:** calcular_mg, registrar_operacao, ler_saldo, verificar_protecao, logar_alerta, gerar_relatorio
- **Quando é usado:** Loop Quotex (modo manual com IA)

### AgentProtetor
- **Função:** Guardião do capital — bloqueia operações quando limites são atingidos
- **Usa API:** Não (100% local, zero latência)
- **Verifica antes de cada sequência:**
  - Stop Loss % (ex: perda > 20% do saldo inicial)
  - Stop Loss R$ (ex: perdeu mais de R$300 na sessão)
  - Take Profit R$ (ex: lucrou R$500, para de operar)
  - Max sequências de LOSS consecutivas (ex: 5 losses seguidos)
  - 3 ou mais Cenário 3 (loss completo do MG) na sessão
  - Max operações por sessão (Simulação e Quotex)

### AgentAnalisador
- **Função:** Analisa métricas e recomenda continuar ou pausar
- **Usa API:** Não (100% local)
- **Analisa:** taxa de acerto, drawdown, risco atual, tendência da sessão
- **Resultado:** "continuar" ou "pausar" (não bloqueia definitivamente, apenas recomenda)

### AgentQuotex
- **Função:** Ponte entre o sistema e a corretora Quotex via websocket
- **Usa API:** Não (usa PyQuotex)
- **Responsabilidades:**
  - Conectar/desconectar da Quotex
  - Buscar saldo (demo ou real)
  - Buscar lista de ativos e payouts
  - Executar trades (buy)
  - Aguardar resultado (check_win)
  - Buscar candles históricos

---

## 3. Configuracoes

Todas as configurações ficam em `config.json` e são editáveis pelo menu **[6] Config**.

### Conta
| Parâmetro | Padrão | Descrição |
|---|---|---|
| `account_mode` | PRACTICE | Modo da conta: PRACTICE (demo) ou REAL |

### Operação
| Parâmetro | Padrão | Descrição |
|---|---|---|
| `entrada_padrao` | 10.0 | Valor base de entrada em R$ |
| `duracao_padrao` | 300 | Duração padrão das operações em segundos |
| `niveis_mg` | 3 | Quantidade de níveis Martingale (entrada + MG1 + MG2 + ...) |
| `fator_correcao_mg` | false | Aplica fator 1/payout no cálculo do MG para compensar comissão |
| `estrategia_ativa` | NENHUMA | Estratégia técnica usada no Loop Autônomo |

### Filtros de Ativo
| Parâmetro | Padrão | Descrição |
|---|---|---|
| `tipo_ativo` | AMBOS | Filtrar por: OTC, NAO_OTC, ou AMBOS |
| `tipo_mercado` | AMBOS | Filtrar por: FOREX, CRIPTO, MATERIA_PRIMA, ACAO, ou AMBOS |
| `payout_minimo_pct` | 75 | Payout mínimo aceitável (%) nos loops Quotex e Autônomo |
| `payout_minimo_pct_telegram` | 75 | Payout mínimo aceitável (%) nos loops Telegram e Lista |

### Proteção
| Parâmetro | Padrão | Descrição |
|---|---|---|
| `stop_loss_pct` | 20.0 | Para se perder mais de X% do saldo inicial da sessão |
| `stop_loss_reais` | OFF | Para se perder mais de R$ X na sessão (0 = desativado) |
| `take_profit_reais` | OFF | Para ao lucrar R$ X na sessão (0 = desativado) |
| `max_loss_streak` | 5 | Para após X losses consecutivos |
| `max_ops_sessao` | 50 | Máximo de sequências (Simulação e Quotex). 0 = ilimitado |
| `saldo_inicial` | 1000.0 | Referência para cálculo do Stop Loss % |

### Conexão (avançado)
| Parâmetro | Padrão | Descrição |
|---|---|---|
| `janela_execucao_seg` | 5 | Tolerância em segundos para executar sinal fora do horário ideal |
| `intervalo_verificacao_seg` | 3 | Intervalo entre tentativas de reconexão |
| `timeout_operacao_seg` | 1800 | Tempo máximo para concluir toda uma sequência MG |
| `timeout_resultado_seg` | 900 | Tempo máximo para receber resultado de uma operação individual |
| `timeout_conexao_seg` | 30 | Tempo máximo para estabelecer conexão com a Quotex |
| `tentativas_reconexao` | 3 | Número de tentativas de reconexão antes de desistir |

---

## 4. Sistema de Protecao

O **AgentProtetor** é verificado **antes de cada sequência** em todos os loops.
É totalmente local (sem API), portanto é instantâneo.

### Camadas de proteção (ordem de verificação)

**Camada 1 — Stop Loss %**
Compara o saldo atual com o saldo do início da sessão.
```
saldo_atual < saldo_inicial * (1 - stop_loss_pct/100)
Exemplo: saldo_inicial=R$1000, stop_loss=20%
         bloqueia se saldo_atual < R$800
```

**Camada 2 — Stop Loss R$**
Valor absoluto de perda máxima na sessão.
```
perda = saldo_inicial - saldo_atual
bloqueia se perda >= stop_loss_reais
```

**Camada 3 — Take Profit R$**
Para o sistema quando o objetivo de lucro é atingido.
```
lucro_total >= take_profit_reais
```

**Camada 4 — Loss Streak**
Conta losses consecutivos. Reseta ao WIN.
```
losses_consecutivos >= max_loss_streak
```

**Camada 5 — Cenário 3 acumulados**
Conta quantas vezes o MG completo foi perdido (todos os níveis).
Bloqueia ao atingir 3 Cenários 3 na sessão.

**Camada 6 — Max operações (Simulação e Quotex)**
```
ops_sessao >= max_ops_sessao
```

### O que acontece ao bloquear

- O loop é **encerrado imediatamente**
- Um alerta é salvo em `alertas.json`
- A mensagem exata do motivo é exibida no terminal
- Para desbloquear manualmente: menu **[r] Reiniciar**

### Saldo sincronizado vs estimado

Nos loops que conectam à Quotex, o protetor usa o **saldo real da conta** para comparação.
No Loop Simulação (sem Quotex), usa o saldo estimado calculado pelo histórico de `operacoes.json`.

---

## 5. Sistema de Analise

O **AgentAnalisador** é consultado após cada sequência concluída (nos loops Quotex e Autônomo).
Diferente do Protetor, ele **não bloqueia** — apenas recomenda pausar.

### Métricas avaliadas
- Taxa de acerto da sessão atual
- Risco percentual de perda atual
- Quantidade de Cenários 3
- Tendência (melhorando ou piorando)

### Resultado possível
- `continuar` — prossegue normalmente para a próxima iteração
- `pausar` — encerra o loop com mensagem "Loop PAUSADO pelo Analisador"

---

## 6. Martingale (MG)

O MG é o sistema de recuperação de perdas. Ao perder uma entrada, o sistema entra
automaticamente no próximo nível com um valor calculado para recuperar a perda anterior
e ainda gerar o lucro desejado.

### Fórmula de cálculo

```
proximo_valor = (perda_acumulada + lucro_desejado) / payout
```

### Cenários possíveis

| Cenário | Evento | Resultado |
|---|---|---|
| **Cenário 1** | WIN na entrada original | Melhor caso — lucro com menor risco |
| **Cenário 2** | WIN em nível de MG | Recuperou perda + lucro desejado |
| **Cenário 3** | LOSS em todos os níveis | Pior caso — perda acumulada de todos os níveis |
| **DOJI** | Empate em qualquer nível | Capital devolvido — sem lucro, sem perda |

### Exemplo com 3 níveis, entrada R$10, payout 85%

```
Entrada:  R$10.00  → WIN = +R$8.50 | LOSS = -R$10.00
MG1:      R$22.00  → WIN = +R$8.70 | LOSS = -R$32.00 (acum)
MG2:      R$48.00  → WIN = +R$8.80 | LOSS = -R$80.00 (acum)
```

### Impacto do payout no MG

Quanto menor o payout, maiores os valores de MG. Exemplo com mesmo R$10:

| Payout | Entrada | MG1 | MG2 |
|---|---|---|---|
| 90% | R$10 | R$21 | R$44 |
| 80% | R$10 | R$23 | R$50 |
| 65% | R$10 | R$31 | R$87 |

> Por isso o payout mínimo configurado é crítico: operar com payout baixo infla
> exponencialmente os valores de MG, aumentando muito o risco.

---

## 7. Loop Simulacao

**Acesso:** Menu [1]
**Conexão Quotex:** Não
**Usa API Anthropic:** Não
**Objetivo:** Testar o sistema localmente com resultados aleatórios

### Passo a passo

```
1. Pergunta: Ativo (ex: EURUSD_otc)
2. Pergunta: Direção (CALL ou PUT)
3. Pergunta: Valor de entrada (R$)
4. Pergunta: Max sequências (padrão: config)
5. Pergunta: Níveis MG
6. Pergunta: Intervalo entre sequências (segundos)

LOOP (repete até max_sequencias ou Ctrl+C):
  a. Verifica AgentProtetor → bloqueia se limite atingido
  b. Exibe número da sequência atual
  c. Simula WIN/LOSS com probabilidade aleatória (payout simulado 85%)
  d. Se LOSS e tem nível MG → próximo nível
  e. Se DOJI → sequência encerrada sem registro de loss
  f. Registra resultado em operacoes.json
  g. Consulta AgentAnalisador → pausa se recomendado
  h. Aguarda intervalo configurado
  i. Próxima sequência
```

### Particularidades
- Resultados são **aleatórios** (não refletem mercado real)
- O saldo é **estimado** via operacoes.json (não usa saldo real)
- Útil para testar a lógica do MG e dos limites de proteção
- **Max operações** é respeitado (único loop onde isso limita o total de sequências)

---

## 8. Loop Quotex

**Acesso:** Menu [2]
**Conexão Quotex:** Sim (websocket)
**Usa API Anthropic:** Sim (AgentGerenciador)
**Objetivo:** Operação manual — usuário define ativo, direção e valor; IA gerencia o MG

### Passo a passo

```
1. Conecta na Quotex (com retry até tentativas_reconexao)
2. Exibe saldo real da conta
3. Sincroniza AgentProtetor com saldo real
4. Lista ativos disponíveis filtrados por:
   - tipo_ativo (OTC/NAO_OTC/AMBOS)
   - tipo_mercado (FOREX/CRIPTO/etc)
   - payout >= payout_minimo_pct
   - Ordenados por payout decrescente
5. Usuário seleciona o ativo
6. Pergunta: Direção (CALL ou PUT)
7. Pergunta: Valor de entrada (R$)
8. Pergunta: Max sequências
9. Pergunta: Níveis MG
10. Pergunta: Duração (segundos)
11. Busca payout atual do ativo selecionado

LOOP (repete até max_sequencias ou Ctrl+C):
  a. Verifica AgentProtetor → para se bloqueado
  b. Verifica janela de execução (se horário válido)
  c. AgentGerenciador calcula MG e decide entrada
  d. Executa trade na Quotex (buy)
  e. Aguarda resultado com timeout de timeout_resultado_seg
  f. Se TIMEOUT → tenta reconectar → pula nível atual
  g. WIN: registra, encerra sequência
  h. DOJI: registra como empate, encerra sequência
  i. LOSS: próximo nível MG ou encerra (Cenário 3)
  j. Atualiza saldo real e sincroniza com Protetor
  k. Registra resultado em operacoes.json
  l. AgentGerenciador gera relatório parcial
  m. Consulta AgentAnalisador → pausa se recomendado
  n. Aguarda intervalo entre sequências
  o. Próxima sequência
```

### Particularidades
- O payout é capturado **uma única vez** no início — não é reatualizado durante o loop
- A IA (AgentGerenciador) toma decisões dentro de cada sequência
- **Shutdown gracioso:** primeiro Ctrl+C aguarda a operação atual finalizar antes de parar

---

## 9. Loop Telegram

**Acesso:** Menu [3]
**Conexão Quotex:** Sim (websocket)
**Usa API Anthropic:** Sim (AgentTelegram para parsear sinais)
**Objetivo:** Escuta sinais de grupos/canais do Telegram e executa automaticamente

### Passo a passo

```
1. Pergunta: Valor de entrada (R$)
2. Pergunta: Níveis MG
3. Pergunta: Payout mínimo % (específico para Telegram)
4. Pergunta: Duração padrão (se sinal não informar)
5. Conecta na Quotex
6. Conecta no Telegram (autenticação via SMS na primeira vez)
7. Identifica o bot de sinais configurado

LOOP PRINCIPAL (escuta mensagens indefinidamente):
  Ao receber mensagem:
  a. AgentTelegram (Claude Haiku) parseia o texto:
     - Identifica ativo, direção, duração
     - Verifica se está dentro da janela de execução (janela_execucao_seg)
     - Verifica offset de fuso horário (time_offset)
  b. Se mensagem não for sinal válido → ignora
  c. Verifica AgentProtetor → pula sinal se bloqueado
  d. Verifica se ativo está aberto na Quotex
  e. Verifica payout atual do ativo >= payout_minimo_pct_telegram
     → Se payout abaixo: [SKIP] pula este sinal
  f. Calcula MG com payout atual
  g. Executa trade na Quotex
  h. Aguarda resultado
  i. WIN/LOSS/DOJI → registra em operacoes.json
  j. Consulta AgentAnalisador → pausa se recomendado
  k. Volta a escutar mensagens
```

### Particularidades
- Roda **indefinidamente** até Ctrl+C ou bloqueio do Protetor
- Não tem max_ops_sessao
- Payout é verificado a cada sinal (se cair, pula aquele sinal — não troca de ativo)
- Offset de fuso horário configurável para grupos com horários diferentes do seu
- Sessão Telegram salva em `telegram_session.session` — não deletar

---

## 10. Loop Lista

**Acesso:** Menu [4]
**Conexão Quotex:** Sim (websocket)
**Usa API Anthropic:** Não
**Objetivo:** Executa uma lista pré-definida de sinais de um arquivo JSON

### Formato do arquivo sinais.json

```json
[
  {"ativo": "EURUSD_otc", "direcao": "call", "duracao": 60},
  {"ativo": "GBPUSD_otc", "direcao": "put",  "duracao": 300},
  {"ativo": "EURUSD_otc", "direcao": "call", "duracao": 60, "horario": "14:30"}
]
```

### Passo a passo

```
1. Pergunta: arquivo de sinais (padrão: sinais.json)
2. Valida e carrega a lista JSON
3. Pergunta: Valor de entrada (R$)
4. Pergunta: Níveis MG
5. Pergunta: Duração padrão (se sinal não informar)
6. Pergunta: Intervalo entre sinais (segundos)
7. Conecta na Quotex

LOOP (itera sobre cada sinal da lista):
  a. Verifica AgentProtetor → para se bloqueado
  b. Se sinal tem horário → aguarda o horário definido
  c. Verifica janela de execução
  d. Verifica se ativo está aberto
  e. Verifica payout >= payout_minimo_pct
     → Se payout abaixo: [SKIP] pula para próximo sinal
  f. Calcula MG com payout atual
  g. Executa trade na Quotex
  h. Aguarda resultado
  i. WIN/LOSS/DOJI → registra em operacoes.json
  j. Consulta AgentAnalisador → pausa se recomendado
  k. Aguarda intervalo entre sinais
  l. Próximo sinal da lista

FIM DA LISTA → encerra automaticamente
```

### Particularidades
- Encerra sozinho ao consumir todos os sinais da lista
- Útil para backtesting com sinais históricos conhecidos
- Payout verificado a cada sinal — sinais com payout baixo são pulados

---

## 11. Loop Autonomo

**Acesso:** Menu [5]
**Conexão Quotex:** Sim (websocket)
**Usa API Anthropic:** Não
**Objetivo:** Analisa candles em tempo real com estratégia técnica e opera automaticamente

### Passo a passo

```
1. Conecta na Quotex
2. Exibe saldo e sincroniza com AgentProtetor
3. Lista ativos disponíveis (filtrados por tipo_ativo, tipo_mercado, payout_min)
4. Usuário seleciona o ativo
5. Lista estratégias disponíveis com descrição e timeframe recomendado
6. Usuário seleciona a estratégia
7. Pergunta: Valor de entrada (R$)
8. Pergunta: Duração do candle (padrão = timeframe recomendado pela estratégia)
9. Pergunta: Níveis MG
10. Verifica se ativo está aberto
11. Captura payout inicial

LOOP PRINCIPAL (roda indefinidamente até Ctrl+C ou bloqueio):

  A. PROTEÇÃO:
     a. Verifica AgentProtetor → encerra se bloqueado

  B. SINCRONIZAÇÃO DE CANDLE:
     b. Calcula tempo até abertura do próximo candle
     c. Exibe "[CANDLE] Aguardando abertura M1: HH:MM:SS (Xs)"
     d. Dorme até o horário exato de fechamento do candle

  C. BUSCA DE DADOS (com timeout de 30s):
     e. Chama get_candles() com asyncio.wait_for(timeout=30)
     f. Se TIMEOUT (servidor não responde em 30s):
        → Testa internet
        → Sem internet: aguarda 30s, tenta novamente
        → Com internet: instabilidade do servidor, aguarda próximo candle
     g. Se candles vazios:
        → Testa internet
        → Sem internet: aguarda 30s
        → Com internet: mercado sem dados, aguarda próximo candle

  D. ANÁLISE DA ESTRATÉGIA:
     h. Executa a estratégia selecionada sobre os candles
     i. Se sem sinal → exibe indicadores e volta para A
     j. Se sinal (CALL ou PUT) → exibe destaque com indicadores

  E. VERIFICAÇÃO DE PAYOUT:
     k. Atualiza payout atual do ativo via get_payout()
     l. Se payout < payout_minimo_pct:
        → [PAYOUT] Avisa que payout caiu abaixo do mínimo
        → Busca todos os ativos com payout >= mínimo (ordenados por maior payout)
        → Se encontrou alternativa:
           · Troca ativo principal (asset + asset_real) para o melhor disponível
           · Descarta o sinal atual (era análise do ativo anterior — não vale)
           · A partir do próximo candle, analisa o novo ativo principal
        → Se não encontrou alternativa:
           · Descarta o sinal. Aguarda próximo candle com o mesmo ativo.
        → Em ambos os casos: continue (vai para próximo candle)

  F. EXECUÇÃO (MG):
     m. Calcula MG com payout atual
     n. Para cada nível (entrada, mg1, mg2, ...):
        - Executa trade na Quotex com asyncio.wait_for(timeout=timeout_resultado_seg)
        - Se TIMEOUT → tenta reconectar → pula nível atual
        - WIN → encerra sequência (Cenário 1 ou 2)
        - DOJI → encerra sequência sem perda/lucro
        - LOSS → próximo nível MG
        - LOSS no último nível → Cenário 3

  G. PÓS-OPERAÇÃO:
     o. Atualiza saldo real e sincroniza com AgentProtetor
     p. Registra resultado em operacoes.json
     q. Exibe resumo: resultado, lucro/perda, saldo atual
     r. Consulta AgentAnalisador → pausa loop se recomendado
     s. Volta para A
```

### Comportamento de troca de ativo por payout

Quando o payout do ativo principal cai abaixo do mínimo durante o loop:

```
SITUAÇÃO NORMAL:
  [19:15] SINAL: CALL  ← análise baseada no EURUSD_otc
  [PAYOUT] EURUSD_otc: 65% caiu abaixo do mínimo 80%
  [PAYOUT] Buscando novo ativo principal...
  [PAYOUT] Novo ativo principal: GBPUSD (OTC) | Payout: 87%
  [PAYOUT] Sinal descartado. Analisando novo ativo a partir do próximo candle.

  → O sinal do EURUSD_otc é descartado (análise inválida para o novo ativo)
  → A partir do próximo candle: get_candles do GBPUSD_otc
  → A estratégia analisa o GBPUSD_otc e decide por conta própria

SEM ALTERNATIVA:
  [PAYOUT] Nenhum ativo com payout >= 80% disponível. Ignorando sinal.
  → Loop continua monitorando o mesmo ativo, aguardando payout subir
```

### Particularidades
- Única loop com análise técnica automática de candles
- O ativo é analisado no **horário exato de fechamento do candle** para máxima precisão
- Sincronização de tempo é calculada matematicamente: `ceil(seg_atual / duracao) * duracao`
- Roda indefinidamente até Ctrl+C, bloqueio do Protetor ou pausa do Analisador
- Não usa a API Anthropic (funciona sem custo de tokens)

---

## 12. Estrategias Tecnicas

Todas as estratégias vivem em `estrategias.py` e seguem a mesma interface:

```python
def minha_estrategia(candles: list[dict], cfg: dict) -> dict:
    return {
        "sinal":       "call" | "put" | None,
        "motivo":      str,
        "indicadores": dict,
    }
```

### NENHUMA
- **Tipo:** Placeholder
- **Comportamento:** Nunca gera sinal
- **Uso:** Desativar operações automáticas (monitorar sem operar)

### EMA_RSI
- **Tipo:** Cruzamento de médias + momentum
- **Indicadores:** EMA9, EMA21, RSI(14)
- **Mínimo de candles:** 30
- **Timeframe recomendado:** M1
- **CALL quando:**
  - EMA9 cruza acima da EMA21 (cruzamento de alta)
  - RSI < 30 (sobrevendido) e subindo
  - Vela atual é verde
- **PUT quando:** condições inversas (EMA9 cruza abaixo, RSI > 70 caindo, vela vermelha)
- **Melhor para:** reversões em mercados com tendência clara

### PROFITX_E1
- **Tipo:** Reversão confirmada por tendência e força
- **Indicadores:** SMA5, SMA21, corpo médio, range médio
- **Mínimo de candles:** 30
- **Timeframe recomendado:** M1
- **CALL quando:**
  - Vela anterior vermelha + vela atual verde (reversão)
  - Close atual > close anterior (confirmação)
  - close > SMA5 > SMA21 (tendência de alta)
  - Corpo da vela > média dos últimos 5 corpos (vela forte)
  - Range das últimas 3 velas > 50% do range médio (mercado em movimento)
- **Diferencial:** filtra mercados lateralizados (consolidação) — exige movimento real

### PROFITX_FRACTAL
- **Tipo:** Cruzamento de buffer + padrão de fractal
- **Indicadores:** Buffer1 (close - SMA34), Buffer2 (WMA5 de Buffer1), Fractal 3 barras
- **Mínimo de candles:** 45
- **Timeframe recomendado:** M1
- **CALL quando:**
  - Buffer1 cruza acima de Buffer2 (momentum positivo)
  - Fractal de alta confirmado (candle central tem low mais baixo que vizinhos)
- **Diferencial:** combina momentum de médias com padrão geométrico de reversão

### PROFITX_RESTRITO
- **Tipo:** Filtro múltiplo rigoroso (menor frequência, maior seletividade)
- **Indicadores:** Buffer1, Buffer2 (mesmo do Fractal), RSI(14), corpo, range médio
- **Mínimo de candles:** 45
- **Timeframe recomendado:** M1
- **CALL quando:** (todos os filtros obrigatórios)
  - Buffer1 > Buffer2 (tendência dos buffers favorável)
  - RSI > 50 (momentum de alta)
  - Corpo da vela > 30% do range médio (vela com corpo relevante)
  - Range da vela > 50% do range médio (movimento real, não ruído)
- **Diferencial:** menos sinais, mais confiáveis — ideal para quem prefere operar menos
- **Exibe no terminal:** B1/B2, corpo, mov, RSI — útil para acompanhar o estado

### Comparativo das estratégias

| Estratégia | Frequência de sinais | Seletividade | Complexidade |
|---|---|---|---|
| NENHUMA | Zero | — | — |
| EMA_RSI | Baixa | Alta | Média |
| PROFITX_E1 | Média | Média | Média |
| PROFITX_FRACTAL | Média | Média | Alta |
| PROFITX_RESTRITO | Baixa | Muito alta | Alta |

### Como adicionar uma nova estratégia

1. Implemente a função em `estrategias.py` seguindo a interface padrão
2. Registre em `ESTRATEGIAS`:
   ```python
   ESTRATEGIAS["MINHA_ESTRATEGIA"] = minha_estrategia
   ```
3. Registre metadados em `ESTRATEGIAS_META`:
   ```python
   ESTRATEGIAS_META["MINHA_ESTRATEGIA"] = {
       "timeframe_rec": 60,
       "descricao": "Descrição breve",
   }
   ```
4. O Loop Autônomo exibe automaticamente na lista de seleção — sem alterar main.py

---

## 13. Estabilidade e Resiliencia

### Verificação de internet na inicialização

Ao executar `main.py`, antes de qualquer outra ação, o sistema testa a conexão:
```
[REDE] Testando conexão com a internet... OK (42ms)
```
Método: conexão TCP na porta 53 do DNS público do Google (8.8.8.8).
Sem internet → sistema aborta com mensagem clara antes de tentar conectar à Quotex.

### Timeout em get_candles (Loop Autônomo)

O PyQuotex pode "travar" silenciosamente se o servidor da Quotex parar de responder
durante uma requisição de candles — sem lançar exceção, sem retornar, ficando preso.

Solução implementada:
```
get_candles() → asyncio.wait_for(timeout=30s)
Se não responder em 30s:
  → Testa internet
  → Com internet: "instabilidade no servidor" → aguarda próximo candle
  → Sem internet: aguarda 30s → tenta novamente
```

Antes dessa correção: o sistema ficaria preso para sempre nessa chamada.
Caso real documentado: travamento às 18:23 que durou 3h17min até o usuário reiniciar.

### Reconexão automática durante operação

Se uma operação ativa perder conexão (timeout ou exceção de rede):
```
1. [TIMEOUT] Resultado não recebido em Xs
2. Tenta reconectar com _conectar_com_retry()
3. Se reconectou: pula o nível atual e continua o loop
4. Se não reconectou após N tentativas: encerra o loop
```

### Shutdown gracioso (Ctrl+C)

```
1º Ctrl+C → flag de encerramento ativada
  → Exibe: "Aguardando operação atual finalizar..."
  → Operação em andamento completa normalmente
  → Resultado registrado em operacoes.json
  → Loop encerra na próxima iteração

2º Ctrl+C → saída imediata (se necessário forçar)
```

### Detecção de DOJI

Quando a corretora devolve o capital (empate):
- `check_win()` retorna falso (mesmo comportamento que LOSS)
- `get_profit()` retorna 0.0 (diferente de LOSS que retorna negativo)
- O sistema detecta: `if not win and profit == 0.0 → DOJI`
- Tratamento: encerra a sequência MG sem registrar como loss
- Não afeta loss streak, não afeta contagem de Cenário 3

---

## 14. Arquivos do Sistema

### Arquivos principais

| Arquivo | Função | Editável? |
|---|---|---|
| `main.py` | Ponto de entrada. CLI, loops, menu | Não (código) |
| `agents.py` | Lógica dos 5 agentes | Não (código) |
| `skills.py` | Ferramentas dos agentes (tools) | Não (código) |
| `estrategias.py` | Estratégias técnicas | Sim (adicionar estratégias) |
| `config.json` | Parâmetros de risco e operação | Via menu [6] |
| `.env` | Credenciais (API keys, login) | Editor de texto |

### Arquivos gerados automaticamente

| Arquivo | Conteúdo | Observação |
|---|---|---|
| `operacoes.json` | Histórico completo de operações | Cresce com o uso |
| `alertas.json` | Alertas do AgentProtetor | Registra bloqueios |
| `relatorio.txt` | Último relatório gerado | Sobrescrito a cada geração |
| `sinais.json` | Lista de sinais para o Loop Lista | Criado manualmente pelo usuário |
| `telegram_session.session` | Autenticação Telegram | Não deletar |
| `.pycmd` | Comando Python detectado pelo setup.bat | Usado pelo iniciar.bat |

### Crescimento do operacoes.json

O arquivo cresce a cada operação executada e nunca é podado automaticamente.
Impacto no desempenho:

| Operações | Tamanho estimado | Impacto |
|---|---|---|
| até 500 | < 1MB | Imperceptível |
| 500 a 2.000 | 1–5MB | Mínimo |
| 2.000 a 10.000 | 5–25MB | Leve lentidão nos relatórios |
| acima de 10.000 | > 25MB | Recomenda-se arquivar manualmente |

Para arquivar: renomeie `operacoes.json` para `operacoes_2025.json` e o sistema
criará um novo arquivo vazio na próxima execução.

---

*Última atualização: fevereiro de 2026*
