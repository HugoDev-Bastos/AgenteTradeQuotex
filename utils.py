"""
utils.py - Utilitarios compartilhados: encoding, log, rede, shutdown, classificacao de ativo.
"""

import sys
import time
import signal
import socket
import threading
import asyncio
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# --- Encoding: garante UTF-8 em qualquer terminal Windows (CMD, PowerShell, etc.) ---
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ============================================================
# LOG DE SESSAO (sessao.log - rotativo por dia, 30 dias)
# ============================================================

_logger_sessao: logging.Logger | None = None


def _setup_log():
    """Configura log de sessao rotativo diario. Arquivo: sessao.log."""
    global _logger_sessao
    log_path = Path(__file__).resolve().parent / "logs" / "sessao.log"
    handler = TimedRotatingFileHandler(
        log_path, when="midnight", backupCount=30, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    _logger_sessao = logging.getLogger("sessao")
    _logger_sessao.setLevel(logging.DEBUG)
    _logger_sessao.addHandler(handler)
    _logger_sessao.propagate = False

def log_s(nivel: str, msg: str):
    """Escreve no sessao.log. nivel: INFO | WARN | ERROR"""
    if _logger_sessao is None:
        return
    n = nivel.upper()
    if n == "INFO":        _logger_sessao.info(msg)
    elif n in ("WARN", "WARNING"): _logger_sessao.warning(msg)
    elif n == "ERROR":     _logger_sessao.error(msg)
    else:                  _logger_sessao.debug(msg)


# --- Verificador de conexao com a internet ---
def _verificar_internet(host: str = "8.8.8.8", port: int = 53, timeout: int = 3) -> tuple[bool, float]:
    """Testa conexao TCP com o DNS publico do Google (8.8.8.8:53).

    Nao envia dados — apenas verifica se a rede responde.
    Retorna (conectado: bool, latencia_ms: float).
    """
    try:
        inicio = time.monotonic()
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        ms = (time.monotonic() - inicio) * 1000
        return True, ms
    except OSError:
        return False, 0.0


# --- Validacao de ambiente: Python, .env e internet ---
def _validar_ambiente():
    """Verifica requisitos minimos antes de iniciar. Aborta com mensagem clara se falhar."""
    erros = []

    # Python >= 3.12
    if sys.version_info < (3, 12):
        erros.append(
            f"Python 3.12+ necessario. Versao atual: {sys.version_info.major}.{sys.version_info.minor}\n"
            f"  Baixe em: https://www.python.org/downloads/"
        )

    # Arquivo .env
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        erros.append(
            ".env nao encontrado.\n"
            "  Copie o arquivo .env.example para .env e preencha suas credenciais.\n"
            f"  Local esperado: {env_path}"
        )
    else:
        # Verifica variaveis obrigatorias
        try:
            from dotenv import dotenv_values
            env = dotenv_values(env_path)
        except ImportError:
            env = {}

        obrigatorias = ["ANTHROPIC_API_KEY", "QUOTEX_EMAIL", "QUOTEX_PASSWORD", "QUOTEX_ACCOUNT_MODE"]
        faltando = [v for v in obrigatorias if not env.get(v) or env[v].startswith("COLOQUE_")]
        if faltando:
            erros.append(
                "Variaveis obrigatorias nao configuradas no .env:\n" +
                "".join(f"  - {v}\n" for v in faltando) +
                "  Consulte o arquivo .env.example para instrucoes."
            )

    # Conexao com a internet
    print("  [REDE] Testando conexao com a internet...", end=" ", flush=True)
    ok, ms = _verificar_internet()
    if ok:
        print(f"OK ({ms:.0f}ms)")
    else:
        print("FALHOU")
        erros.append(
            "Sem conexao com a internet (timeout 3s).\n"
            "  Verifique sua rede antes de iniciar o sistema."
        )

    if erros:
        print("\n" + "=" * 60)
        print("  [ERRO] Ambiente nao configurado corretamente:")
        print("=" * 60)
        for e in erros:
            print(f"\n  {e}")
        print("\n" + "=" * 60 + "\n")
        sys.exit(1)


# ============================================================
# SHUTDOWN GRACIOSO (Ctrl+C durante trade ativo)
# ============================================================

_shutdown_gracioso = threading.Event()


def _instalar_shutdown_gracioso():
    """Instala handler de Ctrl+C que aguarda a operacao atual terminar.

    - 1o Ctrl+C: seta flag, operacao atual completa e registra normalmente
    - 2o Ctrl+C: saida imediata (comportamento padrao)

    Retorna o handler original para restaurar depois.
    """
    _shutdown_gracioso.clear()
    handler_original = signal.getsignal(signal.SIGINT)

    def _handler(*_):
        if _shutdown_gracioso.is_set():
            # Segundo Ctrl+C: saida imediata
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt
        print("\n\n  [!] Ctrl+C recebido. Aguardando operacao atual finalizar...")
        print("      Pressione Ctrl+C novamente para saida imediata.\n")
        _shutdown_gracioso.set()

    signal.signal(signal.SIGINT, _handler)
    return handler_original


def _restaurar_shutdown(handler_original):
    """Restaura handler original de Ctrl+C e limpa a flag."""
    signal.signal(signal.SIGINT, handler_original)
    _shutdown_gracioso.clear()


async def _sleep_cancelavel(segundos: int):
    """Sleep em blocos de 1s para checar flag de shutdown rapidamente."""
    for _ in range(segundos):
        if _shutdown_gracioso.is_set():
            return
        await asyncio.sleep(1)


# ============================================================
# HELPER: input com suporte a cancelamento
# ============================================================

def _inp(prompt: str, default=None):
    """Input com suporte a cancelamento (0 ou 'voltar').

    Retorna (valor, cancelado). Se cancelado=True, o loop deve retornar.
    """
    hint = f" [{default}]" if default is not None else ""
    raw = input(f"  {prompt}{hint} (0=voltar): ").strip()
    if raw in ("0", "voltar", "menu"):
        return None, True
    if raw == "" and default is not None:
        return default, False
    return raw, False


# ============================================================
# LOOP QUOTEX REAL (websocket)
# ============================================================

_CRYPTO_SYMBOLS = frozenset({
    "BTC", "ETH", "LTC", "XRP", "BCH", "EOS", "DOGE", "DASH",
    "XMR", "NEO", "IOTA", "TRX", "ADA", "XLM", "BNB", "ETC",
    "SOL", "DOT", "MATIC", "LINK", "AVAX", "UNI", "ALGO", "ATOM",
    "FIL", "VET", "THETA", "SHIB", "TON", "APT", "ARB", "OP",
    "NEAR", "FTM", "MANA", "SAND", "AXS", "CRO", "EGLD",
})

# Palavras-chave de materias-primas
_COMMODITY_KEYWORDS = frozenset({
    "GOLD", "SILVER", "OIL", "BRENT", "WTI", "GAS", "CRUDE",
    "COFFEE", "SUGAR", "COCOA", "COPPER", "PLATINUM", "PALLADIUM",
    "WHEAT", "CORN", "COTTON", "ZINC", "NICKEL", "ALUMINIUM",
    "LUMBER", "SOYBEAN", "NATURAL",
})


def _classificar_ativo(nome_display: str) -> str:
    """Classifica ativo por tipo de mercado a partir do nome de exibicao.

    Retorna: FOREX | CRIPTO | MATERIA_PRIMA | ACAO
    """
    nome_limpo = nome_display.replace("(OTC)", "").replace("(Digital)", "").strip()
    nome_upper = nome_limpo.upper()

    # Cripto: qualquer parte do par e simbolo cripto
    partes = [p.strip() for p in nome_limpo.replace("/", " ").split() if p.strip()]
    for parte in partes:
        if parte.upper() in _CRYPTO_SYMBOLS:
            return "CRIPTO"

    # Materia-prima: nome contem palavra-chave de commodity
    for keyword in _COMMODITY_KEYWORDS:
        if keyword in nome_upper:
            return "MATERIA_PRIMA"

    # Forex: padrao XXX/YYY com dois codigos de exatamente 3 letras
    if "/" in nome_limpo:
        lados = nome_limpo.split("/")
        if len(lados) == 2:
            a, b = lados[0].strip(), lados[1].strip()
            if len(a) == 3 and a.isalpha() and len(b) == 3 and b.isalpha():
                return "FOREX"

    # Forex: formato interno XXXXXX (6 letras, ex: AUDUSD, CADJPY)
    if len(nome_upper) == 6 and nome_upper.isalpha():
        return "FOREX"

    # Padrao: acoes e indices
    return "ACAO"


def _esta_no_horario(cfg: dict) -> bool:
    """Retorna True se hora atual esta dentro da janela de operacao configurada.

    Usa as chaves 'horario_inicio' e 'horario_fim' do config (formato "HH:MM").
    Se qualquer uma estiver vazia, retorna True (sem restricao).
    """
    inicio = cfg.get("horario_inicio", "").strip()
    fim    = cfg.get("horario_fim", "").strip()
    if not inicio or not fim:
        return True
    try:
        agora = datetime.now().strftime("%H:%M")
        return inicio <= agora <= fim
    except Exception:
        return True

