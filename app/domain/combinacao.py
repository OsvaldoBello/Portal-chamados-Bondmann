"""Combinação de chamados (migration 0065) — montagem do texto e dos anexos.

Lógica pura: recebe o chamado duplicado (e as mensagens **públicas** dele, só
para os anexos), devolve o texto da mensagem única que é publicada no chamado
PRINCIPAL e a lista consolidada de anexos. Fica em `app/domain/` (não no
repositório) porque não toca banco nem RLS e é o pedaço da feature que dá para
testar sem Supabase.

Por que uma mensagem só, e não copiar cada mensagem do duplicado como uma
mensagem separada: `mensagens.remetente_id` é a autoria real da fala e a policy
`mensagens_insert` (0042) exige ``remetente_id = auth.uid()`` — reescrever
autoria alheia exigiria conexão administrativa e ainda produziria uma conversa
com falas antigas surgindo no meio do histórico do principal, sem contexto de
onde vieram. A digest única é publicada por quem combinou, no momento em que
combinou, e diz de qual chamado ela veio.

**Só o DESCRITIVO, nunca a conversa** (decisão do gestor 2026-07-30, revisando a
0065): a digest traz código, autor, contato, assunto e descrição do duplicado —
e para por aí. Trazer as falas públicas junto (comportamento original) poluía o
chat do principal, ainda mais porque numa combinação de N chamados a conversa
inteira de cada um era despejada de uma vez; e o que mais aparecia ali era a
troca de **perguntas da triagem por IA** com o autor (mensagens públicas como
quaisquer outras — `app/ia/triagem.py`), que é raciocínio do chamado repetido,
não informação nova sobre a ocorrência. Quem precisar do detalhe tem o link do
duplicado em "Chamados combinados neste". Os **anexos** continuam vindo: são o
print do erro, a evidência — arquivo não polui conversa.

Formatação: cada bloco é separado por uma LINHA EM BRANCO de propósito —
``app.templating.paragrafos_mensagem`` (usada na renderização do chat) rejunta
linhas soltas de um mesmo parágrafo e só respeita a quebra entre parágrafos.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_TZ_BR = ZoneInfo("America/Sao_Paulo")

__all__ = ["texto_combinacao", "anexos_combinacao"]


def _dt(valor: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    return valor.astimezone(_TZ_BR).strftime(fmt) if valor else "—"


def texto_combinacao(duplicado: dict[str, Any]) -> str:
    """Digest do chamado ``duplicado`` para publicar no principal.

    Só o descritivo da abertura — nenhuma mensagem do chat do duplicado entra
    aqui (ver o cabeçalho do módulo). Os arquivos daquele chamado entram pelo
    :func:`anexos_combinacao`, na mesma mensagem.
    """
    autor = (duplicado.get("cliente_nome") or "").strip() or "autor não identificado"
    setor_autor = (duplicado.get("cliente_departamento") or "").strip()
    origem = f"{autor} ({setor_autor})" if setor_autor else autor
    contato = (duplicado.get("telefone_contato") or "").strip()

    abertura = f"Aberto por {origem} em {_dt(duplicado.get('created_at'))}"
    if contato:
        abertura += f" · contato {contato}"

    blocos = [
        f"🔗 Chamado {duplicado.get('codigo') or ''} combinado com este (mesma ocorrência).".strip(),
        abertura + ".",
        f"Assunto: {(duplicado.get('titulo') or '').strip() or '—'}",
    ]
    descricao = (duplicado.get("descricao") or "").strip()
    if descricao:
        blocos.append(descricao)

    return "\n\n".join(blocos)


def anexos_combinacao(mensagens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anexos das mensagens públicas do duplicado, sem repetir o mesmo ``path``.

    Continuam vindo mesmo depois de a digest deixar de trazer as falas: o
    arquivo (print do erro, log) é a evidência da ocorrência e aparece como
    anexo da mensagem, não como texto no meio da conversa. Inclui os anexos da
    própria abertura do chamado, que moram na primeira mensagem.

    Os bytes NÃO são recopiados no Storage: o path continua valendo (o bucket é
    escopado por ``empresa_id``, não por chamado — policy ``anexos_select``,
    migration 0007), então a signed URL gerada na renderização do principal
    funciona igual. Copiar arquivo duplicaria armazenamento sem ganho.
    """
    vistos: set[str] = set()
    saida: list[dict[str, Any]] = []
    for m in mensagens:
        for anexo in m.get("anexos") or []:
            path = anexo.get("path")
            if not path or path in vistos:
                continue
            vistos.add(path)
            saida.append(anexo)
    return saida
