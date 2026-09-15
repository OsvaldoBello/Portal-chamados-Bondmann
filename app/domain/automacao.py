"""Automação de criação/desligamento de usuários — regras puras (F2, 2026-09-14).

Governado por `plano_md_mestre_automacao_acessos.md` (Seções 3 e 5). Aqui não
há banco nem HTTP: só a tradução entre o que o RH preencheu no chamado
(`chamados.dados_formulario`, layouts de `formularios_acessos.py`) e o que o
worker externo recebe/devolve (contrato em `docs/automacao_api.md`), mais o
agendamento e os textos das mensagens gravadas no chamado.

Decisões do gestor (2026-09-14) aplicadas aqui:
- **Criação** roda às 07h (Brasília) do **dia útil anterior** à data de início;
  sem data, ou data já passada, roda assim que aprovada.
- **Desligamento** roda às 18h (Brasília) da data informada; data passada =
  imediato.
- **Credenciais** vão na mensagem pública de encerramento ao RH (D4) — nunca
  em `resultado`/`payload` (mascaradas aqui antes de persistir).
- O chamado só é RESOLVIDO quando TODAS as etapas deram `SUCCESS` (e não em
  dry-run).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.domain import formularios_acessos as ac
from app.domain.periodo import TZ_BR

VERSAO_CONTRATO = "1"

TIPO_CRIACAO = "CRIACAO"
TIPO_DESLIGAMENTO = "DESLIGAMENTO"
TIPO_REVOGAR_LICENCA = "REVOGAR_LICENCA"
TIPOS = (TIPO_CRIACAO, TIPO_DESLIGAMENTO, TIPO_REVOGAR_LICENCA)

STATUS_NA_FILA = "NA_FILA"
STATUS_EXECUTANDO = "EXECUTANDO"
STATUS_CONCLUIDO = "CONCLUIDO"
STATUS_COM_PENDENCIAS = "CONCLUIDO_COM_PENDENCIAS"
STATUS_FALHOU = "FALHOU"
STATUS_CANCELADO = "CANCELADO"
STATUS_ATIVOS = (STATUS_NA_FILA, STATUS_EXECUTANDO)
STATUS_FINAIS = (STATUS_CONCLUIDO, STATUS_COM_PENDENCIAS, STATUS_FALHOU, STATUS_CANCELADO)

STATUS_LABEL = {
    STATUS_NA_FILA: "Na fila",
    STATUS_EXECUTANDO: "Executando",
    STATUS_CONCLUIDO: "Concluída",
    STATUS_COM_PENDENCIAS: "Concluída com pendências",
    STATUS_FALHOU: "Falhou",
    STATUS_CANCELADO: "Cancelada",
}

# Etapas do worker: mesmos valores de `models.StepStatus` da automação.
ETAPA_SUCCESS = "SUCCESS"
ETAPA_FAILED = "FAILED"
ETAPA_SKIPPED = "SKIPPED"
_ETAPA_STATUS_VALIDOS = {ETAPA_SUCCESS, ETAPA_FAILED, ETAPA_SKIPPED}
# Numa reexecução (`pular_etapas`), o worker devolve as etapas já feitas como
# SKIPPED com esta mensagem exata (contrato): contam como concluídas, não como
# pendência — senão a reexecução nunca chegaria a CONCLUIDO.
ETAPA_JA_CONCLUIDA_MSG = "já concluída em execução anterior"

_PERFIL_ENUM = {
    ac.PERFIL_REPRESENTANTE: "REPRESENTANTE",
    ac.PERFIL_INTERNO: "INTERNO",
    ac.PERFIL_SUPERVISOR: "SUPERVISOR",
}
_DISPOSITIVO_ENUM = {"IOS": "IOS", "ANDROID": "ANDROID", "SIMULADOR": "SIMULADOR"}
_PAPEL_ENUM = {"Funcionário": "CLIENTE", "Operador": "OPERADOR", "Admin": "ADMIN"}

# Chaves de `details` das etapas que nunca podem ser persistidas em claro.
_CHAVE_SENSIVEL_RE = re.compile(r"(senha|password|passwd|secret|token|pwd)", re.IGNORECASE)
MASCARA = "***"

# Rótulos das credenciais devolvidas pelo worker (contrato, seção "credenciais").
_CREDENCIAIS_LABEL = (
    ("email", "E-mail corporativo"),
    ("senha_temporaria_m365", "Senha temporária do e-mail (troca obrigatória no 1º acesso)"),
    ("link_wmw", "Link de acesso do WMW Vendas"),
    ("senha_wmw", "Senha do WMW Vendas"),
    ("ramal_sip", "Ramal (CompanySIP)"),
    ("senha_ramal_sip", "Senha do ramal"),
    ("usuario_sap", "Usuário SAP"),
    ("senha_sap", "Senha inicial do SAP"),
)


def tipo_da_subcategoria(subcategoria: str | None) -> str | None:
    """Tipo de job de uma subcategoria de acesso (ou ``None`` fora delas)."""
    sub = (subcategoria or "").strip()
    if sub == ac.SUB_CRIACAO_USUARIO:
        return TIPO_CRIACAO
    if sub == ac.SUB_DESLIGAMENTO:
        return TIPO_DESLIGAMENTO
    return None


def _regiao(valor: Any) -> dict[str, str] | None:
    """``"082-ARARAQUARA"`` → ``{"codigo": "082", "nome": "ARARAQUARA", "completo": ...}``."""
    texto = str(valor or "").strip()
    if not texto:
        return None
    codigo, _, nome = texto.partition("-")
    return {"codigo": codigo.strip(), "nome": nome.strip() or codigo.strip(), "completo": texto}


def _primeira_palavra_em(mapa: dict[str, str], valor: Any) -> str | None:
    texto = str(valor or "").strip()
    for prefixo, enum in mapa.items():
        if texto.startswith(prefixo):
            return enum
    return None


def montar_payload(
    tipo: str,
    dados: dict[str, Any],
    chamado: dict[str, Any],
    *,
    pular_etapas: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Payload do job a partir do `dados_formulario` validado na abertura.

    Normaliza os rótulos escolhidos pelo RH para os enums que a automação já
    entende (`UserProfileType`, dispositivo, papel do portal) e desmonta a
    região em código/nome — é o formato que `flow.py` consome hoje pela CLI.
    ``pular_etapas`` = nomes de etapas já concluídas num job anterior
    (reexecução só das pendências)."""
    base: dict[str, Any] = {
        "versao": VERSAO_CONTRATO,
        "tipo": tipo,
        "chamado": {"id": str(chamado.get("id") or ""), "codigo": chamado.get("codigo") or ""},
        "pular_etapas": list(pular_etapas),
    }
    perfil = _PERFIL_ENUM.get(str(dados.get("perfil") or ""))
    if tipo == TIPO_CRIACAO:
        base.update(
            {
                "nome_completo": str(dados.get("nome_completo") or "").strip(),
                "email": str(dados.get("email") or "").strip().lower(),
                "perfil": perfil,
                "telefone": str(dados.get("telefone") or "").strip(),
                "gestor_email": (str(dados.get("gestor_email") or "").strip().lower() or None),
                "data_inicio": dados.get("data_inicio") or None,
                "cargo": (str(dados.get("cargo") or "").strip() or None),
                "portal_papel": _primeira_palavra_em(_PAPEL_ENUM, dados.get("portal_papel")),
                "portal_setor": (str(dados.get("portal_setor") or "").strip() or None),
                "licencas_sap": list(dados.get("licencas_sap") or []),
                "regiao": _regiao(dados.get("regiao_wmw")),
                "dispositivo_wmw": _primeira_palavra_em(_DISPOSITIVO_ENUM, dados.get("dispositivo_wmw")),
                "observacoes": (str(dados.get("observacoes") or "").strip() or None),
            }
        )
    elif tipo == TIPO_DESLIGAMENTO:
        base.update(
            {
                "nome_completo": str(dados.get("nome_completo") or "").strip(),
                "email": str(dados.get("email") or "").strip().lower(),
                "perfil": perfil,
                "data_desligamento": dados.get("data_desligamento") or None,
                "regiao": _regiao(dados.get("regiao")),
                "motivo": str(dados.get("motivo") or "").strip() or ac.MOTIVO_DESLIGAMENTO_PADRAO,
                "encaminhar_para": str(dados.get("encaminhar_para") or "").strip().lower(),
                "observacoes": (str(dados.get("observacoes") or "").strip() or None),
            }
        )
    else:
        raise ValueError(f"tipo de job sem payload de formulário: {tipo}")
    return base


