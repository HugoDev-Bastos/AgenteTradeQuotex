"""
main.py - Entry point do Trading System Quotex.

Modos:
  quotex   - Loop automatico via websocket
  telegram - Sinais automaticos do Telegram
  lista    - Sinais de arquivo JSON
  autonomo - Estrategias tecnicas autonomas
  backteste - Simulacao de acuracia
"""

import sys

# --- Encoding: garante UTF-8 em qualquer terminal Windows ---
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from utils import _setup_log, log_s, _validar_ambiente

_validar_ambiente()

from agents import AgentProtetor, AgentVerificador, AgentAnalisador
from cli import modo_manual


def main():
    _setup_log()
    log_s("INFO", "=" * 50)
    log_s("INFO", "Sistema iniciado")
    protetor    = AgentProtetor.from_config()
    verificador = AgentVerificador.from_config()
    analisador  = AgentAnalisador(janela=20)

    modo_manual(protetor, analisador, verificador)

    log_s("INFO", "Sistema encerrado")


if __name__ == "__main__":
    main()
