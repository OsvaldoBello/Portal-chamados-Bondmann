"""Testes do AtendimentoService (Sprint 2 / item 2.2, M2) — puros, sem banco."""

from __future__ import annotations

from app.services.atendimento import AtendimentoService

DEP_TI = "dep-ti"
DEP_MKT = "dep-marketing"
USER_STAFF = "staff-1"
USER_AUTOR = "autor-1"


def _chamado(*, departamento_id=DEP_TI, cliente_id=USER_AUTOR, operador_id=None, autoatendimento=False):
    return {
        "departamento_id": departamento_id,
        "cliente_id": cliente_id,
        "operador_id": operador_id,
        "autoatendimento": autoatendimento,
    }


def _perfil(*, departamento_id=DEP_TI):
    return {"departamento_id": departamento_id}


def test_dept_bate_true_quando_setores_iguais():
    assert AtendimentoService.dept_bate(_chamado(departamento_id=DEP_TI), _perfil(departamento_id=DEP_TI))


def test_dept_bate_false_quando_setores_diferentes():
    # Cenário do bug 0028: líder de setor acompanhando chamado de outro
    # departamento não pode ser tratado como se o setor batesse.
    assert not AtendimentoService.dept_bate(_chamado(departamento_id=DEP_MKT), _perfil(departamento_id=DEP_TI))


def test_dept_bate_false_quando_perfil_sem_departamento():
    # Super-admin (departamento_id nulo) nunca "bate" com um chamado — evita
    # (None == None) virar um match acidental.
    assert not AtendimentoService.dept_bate(
        _chamado(departamento_id=None), _perfil(departamento_id=None)
    )


def test_pode_reivindicar_quando_dept_bate_e_ninguem_assumiu():
    perm = AtendimentoService.permissoes(
        _chamado(operador_id=None), _perfil(), USER_STAFF
    )
    assert perm.pode_reivindicar and not perm.pode_atender


def test_pode_atender_quando_ja_assumido():
    perm = AtendimentoService.permissoes(
        _chamado(operador_id=USER_STAFF), _perfil(), USER_STAFF
    )
    assert perm.pode_atender and not perm.pode_reivindicar


def test_fora_do_setor_nao_reivindica_nem_atende():
    perm = AtendimentoService.permissoes(
        _chamado(departamento_id=DEP_MKT, operador_id=None), _perfil(departamento_id=DEP_TI), USER_STAFF
    )
    assert not perm.dept_bate
    assert not perm.pode_reivindicar and not perm.pode_atender


def test_autor_bloqueado_mesmo_com_dept_bate():
    # Segregação de função (0029): autor do chamado nunca vira responsável,
    # mesmo sendo do mesmo setor de destino.
    perm = AtendimentoService.permissoes(
        _chamado(cliente_id=USER_AUTOR, operador_id=None), _perfil(), USER_AUTOR
    )
    assert perm.eh_autor and perm.bloqueado_por_autoria
    assert not perm.pode_reivindicar and not perm.pode_atender


def test_autoatendimento_libera_autor_como_responsavel():
    # Exceção Marketing/RH (0038/0042): no autoatendimento o autor pode
    # reivindicar/atender o próprio chamado.
    perm = AtendimentoService.permissoes(
        _chamado(cliente_id=USER_AUTOR, operador_id=None, autoatendimento=True), _perfil(), USER_AUTOR
    )
    assert perm.eh_autor and not perm.bloqueado_por_autoria
    assert perm.pode_reivindicar and not perm.pode_atender