def montar_payload_revogar_licenca(
    licenca: dict[str, Any], chamado: dict[str, Any]
) -> dict[str, Any]:
    """Payload do job de revogação da licença M365 (nasce ao concluir um
    DESLIGAMENTO; ``licenca`` vem do resultado do worker — ver contrato)."""
    return {
        "versao": VERSAO_CONTRATO,
        "tipo": TIPO_REVOGAR_LICENCA,
        "chamado": {"id": str(chamado.get("id") or ""), "codigo": chamado.get("codigo") or ""},
        "pular_etapas": [],
        "email": str(licenca.get("email") or "").strip().lower(),
        "ms_user_id": str(licenca.get("ms_user_id") or "").strip() or None,
        "offboard_date": licenca.get("offboard_date") or None,
    }


def _para_date(valor: Any) -> date | None:
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


def dia_util_anterior(dia: date, feriados: set[date]) -> date:
    """Dia útil (seg–sex, fora de ``feriados``) imediatamente anterior a ``dia``."""
    atual = dia - timedelta(days=1)
    while atual.weekday() >= 5 or atual in feriados:
        atual -= timedelta(days=1)
    return atual


def calcular_executar_apos(
    tipo: str,
    payload: dict[str, Any],
    *,
    agora: datetime,
    feriados: set[date],
    hora_criacao: int = 7,
    hora_desligamento: int = 18,
    licenca_dias: int = 15,
) -> datetime:
    """Quando o worker pode pegar o job (UTC, aware). Nunca no passado: data
    vencida ou ausente ⇒ ``agora``."""
    if agora.tzinfo is None:
        raise ValueError("agora precisa ser timezone-aware")
    alvo: datetime | None = None
    if tipo == TIPO_CRIACAO:
        inicio = _para_date(payload.get("data_inicio"))
        if inicio:
            alvo = datetime.combine(
                dia_util_anterior(inicio, feriados), time(hour=hora_criacao), tzinfo=TZ_BR
            )
    elif tipo == TIPO_DESLIGAMENTO:
        dia = _para_date(payload.get("data_desligamento"))
        if dia:
            alvo = datetime.combine(dia, time(hour=hora_desligamento), tzinfo=TZ_BR)
    elif tipo == TIPO_REVOGAR_LICENCA:
        dia = _para_date(payload.get("offboard_date")) or agora.astimezone(TZ_BR).date()
        alvo = datetime.combine(dia + timedelta(days=licenca_dias), time(hour=hora_criacao), tzinfo=TZ_BR)
    if alvo is None or alvo <= agora:
        return agora.astimezone(UTC)
    return alvo.astimezone(UTC)


