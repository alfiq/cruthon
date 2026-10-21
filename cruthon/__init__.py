"""Cruthon: un lenguaje de programación basado en teoría de conjuntos.

Cruthon se interpreta como Python: el código se ejecuta conforme se lee, tanto
en un archivo como en la REPL interactiva.

Uso mínimo::

    from cruthon import Interpreter, parse

    module = parse("blob main:\\n    x = 1\\n")
    Interpreter().run(module)
"""

from __future__ import annotations

__version__ = "0.1.0"

from .blobs import Blob, difference, intersect, symmetric_difference, union
from .errors import (
    BlobTypeError,
    CruthonError,
    CruthonRuntimeError,
    CruthonSyntaxError,
    IncompleteInput,
    NameNotFound,
    ScopeError,
)
from .interpreter import Interpreter
from .lexer import tokenize
from .parser import parse
from .repl import CruthonREPL

__all__ = [
    "__version__",
    "Blob",
    "BlobTypeError",
    "CruthonError",
    "CruthonREPL",
    "CruthonRuntimeError",
    "CruthonSyntaxError",
    "IncompleteInput",
    "Interpreter",
    "NameNotFound",
    "ScopeError",
    "difference",
    "intersect",
    "parse",
    "symmetric_difference",
    "tokenize",
    "union",
]
