"""Builtin de Python disponibles dentro de cualquier blob.

Cruthon no expone el espacio de nombres completo de Python: expone una lista
curada de funciones y de excepciones que se comportan exactamente igual que
en Python, para que el código portado funcione sin cambios. Todo lo que no
está en esta tabla es invisible para un blob.
"""

from __future__ import annotations

import builtins as python_builtins
from typing import Any, Dict

from .errors import (
    BlobTypeError,
    CruthonError,
    CruthonRuntimeError,
    CruthonSyntaxError,
    NameNotFound,
    ScopeError,
)

#: Funciones de Python que se pueden usar dentro de un blob.
FUNCIONES: Dict[str, Any] = {
    "abs": python_builtins.abs,
    "all": python_builtins.all,
    "any": python_builtins.any,
    "ascii": python_builtins.ascii,
    "bin": python_builtins.bin,
    "bool": python_builtins.bool,
    "bytearray": python_builtins.bytearray,
    "bytes": python_builtins.bytes,
    "callable": python_builtins.callable,
    "chr": python_builtins.chr,
    "classmethod": python_builtins.classmethod,
    "complex": python_builtins.complex,
    "dict": python_builtins.dict,
    "dir": python_builtins.dir,
    "Ellipsis": python_builtins.Ellipsis,
    "divmod": python_builtins.divmod,
    "enumerate": python_builtins.enumerate,
    "filter": python_builtins.filter,
    "float": python_builtins.float,
    "format": python_builtins.format,
    "frozenset": python_builtins.frozenset,
    "getattr": python_builtins.getattr,
    "hasattr": python_builtins.hasattr,
    "hash": python_builtins.hash,
    "hex": python_builtins.hex,
    "id": python_builtins.id,
    "input": python_builtins.input,
    "int": python_builtins.int,
    "isinstance": python_builtins.isinstance,
    "issubclass": python_builtins.issubclass,
    "iter": python_builtins.iter,
    "len": python_builtins.len,
    "list": python_builtins.list,
    "map": python_builtins.map,
    "max": python_builtins.max,
    "memoryview": python_builtins.memoryview,
    "min": python_builtins.min,
    "next": python_builtins.next,
    "NotImplemented": python_builtins.NotImplemented,
    "object": python_builtins.object,
    "open": python_builtins.open,
    "oct": python_builtins.oct,
    "ord": python_builtins.ord,
    "pow": python_builtins.pow,
    "print": python_builtins.print,
    "property": python_builtins.property,
    "range": python_builtins.range,
    "repr": python_builtins.repr,
    "reversed": python_builtins.reversed,
    "round": python_builtins.round,
    "set": python_builtins.set,
    "setattr": python_builtins.setattr,
    "slice": python_builtins.slice,
    "sorted": python_builtins.sorted,
    "staticmethod": python_builtins.staticmethod,
    "str": python_builtins.str,
    "sum": python_builtins.sum,
    "tuple": python_builtins.tuple,
    "type": python_builtins.type,
    "vars": python_builtins.vars,
    "zip": python_builtins.zip,
    "delattr": python_builtins.delattr,
}

#: Builtins que dependen del ámbito donde se usan: los resuelve el
#: intérprete (``cruthon/interpreter.py``), porque ``eval`` y ``exec``
#: necesitan el blob y el marco del punto de llamada, no un ámbito global.
FUNCIONES_CON_AMBITO = ("eval", "exec")

#: Excepciones de Python que se pueden capturar dentro de un blob.
EXCEPCIONES: Dict[str, Any] = {
    nombre: getattr(python_builtins, nombre)
    for nombre in (
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "ConnectionError",
        "Exception",
        "IndentationError",
        "IndexError",
        "KeyError",
        "NameError",
        "OSError",
        "RuntimeError",
        "StopIteration",
        "SyntaxError",
        "TimeoutError",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    )
}

#: La única excepción visible que no hereda de ``Exception``.
#:
#: Un programa que abre un socket o atiende en un bucle necesita poder
#: despedirse cuando llega Ctrl+C. Sin este nombre, la interrupción sale del
#: intérprete como una traza; con él, el programa la captura y cierra lo que
#: tenga abierto. ``except Exception`` no la captura, igual que en Python.
BASE_EXCEPCIONES: Dict[str, Any] = {
    "KeyboardInterrupt": python_builtins.KeyboardInterrupt,
}

#: Errores propios de Cruthon: también se pueden capturar.
ERRORES_CRUTHON: Dict[str, Any] = {
    "CruthonError": CruthonError,
    "CruthonRuntimeError": CruthonRuntimeError,
    "CruthonSyntaxError": CruthonSyntaxError,
    "ScopeError": ScopeError,
    "NameNotFound": NameNotFound,
    "BlobTypeError": BlobTypeError,
}


def isinstance(obj: Any, classinfo: Any) -> bool:  # noqa: A001 - el nombre es el de Python
    """Como ``isinstance`` de Python, pero entiende las clases de Cruthon.

    Una clase de Cruthon no es un tipo de Python, así que el ``isinstance``
    de la biblioteca estándar no puede compararla. Aquí se resuelve mirando
    el MRO de la clase, que es lo mismo que hace Python con las suyas.
    """
    from .classes import CruthonClass, CruthonInstance

    if python_builtins.isinstance(classinfo, CruthonClass):
        if python_builtins.isinstance(obj, CruthonInstance):
            return any(clase is classinfo for clase in obj.clase.mro)
        return False
    if python_builtins.isinstance(obj, CruthonInstance):
        # Una clase que hereda de un tipo de Python (``class M(list):``) es
        # también de ese tipo.
        base = obj.base_obj
        if base is not None:
            return python_builtins.isinstance(base, classinfo)
    if python_builtins.isinstance(classinfo, tuple):
        return any(isinstance(obj, clase) for clase in classinfo)
    return python_builtins.isinstance(obj, classinfo)


def issubclass(cls: Any, classinfo: Any) -> bool:
    """Como ``issubclass`` de Python, pero entiende las clases de Cruthon."""
    from .classes import CruthonClass

    if python_builtins.isinstance(classinfo, CruthonClass):
        if python_builtins.isinstance(cls, CruthonClass):
            return any(clase is classinfo for clase in cls.mro)
        return False
    if python_builtins.isinstance(classinfo, tuple):
        return any(issubclass(cls, clase) for clase in classinfo)
    return python_builtins.issubclass(cls, classinfo)


def make_builtins() -> Dict[str, Any]:
    """Devuelve la tabla de builtin que ve cualquier blob."""
    tabla = dict(FUNCIONES)
    tabla.update(EXCEPCIONES)
    tabla.update(BASE_EXCEPCIONES)
    tabla.update(ERRORES_CRUTHON)
    # ``isinstance`` y ``issubclass`` saben de las clases de Cruthon.
    tabla["isinstance"] = isinstance
    tabla["issubclass"] = issubclass
    return tabla