def _mascarar(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {
            k: (MASCARA if _CHAVE_SENSIVEL_RE.search(str(k)) and v not in (None, "") else _mascarar(v))
            for k, v in valor.items()
        }
    if isinstance(valor, list):
        return [_mascarar(v) for v in valor]
    return valor


def normalizar_etapas(brutas: Any) -> list[dict[str, Any]]:
    """Lista de etapas devolvida pelo worker → forma canônica e SEM segredos.

    Aceita o dump de ``StepResult`` da automação (``step_name``, ``status``,
    ``error_message``, ``details``) e tolera lixo: etapa sem nome ou com
    status desconhecido vira ``FAILED`` com a razão registrada, para nunca
    contar como sucesso por acidente."""
    etapas: list[dict[str, Any]] = []
    for item in brutas if isinstance(brutas, list) else []:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("step_name") or item.get("nome") or "").strip()
        status_txt = str(item.get("status") or "").strip().upper()
        erro = item.get("error_message") or item.get("erro") or None
        if not nome:
            nome = "(etapa sem nome)"
            status_txt, erro = ETAPA_FAILED, "resultado sem nome de etapa"
        if status_txt not in _ETAPA_STATUS_VALIDOS:
            erro = erro or f"status desconhecido: {status_txt or 'vazio'}"
            status_txt = ETAPA_FAILED
        detalhes = item.get("details") if isinstance(item.get("details"), dict) else {}
        etapas.append(
            {
                "nome": nome[:200],
                "status": status_txt,
                "erro": (str(erro)[:1000] if erro else None),
                "detalhes": _mascarar(detalhes),
            }
        )
    return etapas


