"""Registro dos formulários dinâmicos da abertura de chamado (F1, 2026-09-14).

Ponto único onde a rota de abertura (`app/routes/portal.py`) pergunta "esta
combinação departamento/categoria/subcategoria tem um layout de campos?".
Dois donos de schema hoje:

- `formularios_quimico.py` — layout por **categoria** do Dpto Químico (2026-07);
- `formularios_acessos.py` — layout por **subcategoria** da TI (Criação de
  Novo Usuário / Desligamento), restrito por autor (D3 do plano de automação).

O motor (validação, prefill, rótulos) é o de `campos_dinamicos.py`. Este
módulo só resolve QUAL layout vale e embrulha as diferenças entre os donos
(de onde vêm Assunto/Descrição automáticos, se Prioridade some da tela).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain import formularios_acessos as acessos, formularios_quimico as quimico
from app.domain.campos_dinamicos import (
    CampoDef,
    rotular_campos,
    validar_campos,
    valores_para_template,
)


@dataclass(frozen=True)
class Layout:
    """Um layout resolvido para a tela de abertura.

    ``oculta_assunto``: a tela esconde Assunto/Descrição e o servidor os deriva
    das respostas (`titulo_e_descricao`). ``oculta_prioridade``: a tela esconde
    o select de Prioridade (mantém o padrão MEDIA) — só o Químico faz isso.
    """

    origem: str  # "quimico" | "acessos"
    chave: str  # nome da categoria (Químico) ou da subcategoria (acessos)
    campos: tuple[CampoDef, ...]
    observacao: str = ""
    oculta_assunto: bool = True
    oculta_prioridade: bool = False
    # Nome do campo (do próprio layout) a partir do qual a tela SUGERE o e-mail
    # corporativo em `campo__email` (``primeiro.ultimo@bondmann.com.br``) —
    # conveniência de UI (`novo_chamado.js`), o servidor não depende disso.
    sugere_email_de: str = ""

    def validar(self, dados: dict[str, list[str]]) -> tuple[bool, str | None, dict[str, Any]]:
        return validar_campos(self.campos, dados)

    def prefill(self, dados: dict[str, list[str]]) -> dict[str, Any]:
        return valores_para_template(self.campos, dados)

    def titulo_e_descricao(self, dados: dict[str, Any]) -> tuple[str, str]:
        if self.origem == "quimico":
            return quimico.titulo_e_descricao_automaticos(self.chave, dados)
        return acessos.titulo_e_descricao_automaticos(self.chave, dados)


def _eh_departamento_quimico(nome: str | None) -> bool:
    """Mesma fórmula de `PortalService.quimico_dep_id` ("Dpto Químico", 0027/0049)."""
    return (nome or "").strip().lower() == "dpto químico"


def layout_para(
    *,
    departamento: str | None,
    categoria: str | None,
    subcategoria: str | None,
    perfil_autor: dict[str, Any] | None,
    setores_portal: tuple[str, ...] = (),
) -> Layout | None:
    """Resolve o layout da abertura, ou ``None`` quando a escolha não tem
    campos dinâmicos (ou o autor não pode usá-los — D3).

    Nomes, não ids: os schemas são indexados por nome do catálogo (precedente
    do Químico), então a rota resolve os nomes via `ChamadosRepo` e passa aqui.
    ``setores_portal`` alimenta o select "Setor / departamento do colaborador"
    do layout de criação de usuário (lista viva do banco).
    """
    # Químico: layout por categoria, mas só quando o DESTINO é o Dpto Químico —
    # antes da generalização o `if eh_quimico` da rota era por departamento, e
    # uma categoria homônima de outro setor nunca herdava o layout. Mantido.
    if quimico.eh_categoria_quimico(categoria) and _eh_departamento_quimico(departamento):
        return Layout(
            origem="quimico",
            chave=categoria or "",
            campos=quimico.campos_da_categoria(categoria),
            observacao=quimico.observacao_categoria(categoria),
            oculta_assunto=True,
            oculta_prioridade=True,
        )
    if acessos.eh_subcategoria_acessos(departamento, categoria, subcategoria):
        if not acessos.autor_pode_usar_layout(perfil_autor):
            return None
        sub = (subcategoria or "").strip()
        campos = (
            acessos.campos_criacao(setores_portal)
            if sub == acessos.SUB_CRIACAO_USUARIO
            else acessos.CAMPOS_DESLIGAMENTO
        )
        return Layout(
            origem="acessos",
            chave=sub,
            campos=campos,
            oculta_assunto=True,
            sugere_email_de="nome_completo" if sub == acessos.SUB_CRIACAO_USUARIO else "",
        )
    return None


def rotular_chamado(
    categoria: str | None, subcategoria: str | None, dados: dict[str, Any] | None
) -> list[tuple[str, str]]:
    """Pares ``(label, valor)`` de `dados_formulario` de um chamado já gravado,
    qualquer que seja o dono do layout. Para exibição (detalhe, atendimento,
    contexto da IA). Sem gate de autor: quem vê o chamado vê seus campos.
    Chamado sem layout conhecido devolve as chaves cruas (histórico)."""
    if not dados:
        return []
    if quimico.eh_categoria_quimico(categoria):
        return quimico.rotular(categoria, dados)
    sub = (subcategoria or "").strip()
    if sub == acessos.SUB_CRIACAO_USUARIO:
        return rotular_campos(acessos.campos_criacao(()), dados)
    if sub == acessos.SUB_DESLIGAMENTO:
        return rotular_campos(acessos.CAMPOS_DESLIGAMENTO, dados)
    return rotular_campos((), dados)
