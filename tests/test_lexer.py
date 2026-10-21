"""Pruebas del tokenizador de Cruthon."""

from __future__ import annotations

import pytest

from cruthon.errors import CruthonSyntaxError, IncompleteInput
from cruthon.lexer import TokenType, tokenize


def test_tokeniza_nombres_y_operadores():
    tokens = tokenize("a = b + 1\n")
    tipos = [token.type for token in tokens]
    assert tipos[0] is TokenType.NAME
    assert tipos[1] is TokenType.OP
    assert tokens[-1].type is TokenType.ENDMARKER


def test_indent_y_dedent():
    tokens = tokenize("blob main:\n    x = 1\n")
    tipos = [token.type for token in tokens]
    assert TokenType.INDENT in tipos
    assert TokenType.DEDENT in tipos
    assert tipos.index(TokenType.INDENT) < tipos.index(TokenType.DEDENT)


def test_lineas_en_blanco_no_generan_sangria():
    tokens = tokenize("a = 1\n\n\nb = 2\n")
    assert sum(1 for token in tokens if token.type is TokenType.INDENT) == 0


def test_comentarios_se_ignoran():
    tokens = tokenize("# un comentario\nx = 1  # otro\n")
    nombres = [token.value for token in tokens if token.type is TokenType.NAME]
    assert nombres == ["x"]


def test_numeros():
    tokens = [token for token in tokenize("1 2.5 0x10 1_000\n") if token.type is TokenType.NUMBER]
    assert [token.value for token in tokens] == [1, 2.5, 16, 1000]


def test_cadenas():
    tokens = [token for token in tokenize("'hola' \"mundo\"\n") if token.type is TokenType.STRING]
    assert [token.value for token in tokens] == ["hola", "mundo"]


def test_fstring_produce_un_token():
    tokens = [token for token in tokenize('f"{a} + {b}"\n') if token.type is TokenType.FSTRING]
    assert len(tokens) == 1
    assert tokens[0].value == "{a} + {b}"


def test_cadena_sin_cerrar_al_final_de_la_entrada_es_incompleta():
    """Si el archivo termina dentro de la cadena, falta más entrada."""
    with pytest.raises(IncompleteInput):
        tokenize("x = 'sin cerrar")


def test_cadena_sin_cerrar_con_salto_de_linea_es_error_de_sintaxis():
    """Como en CPython, un salto de línea cierra la cadena: es un error."""
    with pytest.raises(CruthonSyntaxError):
        tokenize("x = 'sin cerrar\n")


def test_sangria_inconsistente():
    with pytest.raises(CruthonSyntaxError):
        tokenize("def f():\n    x = 1\n  y = 2\n")


def test_operadores_de_conjuntos():
    tokens = [token for token in tokenize("a & b | c - d ^ e\n") if token.type is TokenType.OP]
    assert [token.value for token in tokens] == ["&", "|", "-", "^"]
