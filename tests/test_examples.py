"""Pruebas de los ejemplos del documento de diseño.

Cada ejemplo del PDF vive en ``examples/*.cruthon`` y su salida esperada en
``examples/expected/*.out``. Estas pruebas comprueban dos cosas:

1. que el ejemplo se ejecuta sin errores;
2. que la salida coincide exactamente con la guardada en el repositorio.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from cruthon import Interpreter, parse

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXPECTED = EXAMPLES / "expected"

ARCHIVOS = sorted(EXAMPLES.glob("*.cruthon"))


def test_hay_ejemplos_para_cada_operacion():
    nombres = {archivo.name for archivo in ARCHIVOS}
    assert {
        "01_carrito.cruthon",
        "02_calculadora.cruthon",
        "03_interseccion.cruthon",
        "04_union.cruthon",
        "05_diferencia.cruthon",
        "06_diferencia_simetrica.cruthon",
        "07_combinado.cruthon",
    } <= nombres


@pytest.mark.parametrize("archivo", ARCHIVOS, ids=lambda a: a.stem)
def test_el_ejemplo_se_ejecuta_sin_errores(archivo: Path):
    salida = io.StringIO()
    error = io.StringIO()
    interprete = Interpreter(str(archivo))
    with redirect_stdout(salida), redirect_stderr(error):
        interprete.run(parse(archivo.read_text(encoding="utf-8"), str(archivo)))
    assert error.getvalue() == ""


@pytest.mark.parametrize("archivo", ARCHIVOS, ids=lambda a: a.stem)
def test_la_salida_coincide_con_la_guardada(archivo: Path):
    esperado = EXPECTED / (archivo.stem + ".out")
    assert esperado.exists(), f"falta el resultado guardado {esperado.name}"
    salida = io.StringIO()
    interprete = Interpreter(str(archivo))
    with redirect_stdout(salida):
        interprete.run(parse(archivo.read_text(encoding="utf-8"), str(archivo)))
    assert salida.getvalue() == esperado.read_text(encoding="utf-8")


def test_el_ejemplo_del_carrito_conserva_la_regla_del_pdf():
    """`frontend` no ve `db`; `backend` no ve `dibujar_pagina`."""
    from cruthon.errors import ScopeError

    interprete = Interpreter("carrito")
    codigo = (EXAMPLES / "01_carrito.cruthon").read_text(encoding="utf-8")
    interprete.run(parse(codigo, "carrito"))
    main = interprete.globals.lookup("main").get()
    frontend = main.lookup("frontend").get()
    backend = main.lookup("backend").get()
    with pytest.raises(ScopeError):
        interprete.get_attribute(frontend, "db")
    with pytest.raises(ScopeError):
        interprete.get_attribute(backend, "dibujar_pagina")
