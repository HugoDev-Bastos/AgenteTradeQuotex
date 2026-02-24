"""
config.py - Configuracoes do sistema: defaults, carregar/salvar, editor interativo.
"""

import json
import asyncio
import os
from pathlib import Path

from dotenv import dotenv_values

from utils import log_s

CONFIG_FILE = Path(__file__).resolve().parent / "data" / "config.json"

_CONFIG_DEFAULTS = {
    # Conta
    "account_mode": "PRACTICE",
    # Operacao
    "entrada_padrao": 10.0,
    "duracao_padrao": 300,
    "niveis_mg": 3,
    "fator_correcao_mg": False,
    "estrategia_ativa": "EMA_RSI",
    # Filtros
    "tipo_ativo": "AMBOS",
    "tipo_mercado": "AMBOS",
    "payout_minimo_pct": 75,
    "volatilidade_minima_pct": 30,
    "payout_minimo_pct_telegram": 75,
    # Verificador de mercado
    "verificador_ativo": True,
    "max_dojis_consecutivos": 2,
    # Protecao
    "max_loss_streak": 5,
    "max_ops_sessao": 50,
    "stop_loss_pct": 20.0,
    "stop_loss_reais": None,
    "take_profit_reais": None,
    "saldo_inicial": 1000.0,
    # Conexao
    "janela_execucao_seg": 5,
    "intervalo_verificacao_seg": 3,
    "timeout_resultado_seg": 900,
    "timeout_conexao_seg": 120,
    "tentativas_reconexao": 3,
    # Horario de operacao (vazio = sem restricao)
    "horario_inicio": "",
    "horario_fim": "",
}

# Cache mtime: evita releitura desnecessaria do disco
_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0


def carregar_config() -> dict:
    """Le config.json com cache baseado em mtime. Retorna defaults se arquivo nao existir."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = CONFIG_FILE.stat().st_mtime
        if _cfg_cache is not None and mtime == _cfg_mtime:
            return dict(_cfg_cache)  # copia para evitar mutacao do cache
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in _CONFIG_DEFAULTS.items():
            cfg.setdefault(k, v)
        _cfg_cache = cfg
        _cfg_mtime = mtime
        return dict(cfg)
    except Exception:
        return dict(_CONFIG_DEFAULTS)


def salvar_config(cfg: dict):
    """Salva config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def _atualizar_env_multiplo(updates: dict):
    """Atualiza multiplas variaveis no arquivo .env (cria a linha se nao existir)."""
    env_path = Path(__file__).resolve().parent / ".env"
    try:
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        for key, value in updates.items():
            nova_linha = f"{key}={value}\n"
            atualizado = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                    lines[i] = nova_linha
                    atualizado = True
                    break
            if not atualizado:
                lines.append(nova_linha)
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception as e:
        print(f"  [ERRO] Atualizando .env: {e}")


