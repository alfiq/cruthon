"""Consola interactiva de Cruthon (REPL).

Se comporta como la consola de Python: cada instrucción se analiza y ejecuta
en cuanto está completa, y el resultado se muestra mientras se teclea. Como en
``code.InteractiveConsole``, los bloques (un ``blob``, una ``func``) se acumulan
hasta que el usuario escribe una línea en blanco o una línea sin sangrar, y
una expresión abierta (un paréntesis sin cerrar) sigue esperando líneas, como
pide ``codeop.compile_command`` en CPython.
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional, Tuple

from . import nodes as N
from .errors import CruthonSyntaxError, IncompleteInput
from .interpreter import Interpreter
from .parser import parse

BANNER = """Cruthon {version} — lenguaje basado en teoría de conjuntos.
Interpretado como Python: cada instrucción se ejecuta conforme se lee.
Escribe 'salir()' para terminar, '%blobs' para ver los blobs vivos,
'%pertenencia [blob]' para ver la tabla de pertenencia de conjuntos."""


class CruthonREPL:
    """Bucle de lectura, análisis y ejecución."""

    #: Sentencias cuyo cuerpo puede seguir creciendo: la REPL espera a la
    #: línea en blanco antes de ejecutarlas, como hace la consola de Python.
    _BLOQUES = (N.BlobDecl, N.FuncDef, N.If, N.While, N.For, N.Try)

    def __init__(self, interpreter: Optional[Interpreter] = None, filename: str = "<stdin>") -> None:
        self.interpreter = interpreter or Interpreter(filename)
        self.filename = filename
        self.buffer: List[str] = []

    # -- bucle principal -------------------------------------------------
    def interact(self, banner: bool = True) -> int:
        """Bucle principal de la REPL. Devuelve el código de salida."""
        if banner:
            print(BANNER.format(version=_version()))
        while True:
            try:
                prompt = "... " if self.buffer else ">>> "
                line = input(prompt)
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                self.buffer = []
                continue
            if not self.buffer and line.strip() in ("salir()", "exit()", "quit()"):
                break
            if not self.buffer and line.strip().startswith("%"):
                self.run_command(line.strip())
                continue
            if self.buffer and line.strip() and not line.startswith((" ", "\t")):
                # Una línea sin sangrar cierra el bloque pendiente: el bloque
                # se ejecuta y la línea nueva empieza otra instrucción. Pero
                # si lo pendiente es una expresión incompleta (un paréntesis
                # abierto), la línea la continúa en vez de cerrarlo.
                if not self._incompleto():
                    self.flush()
            self.buffer.append(line)
            if not line.strip() and self.buffer:
                self.flush()
                continue
            self.try_flush()
        if self.buffer:
            self.flush()
        return 0

    # -- análisis y ejecución --------------------------------------------
    def _source(self) -> str:
        """El texto pendiente, con un salto de línea al final de cada línea."""
        return "".join(
            line if line.endswith("\n") else line + "\n" for line in self.buffer
        )

    def _analiza(self) -> Tuple[str, Optional[N.Module]]:
        """Analiza el búfer.

        Devuelve ``("incompleto", None)`` si falta más entrada, ``("error",
        None)`` si la entrada es inválida (el error ya se ha reportado) y
        ``("ok", modulo)`` si se pudo analizar.
        """
        try:
            module = parse(self._source(), self.filename)
            # En la REPL cada instrucción es su propio módulo: no hay
            # docstring de módulo, como en la consola de Python.
            module.doc = None
            return ("ok", module)
        except IncompleteInput:
            return ("incompleto", None)
        except CruthonSyntaxError as error:
            self.report(error)
            return ("error", None)

    def _incompleto(self) -> bool:
        """¿Falta más entrada para poder analizar lo que hay en el búfer?"""
        estado, _ = self._analiza()
        return estado == "incompleto"

    def _bloque_pendiente(self, module: N.Module) -> bool:
        """¿La última sentencia es un bloque que todavía puede crecer?"""
        if not module.body:
            return False
        return isinstance(module.body[-1], self._BLOQUES)

    def try_flush(self) -> None:
        """Intenta ejecutar el búfer actual; pide más líneas si está incompleto."""
        estado, module = self._analiza()
        if estado == "incompleto":
            return
        if estado == "error":
            self.buffer = []
            return
        if module is not None and self._bloque_pendiente(module):
            # Puede que el bloque siga creciendo: esperamos a la línea en blanco.
            return
        self.buffer = []
        self.execute(module)

    def flush(self) -> None:
        """Ejecuta el búfer actual aunque esté dentro de un bloque indentado."""
        estado, module = self._analiza()
        if estado == "incompleto":
            self.report(IncompleteInput("entrada incompleta", self.filename))
            self.buffer = []
            return
        if estado == "error":
            self.buffer = []
            return
        self.buffer = []
        if module is not None:
            self.execute(module)

    def execute(self, module: N.Module) -> None:
        try:
            value = self.interpreter.run(module)
        except KeyboardInterrupt:
            # Como en la consola de Python: se interrumpe la instrucción, no
            # la sesión.
            print("KeyboardInterrupt", file=sys.stderr)
            return
        except Exception as error:  # noqa: BLE001 - la REPL no debe morir
            self.report_runtime(error)
            return
        self.echo(module, value)

    def echo(self, module: N.Module, value: Any) -> None:
        """Muestra el resultado de la instrucción, como hace la consola de Python."""
        last = module.body[-1] if module.body else None
        if isinstance(last, N.ExprStmt):
            if value is not None:
                print(repr(value))
        elif isinstance(last, N.BlobDecl):
            print(f"<blob {last.name}>")
        elif isinstance(last, N.FuncDef):
            print(f"<func {last.name}>")

    # -- utilidades -------------------------------------------------------
    def report(self, error: CruthonSyntaxError) -> None:
        print(f'  File "{self.filename}", line {error.lineno}', file=sys.stderr)
        print(f"SyntaxError: {error.message}", file=sys.stderr)

    def report_runtime(self, error: Exception) -> None:
        name = type(error).__name__
        if isinstance(error, SyntaxError):
            self.report(error)  # type: ignore[arg-type]
            return
        print(f"{name}: {error}", file=sys.stderr)

    def run_command(self, line: str) -> None:
        """Comandos de diagnóstico de la REPL (no forman parte del lenguaje)."""
        parts = line.split()
        command = parts[0]
        if command == "%blobs":
            for blob in self.interpreter.globals.children:
                print(f"  {blob!r}")
            return
        if command == "%pertenencia":
            target = self.interpreter.globals
            if len(parts) > 1:
                ref = target.lookup(parts[1])
                if ref is None:
                    print(f"no existe el blob {parts[1]!r}", file=sys.stderr)
                    return
                target = ref.get()
            table = target.membership_table()
            if not table:
                print("  (vacío)")
            for name in sorted(table):
                print(f"  {name}: {', '.join(table[name])}")
            return
        print(f"comando desconocido: {command!r}", file=sys.stderr)


def _version() -> str:
    from . import __version__

    return __version__
