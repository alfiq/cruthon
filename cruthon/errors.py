"""Errores de Cruthon.

Siguen la jerarquía de CPython (``SyntaxError`` / ``RuntimeError``) para que el
interprete se sienta familiar, pero añaden los errores propios del lenguaje
basado en conjuntos: cuando un nombre no vive dentro del área del blob activo
se lanza un ``ScopeError``, el equivalente cruthoniano del ``AttributeError``
de CPython pero por razón de conjuntos y no de jerarquía.
"""

from __future__ import annotations

from typing import Optional


class CruthonError(Exception):
    """Base de todos los errores de Cruthon."""


class CruthonSyntaxError(CruthonError):
    """Error de sintaxis o de sangrado."""

    def __init__(
        self,
        message: str,
        filename: str = "<cruthon>",
        lineno: int = 0,
        colno: int = 0,
        text: Optional[str] = None,
    ) -> None:
        self.message = message
        self.filename = filename
        self.lineno = lineno
        self.colno = colno
        self.text = text
        super().__init__(self._format())

    def _format(self) -> str:
        head = f'File "{self.filename}", line {self.lineno}'
        if self.colno:
            head += f", column {self.colno}"
        return f"{head}\n    {self.message}"

    def __str__(self) -> str:  # pragma: no cover - solo formateo
        return self._format()


class IncompleteInput(CruthonSyntaxError):
    """La entrada está incompleta (bloque abierto, cadena sin cerrar...).

    La REPL usa esta excepción para saber que debe pedir otra línea, igual que
    ``codeop.compile_command`` en CPython.
    """


class CruthonRuntimeError(CruthonError):
    """Error en tiempo de ejecución."""


class ScopeError(CruthonRuntimeError):
    """El nombre pedido no vive dentro del área del blob activo.

    Equivale al ``AttributeError`` de CPython, pero la causa es geométrica:
    el nombre existe en otro blob que no intersecta con el actual.
    """


class NameNotFound(ScopeError):
    """El nombre no existe en ninguna intersección alcanzable."""


class BlobTypeError(CruthonRuntimeError):
    """Una operación de conjuntos recibió algo que no es un blob."""
