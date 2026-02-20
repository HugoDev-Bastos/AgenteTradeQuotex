# AgentTradSyst - Guia de Instalacao

Sistema de trading automatizado com inteligencia artificial para a plataforma Quotex.

---

## Requisitos

| Requisito | Versao minima | Link |
|---|---|---|
| Python | 3.12 ou superior | https://www.python.org/downloads/ |
| Git | Qualquer versao recente | https://git-scm.com/downloads |
| Conta Quotex | - | https://quotex.com |
| Conta Anthropic | - | https://console.anthropic.com |

> **IMPORTANTE:** Durante a instalacao do Python, marque a opcao **"Add Python to PATH"**.

---

## Instalacao

### Passo 1 — Baixar o sistema

```
git clone <URL_DO_REPOSITORIO>
cd AgentTradSyst
```

Ou extraia o arquivo .zip fornecido para uma pasta de sua preferencia.

### Passo 2 — Instalar dependencias

Abra o **CMD** ou **PowerShell** na pasta do sistema e execute:

```
py -3.12 -m pip install -r requirements.txt
```

> Se voce tem apenas uma versao de Python instalada, pode usar `python` ou `python3` no lugar de `py -3.12`.

A instalacao do PyQuotex requer conexao com a internet e Git instalado.

### Passo 3 — Configurar credenciais

Copie o arquivo de exemplo:

```
copy .env.example .env
```

Abra o arquivo `.env` em qualquer editor de texto (Bloco de Notas, VSCode, etc.)
e preencha suas credenciais. Veja a secao **Obtendo as Credenciais** abaixo.

### Passo 4 — Executar

```
py -3.12 main.py
```

Ou use o instalador automatico (Passo 2 e verificacoes):

```
setup.bat
```

---

## Obtendo as Credenciais

### ANTHROPIC_API_KEY (obrigatorio)

1. Acesse https://console.anthropic.com
2. Crie uma conta ou faca login
3. Va em **API Keys** -> **Create Key**
4. Copie a chave (comeca com `sk-ant-...`)
5. Cole no `.env`: `ANTHROPIC_API_KEY=sk-ant-...`

> A API tem custo por uso. Veja os precos em https://anthropic.com/pricing

### QUOTEX_EMAIL e QUOTEX_PASSWORD (obrigatorio)

Email e senha da sua conta em https://quotex.com

```
QUOTEX_EMAIL=seu@email.com
QUOTEX_PASSWORD=suasenha
```

### QUOTEX_ACCOUNT_MODE (obrigatorio)

Define se opera em conta demo ou real:

```
QUOTEX_ACCOUNT_MODE=PRACTICE    <- conta demo (recomendado para comecar)
QUOTEX_ACCOUNT_MODE=REAL        <- conta real (dinheiro real)
```

### Credenciais Telegram (somente para o Loop Telegram)

Necessario apenas se quiser usar o modo de sinais via Telegram.

1. Acesse https://my.telegram.org/apps com seu numero de telefone
2. Clique em **API development tools**
3. Crie um aplicativo (nome e plataforma podem ser qualquer coisa)
4. Anote o **api_id** e o **api_hash**

```
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=0123456789abcdef...
TELEGRAM_PHONE=+5511999999999
```

Na **primeira execucao** do Loop Telegram, o sistema vai pedir o codigo SMS
enviado ao seu numero para autenticar. Apos isso, a sessao fica salva
no arquivo `telegram_session.session` e nao pede mais.

---

## Estrutura dos arquivos

```
AgentTradSyst/
  main.py           <- ponto de entrada, execute este arquivo
  agents.py         <- logica dos 5 agentes
  skills.py         <- ferramentas (tools) dos agentes
  estrategias.py    <- estrategias tecnicas do Loop Autonomo
  simulacao.py      <- simulacao visual standalone (sem API)
  config.json       <- parametros de risco (editavel pelo menu)
  .env              <- suas credenciais (NUNCA compartilhe)
  .env.example      <- modelo de credenciais
  requirements.txt  <- dependencias Python
  setup.bat         <- instalador automatico Windows
  INSTALACAO.md     <- este arquivo

  --- criados automaticamente na primeira execucao ---
  operacoes.json    <- historico de operacoes
  alertas.json      <- registro de alertas do protetor
  relatorio.txt     <- ultimo relatorio gerado
  sinais.json       <- sinais do Loop Lista (edite para usar)
```

---

## Modos de operacao

| Opcao | Modo | Descricao |
|---|---|---|
| 1 | Simulacao | Loop local sem API, sem Quotex |
| 2 | Quotex | Loop real via websocket |
| 3 | Telegram | Sinais automaticos via Telegram |
| 4 | Lista | Sinais pre-definidos em arquivo JSON |
| 5 | Autonomo | Estrategias tecnicas automaticas |
| 6 | Config | Editar parametros de risco |
| r | Reiniciar | Zera sessao e contadores |
| 0 | Sair | Encerra o sistema |

---

## Solucao de problemas

**"Python nao reconhecido como comando"**
Reinstale o Python marcando "Add Python to PATH".

**"ModuleNotFoundError"**
Execute `pip install -r requirements.txt` novamente.

**"Erro de conexao Quotex"**
Verifique email e senha no `.env`. Certifique-se de ter conexao com internet.

**Caracteres estranhos no terminal**
O sistema corrige automaticamente. Se persistir, use o Windows Terminal
(disponivel na Microsoft Store) ou execute antes: `chcp 65001`

**Loop Telegram pede codigo SMS sempre**
Nao delete o arquivo `telegram_session.session`. Ele salva a autenticacao.