def comando_config(protetor: "AgentProtetor" = None):
    """Menu interativo para editar configuracoes organizadas por categoria."""
    cfg = carregar_config()

    env_path = Path(__file__).resolve().parent / ".env"
    try:
        from dotenv import dotenv_values as _dv
        env_cfg = dict(_dv(env_path))
    except Exception:
        env_cfg = {}
    env_editado: dict = {}

    _TELEGRAM_CAMPOS = [
        ("payout_minimo_pct_telegram",  "Payout minimo (%)",           "float"),
        ("TELEGRAM_API_ID",             "API ID",                      "env_int"),
        ("TELEGRAM_API_HASH",           "API Hash",                    "env_str"),
        ("TELEGRAM_PHONE",              "Telefone (+55...)",           "env_str"),
        ("TELEGRAM_BOT",                "Bot username (@...)",         "env_str"),
        ("TELEGRAM_TIME_OFFSET",        "Time Offset (min)",           "env_int"),
        ("__telegram_test__",           "Testar conexao",              "telegram_test_action"),
    ]

    _AVANCADAS_CAMPOS = [
        ("janela_execucao_seg",         "Janela execucao (+-seg)",     "int"),
        ("intervalo_verificacao_seg",   "Intervalo verificacao (seg)", "int"),
        ("timeout_resultado_seg",       "Timeout resultado (seg)",     "int"),
        ("timeout_conexao_seg",         "Timeout conexao (seg)",       "int"),
        ("tentativas_reconexao",        "Tentativas reconexao",        "int"),
    ]

    _FILTROS_CAMPOS = [
        ("tipo_ativo",                  "Tipo ativo",                  "choice:OTC:NAO_OTC:AMBOS"),
        ("tipo_mercado",                "Tipo de mercado",             "choice:FOREX:CRIPTO:MATERIA_PRIMA:ACAO:AMBOS"),
        ("payout_minimo_pct",           "Payout minimo Quotex (%)",    "float"),
        ("volatilidade_minima_pct",     "Volat. min. (0=desativado)", "float"),
        ("max_dojis_consecutivos",      "Max dojis (0=desativado)", "int"),
    ]

    _GERENCIAMENTO_CAMPOS = [
        ("max_loss_streak",             "Max loss streak",             "int"),
        ("max_ops_sessao",              "Max ops/sessao (0=ilimit)",   "int_null"),
        ("stop_loss_pct",               "Stop Loss (%)",               "float"),
        ("stop_loss_reais",             "Stop Loss R$ (0=desativ)",    "float_null"),
        ("take_profit_reais",           "Take Profit R$ (0=desativ)",  "float_null"),
        ("saldo_inicial",               "Saldo inicial (R$)",          "float"),
    ]

    _CONEXAO_CAMPOS = [
        ("QUOTEX_EMAIL",                "Email",                       "env_str"),
        ("QUOTEX_PASSWORD",             "Senha",                       "env_pass"),
        ("__saldo__",                   "Consultar saldo",             "saldo_action"),
    ]

    CAMPOS = [
        ("__header__",   "PADRAO CONFIGS RAPIDAS", None),
        ("account_mode",                "Modo da conta",               "choice:PRACTICE:REAL"),
        ("entrada_padrao",              "Entrada padrao (R$)",         "float"),
        ("duracao_padrao",              "Time Frame padrao",           "choice_int:60:300:900:1800:3600"),
        ("niveis_mg",                   "Niveis MG",                   "int"),
        ("fator_correcao_mg",           "Fator correcao MG",           "bool"),

        ("__header__",   "AGENTES",         None),
        ("verificador_ativo",           "VERIFICADOR",                 "verificador_toggle"),
        ("__protetor__",                "PROTETOR",                    "protetor_toggle"),

        ("__header__",   "OUTROS",          None),
        ("__subgroup__", "CONEXAO QUOTEX",  _CONEXAO_CAMPOS),
        ("__subgroup__", "FILTROS",        _FILTROS_CAMPOS),
        ("__subgroup__", "GERENCIAMENTO",  _GERENCIAMENTO_CAMPOS),
        ("__subgroup__", "TELEGRAM",       _TELEGRAM_CAMPOS),
        ("__subgroup__", "AVANCADAS",      _AVANCADAS_CAMPOS),
    ]

    SEP = "  +--------------------------------------------------+"

    def _vs(key, tipo):
        if key == "__protetor__":
            if protetor is None: return "N/A"
            return "BLOQUEADO" if protetor.bloqueado else "LIVRE"
        if key == "__saldo__":
            return "[consultar]"
        if key == "__telegram_test__":
            return "[testar]"
        if tipo == "env_pass":
            v = env_editado.get(key, env_cfg.get(key, ""))
            return "****" if v else "NAO CONF"
        if tipo and tipo.startswith("env_"):
            v = env_editado.get(key, env_cfg.get(key, ""))
            if not v:
                return "NAO CONF"
            s = str(v)
            return (s[:13] + "..") if len(s) > 15 else s
        v = cfg.get(key)
        if v is None:   return "OFF"
        if v is True:   return "ATIVADO"
        if v is False:  return "DESATIVADO"
        return str(v)

    def _editar_campo(key, label, tipo):
        """Edita um campo individual (config.json ou .env). Modifica cfg/env_editado in-place."""
        if tipo == "protetor_toggle":
            if protetor is None:
                return
            SEP = "  +--------------------------------------------------+"
            bloqueado = protetor.bloqueado
            estado_str = "BLOQUEADO" if bloqueado else "LIVRE"
            print()
            print(SEP)
            print("  |          AGENTPROTETOR - CONTROLE                |")
            print(SEP)
            print("  |                                                  |")
            print("  |  O AgentProtetor monitora riscos e impede novas  |")
            print("  |  operacoes quando os limites sao atingidos:      |")
            print("  |    - Sequencia de losses (loss streak)           |")
            print("  |    - Stop Loss percentual ou em reais            |")
            print("  |    - Maximo de operacoes da sessao               |")
            print("  |    - 3 ou mais Cenario 3 na sessao               |")
            print("  |                                                  |")
            print(f"  |  Bloqueio: {estado_str:<38}|")
            print("  |                                                  |")
            print(SEP)
            print()
            novo = input("  1 - BLOQUEAR  |  2 - DESBLOQUEAR  (Enter = cancelar): ").strip()
            if novo == "1":
                protetor.bloqueado = True
                log_s("WARN", "Protetor bloqueado manualmente pelo usuario")
                print("  -> Bloqueio ATIVADO. Operacoes pausadas.\n")
            elif novo == "2":
                protetor.forcar_desbloqueio()
                log_s("INFO", "Protetor desbloqueado manualmente")
                print("  -> Bloqueio DESATIVADO. Operacoes liberadas.\n")
            else:
                print("  Cancelado.\n")
            return

        if tipo == "verificador_toggle":
            SEP = "  +--------------------------------------------------+"
            estado_atual = cfg.get("verificador_ativo", True)
            estado_str = "ATIVADO" if estado_atual else "DESATIVADO"
            print()
            print(SEP)
            print("  |        AGENTVERIFICADOR - CONTROLE               |")
            print(SEP)
            print("  |                                                  |")
            print("  |  O AgentVerificador analisa as condicoes de      |")
            print("  |  mercado antes de cada operacao e bloqueia a     |")
            print("  |  entrada se alguma condicao falhar:              |")
            print("  |    - Candle fechando cedo (janela de execucao)   |")
            print("  |    - Mercado comprimido (volatilidade minima)    |")
            print("  |    - Muitos dojis consecutivos (indecisao)       |")
            print("  |    - Payout caiu abaixo do minimo configurado    |")
            print("  |                                                  |")
            print(f"  |  Verificador: {estado_str:<35}|")
            print("  |                                                  |")
            print(SEP)
            print()
            novo = input("  1 - ATIVAR  |  2 - DESATIVAR  (Enter = cancelar): ").strip()
            if novo == "1":
                cfg["verificador_ativo"] = True
                print("  -> VERIFICADOR = ATIVADO\n")
            elif novo == "2":
                cfg["verificador_ativo"] = False
                print("  -> VERIFICADOR = DESATIVADO\n")
            else:
                print("  Cancelado.\n")
            return

        if tipo == "saldo_action":
            try:
                from cli import _check_saldo_quotex
                saldo = asyncio.run(_check_saldo_quotex())
                print(f"\n  Saldo Quotex: R$ {saldo['saldo']}")
                print(f"  Modo:         {saldo['modo']}")
                print(f"  Demo:         R$ {saldo['demo_balance']}")
                print(f"  Real:         R$ {saldo['live_balance']}\n")
            except Exception as e:
                print(f"\n  [ERRO] {type(e).__name__}: {e}\n")
            return

        if tipo == "telegram_test_action":
            async def _testar_telegram():
                from agents import AgentTelegram
                print("\n  [TELEGRAM] Conectando... (pode pedir codigo SMS na 1a vez)")
                try:
                    tg = AgentTelegram()
                    await tg.conectar()
                    me = await tg._client_telegram.get_me()
                    print(f"\n  +--------------------------------------------------+")
                    print(f"  |  TELEGRAM - CONEXAO OK                           |")
                    print(f"  +--------------------------------------------------+")
                    nome = (me.first_name or "") + (" " + me.last_name if me.last_name else "")
                    print(f"  Conta:    {nome.strip()}")
                    print(f"  Username: @{me.username or 'sem username'}")
                    print(f"  Telefone: {tg.phone}")
                    print(f"  Bot:      {tg.bot_username}")
                    print(f"  Offset:   {tg.time_offset} min")
                    sessao = Path(__file__).resolve().parent / "data" / "telegram_session.session"
                    print(f"  Sessao:   {'salva' if sessao.exists() else 'nao encontrada'}")
                    print(f"  +--------------------------------------------------+\n")
                    await tg.desconectar()
                except Exception as e:
                    print(f"\n  [ERRO] {type(e).__name__}: {e}\n")
            try:
                asyncio.run(_testar_telegram())
            except Exception as e:
                print(f"\n  [ERRO] {type(e).__name__}: {e}\n")
            return

        _ALERTAS = {
            "max_ops_sessao": (
                "0 = ILIMITADO: AgentProtetor nunca bloqueara por",
                "quantidade de operacoes — sessao corre sem fim.",
            ),
            "stop_loss_reais": (
                "0 = DESATIVADO: protecao em R$ removida.",
                "Apenas o Stop Loss % continuara ativo.",
            ),
            "take_profit_reais": (
                "0 = DESATIVADO: loop nunca encerrara no lucro.",
                "Operacoes continuam ate outro limite ser atingido.",
            ),
            "volatilidade_minima_pct": (
                "0 = DESATIVADO: Verificador ignora volatilidade.",
                "Entra mesmo em mercado comprimido sem movimento.",
            ),
            "max_dojis_consecutivos": (
                "0 = DESATIVADO: Verificador ignora dojis.",
                "Entra mesmo apos sequencia de indecisao no mercado.",
            ),
        }

        vs = _vs(key, tipo)

        if key in _ALERTAS:
            linha1, linha2 = _ALERTAS[key]
            print(f"  +-- [!] AVISO ----------------------------------------+")
            print(f"  |  {linha1:<48}|")
            print(f"  |  {linha2:<48}|")
            print(f"  +----------------------------------------------------+")

        if tipo in ("env_str", "env_pass"):
            novo = input(f"  {label} [{vs}]: ").strip()
            if novo:
                env_editado[key] = novo
                print(f"  -> {label} = {novo}  [pendente salvar]")
            return

        if tipo == "env_int":
            novo = input(f"  {label} [{vs}]: ").strip()
            if novo:
                try:
                    int(novo)
                    env_editado[key] = novo
                    print(f"  -> {label} = {novo}  [pendente salvar]")
                except ValueError:
                    print(f"  [ERRO] Valor invalido: '{novo}'")
            return

        if tipo == "bool":
            print(f"  {label} [{vs}]")
            print(f"    1 - ATIVADO")
            print(f"    2 - DESATIVADO")
            novo = input("  Escolha: ").strip()
            if novo == "1":   cfg[key] = True;  print(f"  -> {label} = ATIVADO")
            elif novo == "2": cfg[key] = False; print(f"  -> {label} = DESATIVADO")
            else: print(f"  [ERRO] Opcao invalida")
            return

        if tipo.startswith("choice_int:"):
            _DUR_LABELS = {
                "60": "M1  - 1 min",  "300": "M5  - 5 min",
                "900": "M15 - 15 min", "1800": "M30 - 30 min",
                "3600": "H1  - 1 hora",
            }
            opcoes = tipo.split(":")[1:]
            print(f"  {label} [{vs}]")
            for i, op in enumerate(opcoes, 1):
                lbl = _DUR_LABELS.get(op, "")
                print(f"    {i} - {op}s  {lbl}")
            novo = input("  Escolha: ").strip()
            if novo.isdigit() and 1 <= int(novo) <= len(opcoes):
                cfg[key] = int(opcoes[int(novo) - 1])
                print(f"  -> {label} = {cfg[key]}")
            else:
                print(f"  [ERRO] Opcao invalida")
            return

        if tipo.startswith("choice:"):
            opcoes = tipo.split(":")[1:]
            print(f"  {label} [{vs}]")
            for i, op in enumerate(opcoes, 1):
                print(f"    {i} - {op}")
            novo = input("  Escolha: ").strip()
            if novo.isdigit() and 1 <= int(novo) <= len(opcoes):
                cfg[key] = opcoes[int(novo) - 1]
                print(f"  -> {label} = {cfg[key]}")
            else:
                print(f"  [ERRO] Opcao invalida")
            return

        novo = input(f"  {label} [{vs}]: ").strip()
        if not novo:
            return
        try:
            if tipo == "float":
                cfg[key] = float(novo)
            elif tipo == "int":
                cfg[key] = int(novo)
            elif tipo == "float_null":
                v = float(novo)
                cfg[key] = None if v == 0 else v
            elif tipo == "int_null":
                v = int(novo)
                cfg[key] = None if v == 0 else v
            print(f"  -> {label} = {cfg[key]}")
        except ValueError:
            print(f"  [ERRO] Valor invalido: '{novo}'")

    def _editar_subgrupo(titulo, sub_campos, extra_cmds: dict | None = None):
        """Sub-menu de edicao para um grupo de campos.

        extra_cmds: dict de {tecla: (label_rodape, funcao)} para comandos extras no rodape.
        Ex: {"r": ("resetar sessao", fn_reset)}
        """
        _sub = {i + 1: c for i, c in enumerate(sub_campos)}
        while True:
            print()
            print(SEP)
            print(f"  |{titulo + ' - CONFIGURACOES':^50}|")
            print(SEP)
            for i, (key, label, tipo) in _sub.items():
                vs = _vs(key, tipo)
                print(f"  |  {i:2}. {label:<28} {vs:<15}|")
            print(SEP)
            if env_editado:
                print(f"  |  {'[*] .env modificado - salve no menu principal':<48}|")
                print(SEP)
            if extra_cmds:
                extras_str = " | ".join(f"{k}: {v[0]}" for k, v in extra_cmds.items())
                rodape = f"Numero: editar | {extras_str} | x: voltar"
            else:
                rodape = "Numero: editar | x: voltar"
            print(f"  |  {rodape:<48}|")
            print(SEP)
            entrada = input("\n  Opcao: ").strip().lower()
            if entrada == "x":
                break
            if extra_cmds and entrada in extra_cmds:
                extra_cmds[entrada][1]()
                continue
            if entrada.isdigit():
                idx = int(entrada)
                if idx in _sub:
                    k, l, t = _sub[idx]
                    _editar_campo(k, l, t)
                else:
                    print("  [ERRO] Numero invalido")

    # Mapa numero -> entrada (campo ou subgroup)
    _editaveis: dict = {}
    _num = 0
    for entry in CAMPOS:
        if entry[0] == "__header__":
            continue
        _num += 1
        _editaveis[_num] = entry

    while True:
        print()
        print(SEP)
        print(f"  |{'CONFIGURACOES DO SISTEMA':^50}|")
        print(SEP)

        num = 0
        for entry in CAMPOS:
            key = entry[0]
            if key == "__header__":
                print(f"  |  {'-- ' + entry[1] + ' --':<48}|")
            elif key == "__subgroup__":
                num += 1
                n = len(entry[2])
                print(f"  |  {num:2}. {entry[1]:<28} {'[' + str(n) + ' campos]':<15}|")
            else:
                num += 1
                vs = _vs(key, entry[2])
                print(f"  |  {num:2}. {entry[1]:<28} {vs:<15}|")

        print(SEP)
        if env_editado:
            print(f"  |  {'[*] .env modificado - salve para aplicar':<48}|")
            print(SEP)
        print("  |  Num: editar | s: salvar | p: padrao | x: sair   |")
        print(SEP)

        entrada = input("\n  Opcao: ").strip().lower()

        if entrada == "x":
            print("  Cancelado. Config nao alterado.\n")
            break

        if entrada == "s":
            salvar_config(cfg)
            if env_editado:
                _atualizar_env_multiplo(env_editado)
                import os
                os.environ.update(env_editado)
                print(f"  [OK] .env atualizado ({len(env_editado)} campo(s))")
                env_editado.clear()
            print("  [OK] Config salvo em config.json\n")
            break

        if entrada == "p":
            confirma = input("  Redefinir TUDO para valores padrao? (s/N): ").strip().lower()
            if confirma == "s":
                cfg = dict(_CONFIG_DEFAULTS)
                print("  [OK] Config redefinido para valores padrao. Digite 's' para salvar.\n")
            else:
                print("  Cancelado.\n")
            continue

        if not entrada.isdigit():
            continue

        idx = int(entrada)
        if idx not in _editaveis:
            print(f"  [ERRO] Numero invalido")
            continue

        entry = _editaveis[idx]
        if entry[0] == "__subgroup__":
            extra = None
            if entry[1] == "TELEGRAM":
                def _resetar_sessao_telegram():
                    sessao = Path(__file__).resolve().parent / "data" / "telegram_session.session"
                    if sessao.exists():
                        sessao.unlink()
                        print("  [OK] Sessao Telegram resetada. Sera pedido novo codigo SMS na proxima conexao.\n")
                        log_s("WARN", "Sessao Telegram resetada manualmente pelo usuario")
                    else:
                        print("  [AVISO] Nenhuma sessao Telegram encontrada.\n")
                extra = {"r": ("resetar sessao", _resetar_sessao_telegram)}
            elif entry[1] == "CONEXAO QUOTEX":
                def _resetar_sessao_quotex():
                    sessao = Path(__file__).resolve().parent / "session.json"
                    if sessao.exists():
                        sessao.unlink()
                        print("  [OK] Sessao Quotex resetada. Novo login sera feito na proxima conexao.\n")
                        log_s("WARN", "Sessao Quotex resetada manualmente pelo usuario")
                    else:
                        print("  [AVISO] Nenhuma sessao Quotex encontrada.\n")
                extra = {"r": ("resetar sessao", _resetar_sessao_quotex)}
            _editar_subgrupo(entry[1], entry[2], extra_cmds=extra)
        else:
            _editar_campo(entry[0], entry[1], entry[2])