def etapa_concluida(etapa: dict[str, Any]) -> bool:
    """SUCCESS nesta execução, ou SKIPPED por já ter sido concluída numa anterior."""
    if etapa.get("status") == ETAPA_SUCCESS:
        return True
    return etapa.get("status") == ETAPA_SKIPPED and (etapa.get("erro") or "").strip() == ETAPA_JA_CONCLUIDA_MSG


def classificar(etapas: list[dict[str, Any]], *, erro_geral: str | None = None) -> str:
    """Status final do job a partir das etapas: tudo concluído ⇒ CONCLUIDO;
    nada concluído nesta execução (ou erro fora do fluxo, ou lista vazia) ⇒
    FALHOU; misto ⇒ CONCLUIDO_COM_PENDENCIAS. Etapas puladas por já terem
    sido feitas antes (`etapa_concluida`) contam como concluídas."""
    if erro_geral or not etapas:
        return STATUS_FALHOU
    concluidas = sum(1 for e in etapas if etapa_concluida(e))
    sucessos_agora = sum(1 for e in etapas if e["status"] == ETAPA_SUCCESS)
    if concluidas == len(etapas):
        return STATUS_CONCLUIDO
    if sucessos_agora == 0:
        return STATUS_FALHOU
    return STATUS_COM_PENDENCIAS


def etapas_concluidas(resultado: Any) -> tuple[str, ...]:
    """Nomes das etapas com SUCCESS num `resultado` já gravado — viram
    ``pular_etapas`` da reexecução."""
    return tuple(
        e["nome"] for e in (resultado if isinstance(resultado, list) else [])
        if isinstance(e, dict) and e.get("nome") and etapa_concluida(e)
    )


def filtrar_credenciais(brutas: Any) -> list[tuple[str, str]]:
    """Pares (rótulo, valor) das credenciais reconhecidas pelo contrato, na
    ordem de exibição. Chaves fora do contrato são ignoradas."""
    if not isinstance(brutas, dict):
        return []
    pares = []
    for chave, rotulo in _CREDENCIAIS_LABEL:
        valor = brutas.get(chave)
        if valor not in (None, ""):
            pares.append((rotulo, str(valor)))
    return pares


# --------------------------------------------------------------------------
# Textos gravados no chamado
# --------------------------------------------------------------------------
_TIPO_LABEL = {
    TIPO_CRIACAO: "criação de acessos",
    TIPO_DESLIGAMENTO: "desligamento de acessos",
    TIPO_REVOGAR_LICENCA: "revogação da licença Microsoft 365",
}
# Artigo + rótulo, para frases como "O desligamento foi executado" (concordância).
_TIPO_FRASE = {
    TIPO_CRIACAO: ("A", "criação de acessos", "concluída", "executada"),
    TIPO_DESLIGAMENTO: ("O", "desligamento de acessos", "concluído", "executado"),
    TIPO_REVOGAR_LICENCA: ("A", "revogação da licença Microsoft 365", "concluída", "executada"),
}
_ETAPA_ICONE = {ETAPA_SUCCESS: "✅", ETAPA_FAILED: "❌", ETAPA_SKIPPED: "⏭️"}


