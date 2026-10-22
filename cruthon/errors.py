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


class CruthonSyntaxError(CruthonError, SyntaxError):
    """Error de sintaxis o de sangrado.

    Hereda también de ``SyntaxError`` de Python: un ``except SyntaxError``
    escrito en Python tiene que seguir capturando aquí, como hace con los
    errores de sintaxis de CPython.
    """

    def __init__(
        self,
        message: str,
        filename: str = "<cruthon>",
        lineno: int = 0,
        colno: int = 0,
        text: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        # ``SyntaxError.__init__`` reinicia estos atributos al construirse,
        # así que se rellenan después.
        self.message = message
        self.msg = message
        self.filename = filename
        self.lineno = lineno
        self.colno = colno
        self.offset = colno
        self.text = text

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


class CruthonTypeError(CruthonRuntimeError, TypeError):
    """Un ``TypeError`` de Python, que además es un error de Cruthon.

    Se usa para los errores de llamada (argumentos que no cuadran) y para
    los tipos que no admiten una operación, de modo que un
    ``except TypeError`` escrito en Python siga capturando, como hace en
    CPython.
    """


class ScopeError(CruthonRuntimeError):
    """El nombre pedido no vive dentro del área del blob activo.

    Equivale al ``AttributeError`` de CPython, pero la causa es geométrica:
    el nombre existe en otro blob que no intersecta con el actual.
    """


class NameNotFound(ScopeError, NameError):
    """El nombre no existe en ninguna intersección alcanzable.

    Hereda también del ``NameError`` de Python: en CPython, un nombre que
    no está definido lanza ``NameError``, así que un ``except NameError``
    escrito en Python tiene que seguir capturando aquí. ``ScopeError``
    sigue siendo su ancestro: ``except ScopeError`` también lo captura.
    """


class AmbiguousName(ScopeError):
    """El nombre vive en más de una región a la misma distancia.

    No se elige una por orden de declaración: la lectura desnuda es ambigua y
    el mensaje lista las rutas de inclusión que compiten, escritas desde el
    blob donde se lee (``A.y`` y ``B.y`` dentro de ``main``; ``main.A.y`` y
    ``main.B.y`` si la lectura es desde la raíz). Cuando las distancias son
    distintas, gana la más cercana y este error no se lanza.
    """


class DuplicateName(CruthonSyntaxError):
    """Dos regiones del mismo blob quieren el mismo nombre.

    Hereda de ``CruthonSyntaxError`` para llevar archivo y línea: el error se
    señala en la declaración duplicada, no en el uso que falla después.
    """


class BlobTypeError(CruthonRuntimeError):
    """Una operación de conjuntos recibió algo que no es un blob."""
