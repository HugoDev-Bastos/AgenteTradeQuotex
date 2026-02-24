#!/bin/bash
# ============================================================
# install_server.sh - Instalacao completa do AgentTradSyst
# Testado em: Ubuntu 20.04/22.04 ARM64 (Oracle Cloud A1.Flex)
# Uso: bash install_server.sh
# ============================================================

set -e  # Para em qualquer erro

REPO_URL="https://github.com/HugoDev-Bastos/AgenteTradeQuotex.git"
REPO_DIR="$HOME/AgenteTradeQuotex"
PYTHON_VERSION="3.13.0"
PYTHON_DIR="$HOME/Python-${PYTHON_VERSION}"
PYTHON_BIN="python3.13"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[ERRO]${NC} $1"; exit 1; }
step() { echo -e "\n${YELLOW}==>${NC} $1"; }

echo ""
echo "  ============================================================"
echo "  AgentTradSyst - Instalacao no servidor"
echo "  ============================================================"
echo ""

# ============================================================
# 1. Atualizar sistema
# ============================================================
step "Atualizando sistema..."
sudo apt update -y && sudo apt upgrade -y
ok "Sistema atualizado"

# ============================================================
# 2. Dependencias de compilacao do Python
# (inclui libsqlite3-dev para evitar erro 'No module named _sqlite3')
# ============================================================
step "Instalando dependencias de compilacao..."
sudo apt install -y \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libncurses5-dev \
    libreadline-dev \
    libffi-dev \
    libsqlite3-dev \
    libbz2-dev \
    liblzma-dev \
    libgdbm-dev \
    uuid-dev \
    wget \
    git \
    curl
ok "Dependencias instaladas"

# ============================================================
# 3. Compilar Python 3.13 (se ainda nao instalado)
# ============================================================
if command -v $PYTHON_BIN &>/dev/null; then
    warn "Python ${PYTHON_VERSION} ja instalado: $(python3.13 --version)"
else
    step "Baixando Python ${PYTHON_VERSION}..."
    cd "$HOME"
    wget -q --show-progress "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
    tar -xzf "Python-${PYTHON_VERSION}.tgz"
    ok "Download concluido"

    step "Compilando Python ${PYTHON_VERSION} (pode demorar 5-10 min)..."
    cd "$PYTHON_DIR"
    ./configure --enable-optimizations --with-lto > /dev/null 2>&1
    make -j$(nproc) > /dev/null 2>&1
    sudo make altinstall > /dev/null 2>&1
    cd "$HOME"
    ok "Python ${PYTHON_VERSION} compilado e instalado"
fi

# Verificar
$PYTHON_BIN --version || err "Python 3.13 nao encontrado apos instalacao"

# ============================================================
# 4. Clonar ou atualizar repositorio
# ============================================================
if [ -d "$REPO_DIR/.git" ]; then
    step "Repositorio ja existe — atualizando..."
    cd "$REPO_DIR"
    git pull origin main
    ok "Repositorio atualizado"
else
    step "Clonando repositorio..."
    git clone "$REPO_URL" "$REPO_DIR"
    ok "Repositorio clonado em $REPO_DIR"
fi

cd "$REPO_DIR"

# ============================================================
# 5. Ambiente virtual
# ============================================================
if [ ! -d "venv" ]; then
    step "Criando ambiente virtual..."
    $PYTHON_BIN -m venv venv
    ok "Ambiente virtual criado"
else
    warn "Ambiente virtual ja existe"
fi

source venv/bin/activate

# ============================================================
# 6. Instalar dependencias Python
# ============================================================
step "Instalando dependencias Python..."
pip install --upgrade pip -q
pip install -r requirements.txt
ok "Dependencias instaladas"

# ============================================================
# 7. Criar diretorios necessarios
# ============================================================
step "Criando diretorios..."
mkdir -p logs data
ok "Diretorios criados: logs/ data/"

# ============================================================
# 8. Configurar .env
# ============================================================
if [ -f ".env" ]; then
    warn ".env ja existe — mantendo atual"
else
    step "Configurando .env..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        cat > .env << 'EOF'
# Anthropic
ANTHROPIC_API_KEY=

# Quotex
QUOTEX_EMAIL=
QUOTEX_PASSWORD=
QUOTEX_ACCOUNT_MODE=PRACTICE

# Telegram
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
TELEGRAM_BOT=
TELEGRAM_TIME_OFFSET=-60
EOF
    fi
    warn ".env criado — preencha as credenciais antes de iniciar:"
    warn "  nano $REPO_DIR/.env"
fi

# ============================================================
# 9. Verificar instalacao
# ============================================================
step "Verificando instalacao..."
$PYTHON_BIN -c "import anthropic, dotenv, telethon, pyquotex, pandas; print('  Modulos OK')"
ok "Verificacao concluida"

# ============================================================
# 10. Criar script de inicializacao rapida
# ============================================================
step "Criando script de inicio rapido..."
cat > "$HOME/start_trading.sh" << EOF
#!/bin/bash
cd $REPO_DIR
source venv/bin/activate
python3.13 main.py
EOF
chmod +x "$HOME/start_trading.sh"
ok "Script criado: ~/start_trading.sh"

# ============================================================
# Resumo final
# ============================================================
echo ""
echo "  ============================================================"
echo "  INSTALACAO CONCLUIDA"
echo "  ============================================================"
echo ""
echo "  Proximo passo — preencher credenciais no .env:"
echo "    nano $REPO_DIR/.env"
echo ""
echo "  Para iniciar o sistema:"
echo "    bash ~/start_trading.sh"
echo ""
echo "  Para rodar em background (screen):"
echo "    screen -S trading"
echo "    bash ~/start_trading.sh"
echo "    (Ctrl+A + D para sair sem matar)"
echo "    screen -r trading  (para voltar)"
echo ""
echo "  Para atualizar o sistema depois:"
echo "    cd $REPO_DIR && git pull && bash ~/start_trading.sh"
echo "  ============================================================"
echo ""