def _linhas_etapas(etapas: list[dict[str, Any]], *, com_detalhes: bool) -> list[str]:
    linhas = []
    for e in etapas:
        linha = f"{_ETAPA_ICONE.get(e['status'], '•')} {e['nome']}"
        if e["status"] != ETAPA_SUCCESS and e.get("erro"):
            linha += f" — {e['erro']}"
        linhas.append(linha)
        if com_detalhes and e.get("detalhes"):
            for k, v in e["detalhes"].items():
                if v in (None, "", [], {}):
                    continue
                linhas.append(f"    · {k}: {v if not isinstance(v, list) else ', '.join(map(str, v))}")
    return linhas


def texto_nota_interna(
    job: dict[str, Any], etapas: list[dict[str, Any]], *, erro_geral: str | None = None
) -> str:
    """Nota interna técnica (só staff) com o resultado etapa a etapa."""
    tipo = _TIPO_LABEL.get(str(job.get("tipo")), str(job.get("tipo")))
    cabecalho = f"Automação de {tipo}"
    if job.get("dry_run"):
        cabecalho += " — SIMULAÇÃO (dry-run, nada foi alterado nos sistemas)"
    partes = [cabecalho, f"Worker: {job.get('worker_id') or '—'}", ""]
    if erro_geral:
        partes += [f"Erro fora do fluxo: {erro_geral}", ""]
    partes += _linhas_etapas(etapas, com_detalhes=True) or ["(nenhuma etapa reportada)"]
    pendentes = [e["nome"] for e in etapas if not etapa_concluida(e)]
    if pendentes:
        partes += ["", "Pendências para ação manual da TI: " + "; ".join(pendentes)]
    return "\n".join(partes)


def texto_mensagem_publica(
    job: dict[str, Any],
    etapas: list[dict[str, Any]],
    credenciais: list[tuple[str, str]],
    status_final: str,
) -> str:
    """Mensagem ao RH (autor). Quando tudo deu certo (D4), é a mensagem de
    encerramento com as credenciais; com pendências, é um parcial sem senhas
    — o TI conclui manualmente e entrega o que faltar."""
    tipo = _TIPO_LABEL.get(str(job.get("tipo")), str(job.get("tipo")))
    if job.get("dry_run"):
        return (
            f"Simulação da automação de {tipo} executada (nada foi alterado nos sistemas). "
            "A TI vai conferir o resultado e dar sequência."
        )
    artigo, _, concluida, executada = _TIPO_FRASE.get(str(job.get("tipo")), ("A", tipo, "concluída", "executada"))
    if status_final == STATUS_CONCLUIDO:
        partes = [f"{artigo} {tipo} foi {concluida} em todos os sistemas:", ""]
        partes += _linhas_etapas(etapas, com_detalhes=False)
        if credenciais:
            partes += ["", "Credenciais e dados de acesso:"]
            partes += [f"• {rotulo}: {valor}" for rotulo, valor in credenciais]
            partes += ["", "Guarde estas informações e repasse ao colaborador por canal seguro."]
        return "\n".join(partes)
    partes = [f"{artigo} {tipo} foi {executada} parcialmente:", ""]
    partes += _linhas_etapas(etapas, com_detalhes=False) or ["(nenhuma etapa concluída)"]
    partes += ["", "A TI vai concluir manualmente o que ficou pendente e retornar por aqui."]
    return "\n".join(partes)


