"""Formulários obrigatórios do RH por subcategoria (feature 2026-08-10).

Pedido do RH: para as subcategorias abaixo, o chamado só pode ser concluído
(``status = 'RESOLVIDO'``) depois de um anexo (o FB/formulário preenchido) ter
sido enviado no chamado — em qualquer mensagem, pública ou nota interna, por
quem for (o solicitante na abertura/resposta, ou o próprio RH). A tela de
abertura mostra o modelo em branco para download assim que a subcategoria é
escolhida (``novo_chamado.js``); a tela de atendimento bloqueia a conclusão
enquanto não houver nenhum anexo (``app/repositories/atendimento.py::formulario_pendente``).

Mesmo padrão do precedente do Químico (`app/domain/formularios_quimico.py`):
mapeamento **em código**, indexado pelo **nome da subcategoria** — não há
coluna nova em `subcategorias` nem tela de admin para isso, porque o conjunto
é fixo (os FBs oficiais do RH, anexados pelo usuário), não configurável.

Os arquivos-modelo (em branco, para download) ficam em
``app/static/formularios/rh/`` e são servidos como estáticos públicos — são
documentos em branco, sem dado de colaborador, então não precisam de RLS/Storage
privado como os anexos de um chamado.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArquivoFormulario:
    """Um arquivo-modelo (em branco) disponível para download."""

    nome: str
    url: str


@dataclass(frozen=True)
class FormularioObrigatorio:
    """Formulário(s) exigido(s) por uma subcategoria antes da conclusão do chamado."""

    label: str
    arquivos: tuple[ArquivoFormulario, ...]


_BASE = "/static/formularios/rh"

FORMULARIOS_POR_SUBCATEGORIA: dict[str, FormularioObrigatorio] = {
    "Programa de Incentivo à Educação": FormularioObrigatorio(
        label="Programa de Incentivo à Educação",
        arquivos=(
            ArquivoFormulario(
                "Requerimento — Programa de Incentivo à Educação",
                f"{_BASE}/programa-incentivo-educacao-requerimento.docx",
            ),
            ArquivoFormulario(
                "Termo de Aceite — Programa de Incentivo à Educação",
                f"{_BASE}/programa-incentivo-educacao-termo-aceite.pdf",
            ),
        ),
    ),
    "Aumento de Quadro": FormularioObrigatorio(
        label="Aumento de Quadro",
        arquivos=(
            ArquivoFormulario(
                "FB031 — Solicitação de contratação",
                f"{_BASE}/fb031-solicitacao-contratacao.docx",
            ),
        ),
    ),
    "Alteração de Função": FormularioObrigatorio(
        label="Alteração de Função",
        arquivos=(
            ArquivoFormulario("FB030 — Alteração de cargo", f"{_BASE}/fb030-alteracao-cargo.docx"),
        ),
    ),
    "Encerramento de Contrato": FormularioObrigatorio(
        label="Encerramento de Contrato",
        arquivos=(
            ArquivoFormulario(
                "FB032 — Solicitação de encerramento de contrato",
                f"{_BASE}/fb032-encerramento-contrato.docx",
            ),
        ),
    ),
    "Solicitação de ajuda de custo": FormularioObrigatorio(
        label="Solicitação de ajuda de custo",
        arquivos=(
            ArquivoFormulario(
                "FB — Solicitação de ajuda de custo",
                f"{_BASE}/fb-solicitacao-ajuda-custo.docx",
            ),
        ),
    ),
    "Registros de Treinamentos": FormularioObrigatorio(
        label="Registros de Treinamentos",
        arquivos=(
            ArquivoFormulario(
                "FB010 — Registro de Treinamento",
                f"{_BASE}/fb010-registro-treinamento.docx",
            ),
        ),
    ),
}


def formulario_da_subcategoria(nome_subcategoria: str | None) -> FormularioObrigatorio | None:
    """O formulário obrigatório da subcategoria (por nome), ou ``None`` se ela
    não exige nenhum."""
    if not nome_subcategoria:
        return None
    return FORMULARIOS_POR_SUBCATEGORIA.get(nome_subcategoria)


def exige_formulario(nome_subcategoria: str | None) -> bool:
    return formulario_da_subcategoria(nome_subcategoria) is not None
