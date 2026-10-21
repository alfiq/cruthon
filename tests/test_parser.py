"""Pruebas del analizador sintáctico de Cruthon."""

from __future__ import annotations

import pytest

from cruthon import nodes as N
from cruthon.errors import CruthonSyntaxError, IncompleteInput
from cruthon.parser import parse


def test_modulo_vacio():
    module = parse("\n")
    assert module.body == []


def test_declaracion_de_blob():
    module = parse("blob main:\n    x = 1\n")
    statement = module.body[0]
    assert isinstance(statement, N.BlobDecl)
    assert statement.name == "main"
    assert statement.source is None
    assert len(statement.body) == 1


def test_blob_con_operacion_de_conjuntos():
    module = parse("blob carrito = frontend & backend:\n    x = 1\n")
    statement = module.body[0]
    assert isinstance(statement.source, N.BinOp)
    assert statement.source.op == "&"


def test_func_usa_la_palabra_clave_func():
    module = parse("func f(a, b):\n    return a + b\n")
    statement = module.body[0]
    assert isinstance(statement, N.FuncDef)
    assert statement.name == "f"
    assert [arg.name for arg in statement.args.args] == ["a", "b"]


def test_def_sigue_siendo_alias_de_func():
    module = parse("def f():\n    return 1\n")
    assert isinstance(module.body[0], N.FuncDef)


def test_expresiones_aritmeticas():
    module = parse("x = 1 + 2 * 3\n")
    assignment = module.body[0]
    assert isinstance(assignment.value, N.BinOp)
    assert assignment.value.op == "+"
    assert assignment.value.right.op == "*"


def test_llamada_con_palabras_clave():
    module = parse("print('hola', 1)\n")
    call = module.body[0].value
    assert isinstance(call, N.Call)
    assert len(call.args) == 2


def test_comprehension():
    module = parse("x = [i * 2 for i in range(3) if i]\n")
    assert isinstance(module.body[0].value, N.ListComp)


def test_el_destino_de_un_for_no_se_confunde_con_una_comparacion():
    """`for i in xs` es un bucle, no la comparación `i in xs`."""
    module = parse("for i in range(3):\n    print(i)\n")
    loop = module.body[0]
    assert isinstance(loop, N.For)
    assert isinstance(loop.target, N.Name)
    assert loop.target.id == "i"


def test_destino_de_for_con_tupla():
    module = parse("for a, b in pares:\n    print(a)\n")
    loop = module.body[0]
    assert isinstance(loop.target, N.Tuple)
    assert [element.id for element in loop.target.elts] == ["a", "b"]


def test_comprehension_con_dos_generadores():
    module = parse("x = {k: v for k, v in pares}\n")
    comprehension = module.body[0].value
    assert isinstance(comprehension, N.DictComp)
    assert len(comprehension.generators) == 1


def test_el_bucle_for_se_cierra_con_una_linea_sin_sangrar():
    """Un bloque abierto y luego una línea sin sangrar: como en CPython."""
    module = parse("blob main:\n    x = 1\nprint(main.x)\n")
    assert len(module.body) == 2


def test_bloque_incompleto_es_entrada_incompleta():
    with pytest.raises(IncompleteInput):
        parse("blob main:\n")


def test_error_de_sintaxis():
    with pytest.raises(CruthonSyntaxError):
        parse("x = = 1\n")
