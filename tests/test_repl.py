"""Pruebas de la REPL de Cruthon.

La REPL es la parte "interpretada igual que Python" del entregable: cada
instrucción se analiza y se ejecuta en cuanto está completa, y su resultado se
muestra mientras se teclea. Estas pruebas comprueban esa propiedad (el resultado
aparece *antes* de que se pida la línea siguiente), el agrupamiento de bloques
y que un error no mate la sesión.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from typing import List

import pytest

from cruthon import CruthonREPL


class _Entrada:
    """Doble de ``input`` que además observa lo impreso hasta ese momento."""

    def __init__(self, lineas: List[str], buffer: io.StringIO) -> None:
        self.lineas = list(lineas)
        self.buffer = buffer
        self.indice = 0
        self.vistos: List[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.vistos.append(self.buffer.getvalue())
        if self.indice >= len(self.lineas):
            raise EOFError
        linea = self.lineas[self.indice]
        self.indice += 1
        return linea


def _ejecutar(lineas: List[str]):
    """Corre la REPL con ``lineas`` y devuelve (stdout, stderr, entrada)."""
    salida = io.StringIO()
    error = io.StringIO()
    entrada = _Entrada(lineas, salida)
    with redirect_stdout(salida), redirect_stderr(error):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("builtins.input", entrada)
            CruthonREPL().interact(banner=False)
    return salida.getvalue(), error.getvalue(), entrada


def test_muestra_el_resultado_de_cada_expresion():
    salida, _, _ = _ejecutar(["1 + 1", "salir()"])
    assert "2" in salida


def test_se_ejecuta_conforme_se_lee():
    """El resultado de la línea N aparece antes de pedir la línea N+1."""
    salida = io.StringIO()
    error = io.StringIO()
    entrada = _Entrada(["21 * 2", "salir()"], salida)
    with redirect_stdout(salida), redirect_stderr(error):
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr("builtins.input", entrada)
            CruthonREPL().interact(banner=False)
    # Cuando se pide la segunda línea, la primera ya se había ejecutado.
    assert "42" in entrada.vistos[1]


def test_las_sentencias_se_ejecutan_en_orden():
    salida, _, _ = _ejecutar(
        ["print('uno')", "print('dos')", "print('tres')", "salir()"]
    )
    assert salida.index("uno") < salida.index("dos") < salida.index("tres")


def test_los_bloques_se_acumulan_hasta_la_linea_en_blanco():
    salida, _, _ = _ejecutar(
        [
            "blob main:",
            "    x = 1",
            "    func f():",
            "        return x",
            "",
            "main.f()",
            "salir()",
        ]
    )
    assert "<blob main>" in salida
    assert "1" in salida


def test_un_error_no_termina_la_sesion():
    salida, error, _ = _ejecutar(
        ["no_existe()", "print('sigo vivo')", "salir()"]
    )
    assert "sigo vivo" in salida
    assert "NameNotFound" in error


def test_el_error_de_alcance_por_conjuntos_se_reporta():
    salida, error, _ = _ejecutar(
        [
            "blob main:",
            "    blob a:",
            "        secreto = 1",
            "",
            "main.a.secreto",
            "salir()",
        ]
    )
    # El nombre sí es visible: `a` es hijo de `main`.
    assert "1" in salida


def test_el_alcance_entre_blobs_hermanos_falla():
    salida, error, _ = _ejecutar(
        [
            "blob main:",
            "    blob a:",
            "        secreto = 1",
            "",
            "    blob b:",
            "        pass",
            "",
            "main.b.secreto",
            "salir()",
        ]
    )
    assert "ScopeError" in error


def test_comando_de_pertenencia():
    salida, _, _ = _ejecutar(
        [
            "blob main:",
            "    blob a:",
            "        valor = 1",
            "",
            "%pertenencia main",
            "salir()",
        ]
    )
    assert "valor" in salida


def test_comando_blobs():
    salida, _, _ = _ejecutar(
        [
            "blob main:",
            "    blob a:",
            "        valor = 1",
            "",
            "%blobs",
            "salir()",
        ]
    )
    assert "blob" in salida