def texto_email_alerta_ti(
    job: dict[str, Any],
    etapas: list[dict[str, Any]],
    status_final: str,
    *,
    erro_geral: str | None,
    link: str,
) -> str:
    """Alerta aos admins da TI (regra do gestor, 2026-09-15): detalhado o
    bastante para agir sem abrir o log — quem, o quê, cada etapa com o erro
    que o sistema devolveu, o que ficou por fazer e onde continuar."""
    tipo = _TIPO_LABEL.get(str(job.get("tipo")), str(job.get("tipo")))
    payload = job.get("payload") or {}
    quem = " · ".join(
        str(v) for v in (payload.get("nome_completo"), payload.get("email"), payload.get("perfil")) if v
    )
    partes = [
        f"Chamado: {job.get('chamado_codigo') or '—'} — {job.get('chamado_titulo') or ''}".rstrip(" —"),
        f"Automação: {tipo}{' (SIMULAÇÃO — nada foi alterado)' if job.get('dry_run') else ''}",
        f"Colaborador: {quem or '—'}",
        f"Status final: {STATUS_LABEL.get(status_final, status_final)} ({status_final})",
        f"Worker: {job.get('worker_id') or '—'} · job {job.get('id') or '—'} · tentativa {job.get('tentativas') or 1}",
    ]
    inicio, fim = job.get("claimed_at"), job.get("finalizado_em")
    if inicio or fim:
        partes.append(f"Execução: {_fmt_dt(inicio)} → {_fmt_dt(fim)}")
    partes.append("")
    if erro_geral:
        partes += ["ERRO FORA DO FLUXO (o worker não completou as etapas):", f"  {erro_geral}", ""]
    partes.append("Etapas:")
    for e in etapas:
        linha = f"  {_ETAPA_ICONE.get(e['status'], '•')} {e['status']:<8} {e['nome']}"
        partes.append(linha)
        if e.get("erro"):
            partes.append(f"        erro: {e['erro']}")
        if e["status"] == ETAPA_FAILED and e.get("detalhes"):
            for k, v in e["detalhes"].items():
                if v not in (None, "", [], {}):
                    partes.append(f"        {k}: {v}")
    pendentes = [e["nome"] for e in etapas if not etapa_concluida(e)]
    partes.append("")
    if pendentes:
        partes += ["Ficou por fazer (ação manual ou 'Reexecutar pendências' no card):"]
        partes += [f"  - {n}" for n in pendentes]
    else:
        partes.append("Nenhuma etapa pendente.")
    partes += ["", f"Chamado no portal: {link}", ""]
    return "\n".join(partes)


def _fmt_dt(valor: Any) -> str:
    if not isinstance(valor, datetime):
        return "—"
    return valor.astimezone(TZ_BR).strftime("%d/%m/%Y %H:%M:%S")


def texto_email_neutro(codigo: str, status_final: str) -> str:
    """Corpo do e-mail de "nova mensagem" para a mensagem da automação — sem
    reproduzir o conteúdo (que pode ter credenciais)."""
    if status_final == STATUS_CONCLUIDO:
        return (
            f"A automação de acessos do chamado {codigo} foi concluída. As credenciais e o "
            "resumo estão disponíveis no próprio chamado — abra pelo link."
        )
    return (
        f"A automação de acessos do chamado {codigo} foi executada com pendências. "
        "O detalhe está no próprio chamado — abra pelo link."
    )


def resumo_payload(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Pares (rótulo, valor) do payload para o card do atendimento — o que o
    TI confere ANTES de clicar em executar."""
    if not payload:
        return []
    pares: list[tuple[str, str]] = []

    def _add(rotulo: str, valor: Any) -> None:
        if valor in (None, "", [], {}):
            return
        if isinstance(valor, dict):
            valor = valor.get("completo") or valor
        if isinstance(valor, list):
            valor = ", ".join(map(str, valor))
        pares.append((rotulo, str(valor)))

    _add("Nome", payload.get("nome_completo"))
    _add("E-mail", payload.get("email"))
    _add("Perfil", payload.get("perfil"))
    _add("Telefone", payload.get("telefone"))
    _add("Data de início", payload.get("data_inicio"))
    _add("Data do desligamento", payload.get("data_desligamento"))
    _add("Gestor", payload.get("gestor_email"))
    _add("Cargo", payload.get("cargo"))
    _add("Papel no portal", payload.get("portal_papel"))
    _add("Setor no portal", payload.get("portal_setor"))
    _add("Licenças SAP", payload.get("licencas_sap"))
    _add("Região", payload.get("regiao"))
    _add("Dispositivo WMW", payload.get("dispositivo_wmw"))
    _add("Motivo", payload.get("motivo"))
    _add("Encaminhar e-mails para", payload.get("encaminhar_para"))
    _add("Data do desligamento (licença)", payload.get("offboard_date"))
    _add("Etapas a pular (já concluídas)", payload.get("pular_etapas"))
    return pares
