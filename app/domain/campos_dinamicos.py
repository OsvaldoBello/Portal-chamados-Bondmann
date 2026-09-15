"""Motor genérico dos formulários dinâmicos (F1 da automação de acessos, 2026-09-14).

Extraído de `app/domain/formularios_quimico.py`, que nasceu como o único dono
de "campos por categoria" (2026-07). Com a chegada dos layouts por
**subcategoria** da TI (Criação de Novo Usuário / Desligamento — plano em
`plano_md_mestre_automacao_acessos.md`, Seção 4), a definição de campo, a
validação e o prefill deixaram de ser específicos do Químico e vieram para
cá. `formularios_quimico.py` continua dono dos schemas do Químico e da sua
API pública (`validar_payload`, `rotular`, ...) — só delega para este módulo.

Novidade em relação ao motor original: campo **condicional** (`visivel_se`).
Um campo com ``visivel_se=("perfil", ("INTERNO",))`` só existe quando o campo
``perfil`` do MESMO payload vale ``INTERNO`` — fora disso ele é ignorado na
validação (nem obrigatório, nem gravado) e escondido na tela
(`novo_chamado.js`). A visibilidade é sempre calculada a partir dos valores
submetidos, nunca de estado do front — defesa em profundidade contra POST
forjado, o mesmo princípio das chaves conhecidas do schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Tipos de campo suportados pelo partial `_campos_dinamicos.html` e pela validação.
# ``checkbox_multi``: 0..N opções marcadas — valor gravado é ``list[str]``.
TIPOS_VALIDOS = {"text", "textarea", "select", "date", "number", "email", "tel", "checkbox_multi"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class CampoDef:
    """Definição de um campo dinâmico de formulário.

    ``name`` é a chave em ``dados_formulario`` (e o sufixo do input HTML
    ``campo__<name>``). ``opcoes`` se aplica a ``select``/``checkbox_multi``.
    ``min_chars`` valida um tamanho mínimo em campos de texto (ex.: Lote, Fone).
    ``visivel_se`` = ``(name_do_campo_controlador, valores_que_exibem)`` — o
    campo só é validado/gravado/exibido quando o controlador (um ``select`` do
    mesmo layout, declarado ANTES dele) está com um desses valores.
    ``dominio_email``: em campos ``email``, exige que o endereço termine nesse
    domínio (ex.: ``"bondmann.com.br"``).
    """

    name: str
    label: str
    tipo: str = "text"
    obrigatorio: bool = False
    opcoes: tuple[str, ...] = field(default_factory=tuple)
    ajuda: str = ""
    min_chars: int = 0
    visivel_se: tuple[str, tuple[str, ...]] | None = None
    dominio_email: str = ""
    placeholder: str = ""


def _valor_controlador(dados: dict[str, Any], name: str) -> str:
    """Primeiro valor submetido do campo ``name`` (lista bruta ou já limpo)."""
    bruto = dados.get(name)
    if isinstance(bruto, list):
        bruto = bruto[0] if bruto else ""
    return str(bruto or "").strip()


def campo_visivel(campo: CampoDef, dados: dict[str, Any]) -> bool:
    """O campo está ativo para este payload? (sem ``visivel_se`` ⇒ sempre.)"""
    if campo.visivel_se is None:
        return True
    controlador, valores = campo.visivel_se
    return _valor_controlador(dados, controlador) in valores


def campos_visiveis(campos: tuple[CampoDef, ...], dados: dict[str, Any]) -> tuple[CampoDef, ...]:
    return tuple(c for c in campos if campo_visivel(c, dados))


def validar_campos(
    campos: tuple[CampoDef, ...], dados: dict[str, list[str]]
) -> tuple[bool, str | None, dict[str, Any]]:
    """Valida as respostas de um layout de campos dinâmicos.

    ``dados`` mapeia ``name -> lista de valores brutos`` como submetidos (um
    campo normal chega como lista de 1 item; um ``checkbox_multi`` pode chegar
    com 0..N). Retorna ``(ok, erro, limpo)``: ``limpo`` só contém as chaves
    conhecidas do schema E visíveis para este payload (defesa em profundidade
    contra campos forjados no POST) — valor ``str`` para a maioria dos tipos,
    ``list[str]`` para ``checkbox_multi``. Layout vazio ⇒ ``(True, None, {})``.
    """
    if not campos:
        return True, None, {}

    limpo: dict[str, Any] = {}
    for campo in campos:
        if not campo_visivel(campo, dados):
            continue  # condicional inativo: ignora o que veio (nem obrigatório, nem grava)
        brutos = dados.get(campo.name) or []
        if campo.tipo == "checkbox_multi":
            marcados = [v.strip() for v in brutos if v.strip()]
            invalidas = [v for v in marcados if v not in campo.opcoes]
            if invalidas:
                return False, f'Opção inválida no campo "{campo.label}".', {}
            if campo.obrigatorio and not marcados:
                return False, f'Selecione ao menos uma opção em "{campo.label}".', {}
            if marcados:
                limpo[campo.name] = marcados
            continue

        valor = (brutos[0] if brutos else "").strip()
        if not valor:
            if campo.obrigatorio:
                return False, f'Preencha o campo "{campo.label}".', {}
            continue  # opcional vazio: não grava chave
        if campo.tipo == "select" and valor not in campo.opcoes:
            return False, f'Opção inválida no campo "{campo.label}".', {}
        if campo.tipo == "date":
            try:
                date.fromisoformat(valor)
            except ValueError:
                return False, f'Data inválida no campo "{campo.label}".', {}
        if campo.tipo == "number":
            try:
                int(valor)
            except ValueError:
                return False, f'Valor numérico inválido no campo "{campo.label}".', {}
        if campo.tipo == "email":
            if not _EMAIL_RE.match(valor):
                return False, f'E-mail inválido no campo "{campo.label}".', {}
            if campo.dominio_email and not valor.lower().endswith("@" + campo.dominio_email):
                return (
                    False,
                    f'O campo "{campo.label}" precisa ser um e-mail @{campo.dominio_email}.',
                    {},
                )
        if campo.min_chars and len(valor) < campo.min_chars:
            return (
                False,
                f'O campo "{campo.label}" precisa de pelo menos {campo.min_chars} caracteres.',
                {},
            )
        limpo[campo.name] = valor
    return True, None, limpo


def valores_para_template(
    campos: tuple[CampoDef, ...], dados: dict[str, list[str]]
) -> dict[str, Any]:
    """Normaliza os valores brutos submetidos para prefill no template Jinja.

    Espelha o formato de ``validar_campos`` (``str`` normal / ``list[str]``
    para ``checkbox_multi``), mas SEM validar — usado só para reexibir o que o
    usuário digitou quando o formulário volta com erro. Inclui também os
    campos condicionais inativos (o usuário pode trocar o controlador de
    volta e reencontrar o que digitou)."""
    resultado: dict[str, Any] = {}
    for campo in campos:
        brutos = dados.get(campo.name) or []
        if campo.tipo == "checkbox_multi":
            resultado[campo.name] = [v for v in brutos if v]
        elif brutos:
            resultado[campo.name] = brutos[0]
    return resultado


def rotular_campos(campos: tuple[CampoDef, ...], dados: dict[str, Any]) -> list[tuple[str, str]]:
    """Pares ``(label, valor)`` para exibição, na ordem do schema.

    Valores ``list`` (``checkbox_multi``) são juntados com "; ". Chaves
    presentes em ``dados`` mas ausentes do schema (ex.: campo removido depois
    de gravado) são anexadas ao final com o próprio ``name`` como rótulo, para
    não sumir com dado histórico."""
    if not dados:
        return []

    def _texto(valor: Any) -> str:
        return "; ".join(valor) if isinstance(valor, list) else str(valor)

    vistos: set[str] = set()
    pares: list[tuple[str, str]] = []
    for campo in campos:
        if campo.name in dados and dados[campo.name]:
            pares.append((campo.label, _texto(dados[campo.name])))
            vistos.add(campo.name)
    for chave, valor in dados.items():
        if chave not in vistos and valor:
            pares.append((chave, _texto(valor)))
    return pares
