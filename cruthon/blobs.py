"""Modelo de blobs: la semántica de conjuntos de Cruthon.

Un *blob* es un conjunto con memoria propia. Los blobs se combinan con las
cuatro operaciones que el documento de diseño enumera:

===========  ===================================================================
``a & b``    intersección: memoria nueva compartida por ``a`` y ``b``.
``a | b``    unión: el espacio de ejecución completo de ambos, sin memoria común.
``a - b``    diferencia: el contenido de ``a`` excluyendo lo que ``b`` también tiene.
``a ^ b``    diferencia simétrica: lo que ninguno de los dos comparte con el otro.
===========  ===================================================================

La visibilidad es geométrica: un nombre es visible desde un blob si el área
donde vive está contenida en el área del blob activo. Un blob contenedor ve
todo lo que hay dentro de su área (incluidas las intersecciones que contiene);
dos blobs hermanos no se ven entre sí aunque uno contenga al otro.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional

from .errors import BlobTypeError

#: Operaciones de conjuntos soportadas entre blobs.
SET_OPERATIONS = ("&", "|", "-", "^")


class Ref:
    """Referencia viva a un nombre dentro de un blob.

    ``blob`` es el blob dueño del binding; ``source`` se usa para las vistas
    heredadas (unión, diferencia), donde el valor se resuelve en el blob de
    origen pero no se puede escribir ahí.
    """

    __slots__ = ("blob", "name", "readonly", "source")

    def __init__(
        self,
        blob: "Blob",
        name: str,
        readonly: bool = False,
        source: Optional["Blob"] = None,
    ) -> None:
        self.blob = blob
        self.name = name
        self.readonly = readonly
        self.source = source

    def get(self) -> Any:
        if self.source is not None:
            inner = self.source.lookup_own(self.name)
            return None if inner is None else inner.get()
        blob = self.blob
        if self.name in blob.storage:
            return blob.storage[self.name]
        return blob.refs.get(self.name)

    def set(self, value: Any) -> None:
        if self.source is not None:
            raise BlobTypeError(
                f"'{self.name}' es una vista de solo lectura (operación de conjuntos)"
            )
        self.blob.storage[self.name] = value

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"<Ref {self.blob.name}.{self.name}>"


class InheritedView:
    """Vista de solo lectura sobre el ámbito de otro blob.

    Es lo que usa la unión (``|``), la diferencia (``-``) y la diferencia
    simétrica (``^``): el blob resultante puede leer lo que corresponde a sus
    operandos, pero escribir en él no toca la memoria del operando (no hay
    memoria común).
    """

    __slots__ = ("source", "exclude")

    def __init__(self, source: "Blob", exclude: Optional[FrozenSet[str]] = None) -> None:
        self.source = source
        self.exclude = exclude or frozenset()

    def lookup(self, name: str) -> Optional[Ref]:
        """Resuelve ``name`` entre las declaraciones propias del blob de origen.

        Sólo se miran los miembros declarados en el propio blob (no sus
        áreas hijas): una operación de conjuntos ve el *contenido* de sus
        operandos, no vuelve a entrar en sus sub-áreas (eso evitaría ciclos
        como ``a & b`` dentro de ``a``).
        """
        if name in self.exclude:
            return None
        ref = self.source.lookup_own(name)
        if ref is None:
            return None
        return Ref(ref.blob, name, readonly=True, source=ref.blob)


class Blob:
    """Un blob: conjunto con memoria propia y tabla de pertenencia.

    * ``storage`` — los miembros que viven en el blob (variables y funciones).
    * ``refs`` — referencias a otros blobs (los blobs anidados y las
      intersecciones en las que el blob participa).
    * ``children`` — blobs cuyo ámbito entra en el ámbito de este.
    * ``inherited`` — vistas de solo lectura de los operandos de una
      operación de conjuntos.
    """

    __slots__ = (
        "name",
        "parent",
        "storage",
        "refs",
        "children",
        "inherited",
        "op",
        "operands",
        "decl_order",
    )

    def __init__(
        self,
        name: str = "<blob>",
        parent: Optional["Blob"] = None,
        op: Optional[str] = None,
        operands: Optional[List["Blob"]] = None,
    ) -> None:
        self.name = name
        self.parent = parent
        self.storage: Dict[str, Any] = {}
        self.refs: Dict[str, "Blob"] = {}
        self.children: List["Blob"] = []
        self.inherited: List[InheritedView] = []
        self.op = op
        self.operands: List["Blob"] = list(operands or [])
        self.decl_order: List[str] = []

    # -- construcción ---------------------------------------------------
    def define(self, name: str, value: Any) -> None:
        """Declara un miembro (variable o función) en el blob."""
        if name not in self.storage:
            self.decl_order.append(name)
        self.storage[name] = value

    def add_child(self, child: "Blob") -> None:
        """Añade un blob cuyo ámbito queda dentro del de este."""
        if child not in self.children:
            self.children.append(child)

    def reference(self, name: str, blob: "Blob") -> None:
        """Hace visible un blob por nombre desde este blob."""
        self.refs[name] = blob

    def inherit(self, source: "Blob", exclude: Optional[FrozenSet[str]] = None) -> None:
        """Añade una vista de solo lectura sobre otro blob."""
        self.inherited.append(InheritedView(source, exclude))

    # -- consultas ------------------------------------------------------
    def member_names(self) -> List[str]:
        """Nombres de los miembros propios (variables y funciones)."""
        return [name for name in self.decl_order if name in self.storage]

    def declared_names(self) -> FrozenSet[str]:
        """Todos los nombres que el blob declara, incluidos los blobs anidados."""
        return frozenset(self.storage) | frozenset(self.refs)

    def visible_names(self) -> List[str]:
        """Nombres alcanzables desde este blob (área de pertenencia)."""
        seen: List[str] = []
        for name in self.membership_table():
            if name not in seen:
                seen.append(name)
        return seen

    def membership_table(self) -> Dict[str, List[str]]:
        """Tabla de pertenencia: nombre -> blobs donde vive.

        Es el sustituto cruthoniano del MRO de CPython: en vez de una cadena
        lineal de herencia, cada nombre dice en qué blobs vive.
        """
        table: Dict[str, List[str]] = {}
        visited: set = set()

        def walk(blob: "Blob") -> None:
            if id(blob) in visited:
                return
            visited.add(id(blob))
            for name in blob.storage:
                table.setdefault(name, []).append(blob.name)
            for name in blob.refs:
                table.setdefault(name, []).append(blob.name)
            for child in blob.children:
                walk(child)

        walk(self)
        return table

    # -- resolución de nombres -------------------------------------------
    def lookup_own(self, name: str) -> Optional[Ref]:
        """Busca ``name`` entre las declaraciones propias del blob."""
        if name in self.storage:
            return Ref(self, name)
        if name in self.refs:
            return Ref(self, name)
        return None

    def lookup(self, name: str, _seen: Optional[set] = None) -> Optional[Ref]:
        """Busca ``name`` en el área de este blob.

        Devuelve ``None`` si el nombre no vive dentro del área: eso es un
        error de alcance, no un ``NameError`` de Python.
        """
        if _seen is None:
            _seen = set()
        if id(self) in _seen:
            return None
        _seen.add(id(self))
        ref = self.lookup_own(name)
        if ref is not None:
            return ref
        for child in self.children:
            ref = child.lookup(name, _seen)
            if ref is not None:
                return ref
        for view in self.inherited:
            ref = view.lookup(name)
            if ref is not None:
                return ref
        return None

    def contains(self, name: str) -> bool:
        """¿El nombre vive dentro del área de este blob?"""
        return self.lookup(name) is not None

    def __repr__(self) -> str:
        if self.op:
            return f"<blob {self.name} = {' '.join(op.name for op in self.operands)}>"
        return f"<blob {self.name}>"


# ---------------------------------------------------------------- operaciones
def _require_blobs(a: Any, b: Any, op: str) -> tuple:
    if not isinstance(a, Blob) or not isinstance(b, Blob):
        raise BlobTypeError(
            f"la operación de conjuntos {op!r} solo admite blobs "
            f"(recibidos {type(a).__name__} y {type(b).__name__})"
        )
    return a, b


def intersect(a: Blob, b: Blob, name: Optional[str] = None) -> Blob:
    """Intersección: memoria nueva compartida por ambos blobs.

    El blob resultante contiene solo lo que ambos comparten y crea su propia
    zona de memoria, que ``a`` y ``b`` pueden leer y escribir por igual.
    """
    _require_blobs(a, b, "&")
    result = Blob(name=name or "<interseccion>", op="&", operands=[a, b])
    common = a.declared_names() & b.declared_names()
    if common:
        result.inherit(a, exclude=frozenset(a.declared_names() - common))
    # La zona compartida vive dentro del área de los dos blobs.
    a.add_child(result)
    b.add_child(result)
    if name:
        a.reference(name, result)
        b.reference(name, result)
    return result


def union(a: Blob, b: Blob, name: Optional[str] = None) -> Blob:
    """Unión: el espacio de ejecución de ambos blobs, sin memoria común."""
    _require_blobs(a, b, "|")
    result = Blob(name=name or "<union>", op="|", operands=[a, b])
    result.inherit(a)
    result.inherit(b)
    result.reference(a.name, a)
    result.reference(b.name, b)
    return result


def difference(a: Blob, b: Blob, name: Optional[str] = None) -> Blob:
    """Diferencia: el contenido de ``a`` excluyendo lo que ``b`` también tiene."""
    _require_blobs(a, b, "-")
    result = Blob(name=name or "<diferencia>", op="-", operands=[a, b])
    result.inherit(a, exclude=frozenset(b.declared_names()))
    result.reference(a.name, a)
    return result


def symmetric_difference(a: Blob, b: Blob, name: Optional[str] = None) -> Blob:
    """Diferencia simétrica: lo que ninguno de los dos comparte con el otro."""
    _require_blobs(a, b, "^")
    result = Blob(name=name or "<diferencia_simétrica>", op="^", operands=[a, b])
    a_names = a.declared_names()
    b_names = b.declared_names()
    result.inherit(a, exclude=frozenset(b_names))
    result.inherit(b, exclude=frozenset(a_names))
    result.reference(a.name, a)
    result.reference(b.name, b)
    return result


#: Tabla de las operaciones de conjuntos.
SET_FUNCTIONS = {
    "&": intersect,
    "|": union,
    "-": difference,
    "^": symmetric_difference,
}
