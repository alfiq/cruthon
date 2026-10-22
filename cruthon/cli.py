"""Punto de entrada de la línea de comandos de Cruthon.

Uso::

    cruthon                    # abre la REPL
    cruthon ejemplo.cruthon    # ejecuta un archivo
    cruthon -c "blob main: ..."  # ejecuta un fragmento
"""

from __future__ import annotations

import argparse
import sys
import tokenize
from typing import List, Optional

from . import __version__
from .errors import CruthonError, CruthonSyntaxError
from .interpreter import Interpreter
from .parser import parse
from .repl import CruthonREPL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cruthon",
        description=(
            "Cruthon: un lenguaje de programación basado en teoría de conjuntos. "
            "Se interpreta como Python: se ejecuta conforme se lee."
        ),
    )
    parser.add_argument(
        "archivo",
        nargs="?",
        help="archivo de Cruthon a ejecutar (si se omite, se abre la REPL)",
    )
    parser.add_argument(
        "-c",
        "--comando",
        dest="comando",
        help="ejecuta el fragmento de código dado como argumento",
    )
    parser.add_argument(
        "-i",
        "--interactivo",
        action="store_true",
        help="abre la REPL después de ejecutar el archivo",
    )
    parser.add_argument(
        "-p",
        "--pertenencia",
        action="store_true",
        help="imprime la tabla de pertenencia de conjuntos al terminar",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"cruthon {__version__}",
    )
    return parser


def run_source(source: str, filename: str, show_membership: bool = False) -> int:
    """Analiza y ejecuta un programa de Cruthon."""
    try:
        module = parse(source, filename)
    except CruthonSyntaxError as error:
        print(f'  File "{filename}", line {error.lineno}', file=sys.stderr)
        print(f"    {error.message}", file=sys.stderr)
        return 1
    interpreter = Interpreter(filename)
    try:
        interpreter.run(module)
    except CruthonError as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - se muestran como Python
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    if show_membership:
        _print_membership(interpreter)
    return 0


def _print_membership(interpreter: Interpreter) -> None:
    table = interpreter.globals.membership_table()
    print("\nTabla de pertenencia (nombre -> blobs donde vive):")
    if not table:
        print("  (vacío)")
    for name in sorted(table):
        print(f"  {name}: {', '.join(table[name])}")


def _leer(ruta: str) -> str:
    """Lee un archivo respetando su declaración de codificación, como Python.

    Se usa el mismo detector que CPython (PEP 263): una cookie
    ``# -*- coding: latin-1 -*-`` en las dos primeras líneas o una marca BOM.
    Sin declaración, el archivo se lee como UTF-8.
    """
    with open(ruta, "rb") as handle:
        codificacion, _ = tokenize.detect_encoding(handle.readline)
        return handle.read().decode(codificacion)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.comando is not None:
        status = run_source(args.comando, "<comando>", args.pertenencia)
        if args.interactivo:
            CruthonREPL().interact()
        return status

    if args.archivo:
        try:
            source = _leer(args.archivo)
        except OSError as error:
            print(f"cruthon: no se pudo abrir {args.archivo!r}: {error}", file=sys.stderr)
            return 2
        except UnicodeDecodeError as error:
            print(
                f"cruthon: no se pudo leer {args.archivo!r}: el archivo no está "
                f"codificado en UTF-8 ({error.reason}). Añade una declaración "
                f"'# -*- coding: cp1252 -*-' o conviértelo a UTF-8.",
                file=sys.stderr,
            )
            return 1
        except SyntaxError as error:
            print(f"cruthon: {args.archivo!r}: {error}", file=sys.stderr)
            return 1
        status = run_source(source, args.archivo, args.pertenencia)
        if args.interactivo:
            CruthonREPL().interact()
        return status

    return CruthonREPL().interact()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
