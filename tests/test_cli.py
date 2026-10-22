"""Pruebas de la línea de comandos de Cruthon.

Comprueban el ejecutable tal y como lo usaría una persona: ``cruthon
archivo.cruthon`` ejecuta el archivo, ``cruthon -c "..."`` ejecuta un
fragmento, y ``cruthon`` sin argumentos abre la REPL.
"""

from __future__ import annotations

import io
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from cruthon.cli import main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXPECTED = EXAMPLES / "expected"


def _correr(argv):
    """Invoca ``main`` capturando stdout/stderr y devolviendo el código."""
    salida, error = io.StringIO(), io.StringIO()
    with redirect_stdout(salida), redirect_stderr(error):
        status = main(argv)
    return status, salida.getvalue(), error.getvalue()


def test_ejecuta_un_archivo_de_ejemplo():
    archivo = EXAMPLES / "01_carrito.cruthon"
    status, salida, error = _correr([str(archivo)])
    assert status == 0
    assert error == ""
    assert salida == (EXPECTED / "01_carrito.out").read_text(encoding="utf-8")


def test_ejecuta_un_fragmento_con_menos_c():
    status, salida, error = _correr(["-c", "print('hola')"])
    assert status == 0
    assert salida.strip() == "hola"


def test_un_error_de_sintaxis_devuelve_codigo_1():
    status, _, error = _correr(["-c", "blob main"])
    assert status == 1
    assert "File" in error


def test_un_error_de_ejecucion_devuelve_codigo_1():
    status, _, error = _correr(["-c", "no_existe()"])
    assert status == 1
    assert "NameNotFound" in error


def test_un_archivo_inexistente_devuelve_codigo_2():
    status, _, error = _correr(["no_existe.cruthon"])
    assert status == 2
    assert "no_existe.cruthon" in error


def test_la_opcion_de_pertenencia_imprime_la_tabla():
    status, salida, _ = _correr(
        ["-c", "blob main:\n    blob a:\n        x = 1\n", "-p"]
    )
    assert status == 0
    assert "pertenencia" in salida.lower()
    assert "x" in salida


def test_la_primera_linea_del_archivo_se_ejecuta(tmp_path):
    """Un archivo cuyo primer renglón es código no pierde ese renglón.

    La detección de codificación lee las primeras líneas del archivo para
    buscar la declaración ``# -*- coding: ... -*-``; si no se vuelve al
    principio, el programa empieza en la segunda línea y esa sentencia se
    ejecuta como si no existiera.
    """
    archivo = tmp_path / "primera.cruthon"
    archivo.write_text('print("primera")\nprint("segunda")\n', encoding="utf-8")
    status, salida, error = _correr([str(archivo)])
    assert status == 0, error
    assert salida.splitlines() == ["primera", "segunda"]


def test_version():
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0


def test_el_modulo_se_puede_ejecutar_com_python_m():
    """``python -m cruthon`` es la forma documentada de.launch el intérprete."""
    proceso = subprocess.run(
        [sys.executable, "-m", "cruthon", str(EXAMPLES / "03_interseccion.cruthon")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
    )
    assert proceso.returncode == 0, proceso.stderr
    assert proceso.stdout == (
        EXPECTED / "03_interseccion.out"
    ).read_text(encoding="utf-8")


def test_sin_argumentos_abre_la_repl(monkeypatch):
    """Sin archivo se abre la REPL y se lee de la entrada estándar."""
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    status, salida, _ = _correr([])
    assert status == 0


def test_la_opcion_de_pertenencia_muestra_rutas_de_inclusion():
    """La tabla nombra la ruta completa, no sólo el blob: ``main.a.x``."""
    status, salida, _ = _correr(["-c", "blob main:\n    blob a:\n        x = 1\n", "-p"])
    assert status == 0
    assert "rutas de inclusión" in salida
    assert "main.a.x" in salida
