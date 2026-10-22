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

La memoria se modela con *celdas*: cada nombre vive en una celda y una celda
puede ser compartida por varios blobs. Eso es exactamente lo que pide el
documento para la intersección: "un blob nuevo con memoria propia, compartida
por los blobs que lo originaron (lectura y escritura por ambos)". Cuando dos
blobs declaran el mismo nombre y se intersectan, ambos operandos y la
intersección pasan a compartir la misma celda: escribir por cualquiera de los
tres se ve en los otros dos.

La *tabla de pertenencia* (nombre -> rutas de inclusión) y la resolución de
nombres (``Blob.lookup``) comparten el mismo recorrido por distancias: la
tabla no es decorativa, es la lectura del modelo.

Un nombre es único dentro de su blob y una región es única por su ruta de
inclusión: ``main.A.API`` y ``main.B.API`` son regiones distintas. Desde
``main``, leer ``API`` a secas es ambiguo, no una carrera que gane la primera
declarada.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Iterator, List, Optional

from .errors import AmbiguousName, BlobTypeError, NameNotFound

#: Operaciones de conjuntos soportadas entre blobs.
SET_OPERATIONS = ("&", "|", "-", "^")


class Cell:
    """Celda de memoria: el valor que vive bajo un nombre.

    ``shared`` marca las celdas que son memoria común de una intersección.
    Una celda compartida la escriben todos los blobs que la poseen.
    """

    __slots__ = ("value", "shared")

    def __init__(self, value: Any = None, shared: bool = False) -> None:
        self.value = value
        self.shared = shared

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        estado = " compartida" if self.shared else ""
        return f"<Cell {self.value!r}{estado}>"


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
        cell = blob.storage.get(self.name)
        if cell is not None:
            return cell.value
        if self.name in blob.refs:
            # El nombre es una referencia a otro blob (un sub-blob o una
            # intersección), no un valor.
            return blob.refs[self.name]
        return None

    def set(self, value: Any) -> None:
        if self.source is not None:
            raise BlobTypeError(
                f"'{self.name}' es una vista de solo lectura (operación de conjuntos)"
            )
        cell = self.blob.storage.get(self.name)
        if cell is None:
            raise NameNotFound(f"nombre no definido: {self.name!r}")
        # Si la celda es compartida, el cambio lo ven todos los blobs que la
        # poseen: ése es el sentido de "memoria compartida" del documento.
        cell.value = value

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"<Ref {self.blob.name}.{self.name}>"


class Area:
    """Zona de nombres de la que se resuelve un nombre.

    La implementan los propios blobs (sus miembros) y las vistas heredadas
    (los operandos de una unión o una diferencia, en modo sólo lectura).
    """

    def resolve(self, name: str) -> Optional[Ref]:
        raise NotImplementedError

    def declared_names(self) -> FrozenSet[str]:
        raise NotImplementedError

    @property
    def owner(self) -> str:
        raise NotImplementedError


class InheritedView(Area):
    """Vista de sólo lectura sobre el ámbito de otro blob.

    Es lo que usa la unión (``|``), la diferencia (``-``) y la diferencia
    simétrica (``^``): el blob resultante puede leer lo que corresponde a sus
    operandos, pero escribir en él no toca la memoria del operando (no hay
    memoria común).
    """

    __slots__ = ("source", "exclude")

    def __init__(self, source: "Blob", exclude: Optional[FrozenSet[str]] = None) -> None:
        self.source = source
        self.exclude = exclude or frozenset()

    def resolve(self, name: str) -> Optional[Ref]:
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

    def declared_names(self) -> FrozenSet[str]:
        return self.source.declared_names() - self.exclude

    @property
    def owner(self) -> str:
        return self.source.name


class Blob(Area):
    """Un blob: conjunto con memoria propia y tabla de pertenencia.

    * ``storage`` — las celdas de los miembros que viven en el blob
      (variables y funciones).
    * ``refs`` — referencias a otros blobs (los blobs anidados y las
      intersecciones en las que el blob participa).
    * ``children`` — blobs cuyo ámbito entra en el ámbito de este.
    * ``inherited`` — vistas de sólo lectura de los operandos de una
      operación de conjuntos.
    """

    __slots__ = (
        "name",
        "storage",
        "refs",
        "children",
        "inherited",
        "op",
        "operands",
    )

    def __init__(
        self,
        name: str = "<blob>",
        op: Optional[str] = None,
        operands: Optional[List["Blob"]] = None,
    ) -> None:
        self.name = name
        self.storage: Dict[str, Cell] = {}
        self.refs: Dict[str, "Blob"] = {}
        self.children: List["Blob"] = []
        self.inherited: List[InheritedView] = []
        self.op = op
        self.operands: List["Blob"] = list(operands or [])

    # -- construcción ---------------------------------------------------
    def define(self, name: str, value: Any) -> None:
        """Declara un miembro (variable o función) en el blob.

        Si el nombre ya vive en una celda compartida (una intersección), la
        escritura se propaga a los blobs que la comparten: dentro de la zona
        común, todos leen y escriben lo mismo.
        """
        cell = self.storage.get(name)
        if cell is not None and cell.shared:
            cell.value = value
            return
        self.storage[name] = Cell(value)

    def add_child(self, child: "Blob") -> None:
        """Añade un blob cuyo ámbito queda dentro del de este."""
        if child not in self.children:
            self.children.append(child)

    def reference(self, name: str, blob: "Blob") -> None:
        """Hace visible un blob por nombre desde este blob."""
        self.refs[name] = blob

    def inherit(self, source: "Blob", exclude: Optional[FrozenSet[str]] = None) -> None:
        """Añade una vista de sólo lectura sobre otro blob."""
        self.inherited.append(InheritedView(source, exclude))

    # -- consultas ------------------------------------------------------
    def declared_names(self) -> FrozenSet[str]:
        """Todos los nombres que el blob declara, incluidos los blobs anidados."""
        return frozenset(self.storage) | frozenset(self.refs)

    @property
    def owner(self) -> str:
        return self.name

    def membership_table(self) -> Dict[str, List[str]]:
        """Tabla de pertenencia: nombre -> nombres de los blobs donde vive.

        Versión corta de ``inclusion_table``, que escribe la ruta completa
        (``main.A.y``) y es la que conviene para diagnosticar ambigüedades.
        """
        tabla: Dict[str, List[str]] = {}
        vistos: set = set()
        for area in self.areas():
            if id(area) in vistos:
                continue
            vistos.add(id(area))
            for name in area.declared_names():
                nombres = tabla.setdefault(name, [])
                if area.owner not in nombres:
                    nombres.append(area.owner)
        return tabla

    def inclusion_table(self) -> Dict[str, List[str]]:
        """Tabla de pertenencia: nombre -> rutas de inclusión.

        Las rutas se escriben desde este blob, sin nombrarlo: llamada sobre la
        raíz de un programa con ``blob main: blob A: y = 1``, la ruta de ``y``
        es ``main.A.y``. Un nombre que vive en dos áreas aparece con las dos
        rutas, que es justo lo que la resolución considera ambiguo.
        """
        tabla: Dict[str, List[str]] = {}
        nivel: List[tuple] = [(self, "")]
        vistos: set = set()
        while nivel:
            siguiente: List[tuple] = []
            for area, prefijo in nivel:
                if id(area) in vistos:
                    continue
                vistos.add(id(area))
                for nombre in area.declared_names():
                    ruta = f"{prefijo}.{nombre}" if prefijo else nombre
                    rutas = tabla.setdefault(nombre, [])
                    if ruta not in rutas:
                        rutas.append(ruta)
                siguiente.extend(_inclusiones(area, prefijo))
            nivel = siguiente
        return tabla

    # -- resolución de nombres -------------------------------------------
    def areas(self, _seen: Optional[set] = None) -> Iterator[Area]:
        """Áreas del blob en orden de resolución.

        Primero lo que el blob declara, después las áreas de sus sub-blobs
        (sus intersecciones y sus subconjuntos) y por último las vistas
        heredadas de las operaciones de conjuntos.
        """
        if _seen is None:
            _seen = set()
        if id(self) in _seen:
            return
        _seen.add(id(self))
        yield self
        for child in self.children:
            yield from child.areas(_seen)
        for view in self.inherited:
            yield view

    def resolve(self, name: str) -> Optional[Ref]:
        """Resuelve ``name`` entre las declaraciones propias del blob."""
        if name in self.storage:
            return Ref(self, name)
        if name in self.refs:
            return Ref(self, name)
        return None

    def lookup_own(self, name: str) -> Optional[Ref]:
        """Busca ``name`` entre las declaraciones propias del blob."""
        return self.resolve(name)

    def lookup(self, name: str) -> Optional[Ref]:
        """Busca ``name`` en el área de este blob, por distancia.

        Gana la declaración más cercana. Si a esa distancia hay más de un
        enlace --dos celdas distintas, o dos regiones distintas-- el nombre es
        ambiguo y se lanza ``AmbiguousName`` con las rutas de inclusión que
        compiten. Dos rutas que llegan a la misma celda son el mismo nombre:
        la intersección comparte la celda con sus operandos y eso no es
        ambigüedad, es memoria compartida.

        Devuelve ``None`` si el nombre no vive dentro del área: eso es un
        error de alcance, no un ``NameError`` de Python.
        """
        candidatos = self._candidatos(name)
        if not candidatos:
            return None
        enlaces: Dict[tuple, tuple] = {}
        for ruta, ref in candidatos:
            enlaces.setdefault(_identidad(ref), (ruta, ref))
        if len(enlaces) > 1:
            rutas = sorted(ruta for ruta, _ in enlaces.values())
            # Las rutas se escriben desde este blob, que es donde se lee: son
            # exactamente las calificaciones que sirven para desambiguar.
            raise AmbiguousName(
                f"el nombre {name!r} vive en más de un área a la misma "
                f"distancia: {' y '.join(rutas)}; escribe la ruta de inclusión"
            )
        return next(iter(enlaces.values()))[1]

    def _candidatos(self, name: str) -> List[tuple]:
        """``(ruta, ref)`` de las declaraciones más cercanas de ``name``.

        Recorre por niveles (primero el blob, después sus subconjuntos, y así)
        y para en el primer nivel que declara el nombre: las distancias
        mayores ya no pueden ganar. Las vistas de una operación de conjuntos
        cuentan como una inclusión más, porque el operando es un subconjunto
        del resultado.
        """
        nivel: List[tuple] = [(self, "")]
        vistos: set = set()
        while nivel:
            encontrados: List[tuple] = []
            for area, prefijo in nivel:
                if id(area) in vistos:
                    continue
                vistos.add(id(area))
                ref = area.resolve(name)
                if ref is not None:
                    ruta = f"{prefijo}.{name}" if prefijo else name
                    encontrados.append((ruta, ref))
            if encontrados:
                return encontrados
            siguiente: List[tuple] = []
            for area, prefijo in nivel:
                siguiente.extend(_inclusiones(area, prefijo))
            nivel = siguiente
        return []

    def __repr__(self) -> str:
        if self.op:
            return f"<blob {self.name} = {' '.join(op.name for op in self.operands)}>"
        return f"<blob {self.name}>"


# ------------------------------------------------------------- inclusiones
def _inclusiones(area: Area, prefijo: str) -> List[tuple]:
    """Las áreas que se alcanzan con una inclusión más.

    Los subconjuntos de un blob y las vistas de sus operaciones de conjuntos
    (unión, diferencia, diferencia simétrica) cuentan igual: cada paso es una
    inclusión, y la ruta se escribe con el nombre del área incluida.
    """
    vecinos: List[tuple] = []
    if isinstance(area, Blob):
        for child in area.children:
            ruta = f"{prefijo}.{child.name}" if prefijo else child.name
            vecinos.append((child, ruta))
        for view in area.inherited:
            ruta = f"{prefijo}.{view.owner}" if prefijo else view.owner
            vecinos.append((view, ruta))
    return vecinos


def _identidad(ref: Ref) -> tuple:
    """Identidad del enlace, para saber si dos rutas son el mismo nombre.

    Un valor es el mismo si es la misma celda (la intersección la comparte con
    sus operandos). Una región es la misma si es el mismo blob referenciado:
    ``main.A.API`` y ``main.B.API`` son regiones distintas aunque se llamen
    igual, y por eso su lectura desnuda es ambigua.
    """
    blob = ref.blob
    celda = blob.storage.get(ref.name)
    if celda is not None:
        return ("celda", id(celda))
    return ("blob", id(blob.refs.get(ref.name, blob)))


# ---------------------------------------------------------------- operaciones
def _require_blobs(a: Any, b: Any, op: str) -> tuple:
    if not isinstance(a, Blob) or not isinstance(b, Blob):
        raise BlobTypeError(
            f"la operación de conjuntos {op!r} solo admite blobs "
            f"(recibidos {type(a).__name__} y {type(b).__name__})"
        )
    return a, b


def _celda_compartida(a: Blob, b: Blob, nombre: str) -> Cell:
    """Devuelve la celda que ``a`` y ``b`` van a compartir para ``nombre``.

    Si alguno de los operandos ya tiene una celda compartida (por otra
    intersección) se reutiliza: así ``A & B`` y ``B & C`` comparten la misma
    memoria a través de ``B``, que actúa de intermediario. Si no, se crea una
    celda nueva con el valor del primer operando.
    """
    celda_a = a.storage.get(nombre)
    celda_b = b.storage.get(nombre)
    for celda in (celda_a, celda_b):
        if celda is not None and celda.shared:
            return celda
    if celda_a is not None:
        valor = celda_a.value
    elif celda_b is not None:
        valor = celda_b.value
    else:  # pragma: no cover - defensivo
        valor = None
    return Cell(valor, shared=True)


def intersect(a: Blob, b: Blob, name: Optional[str] = None) -> Blob:
    """Intersección: memoria nueva compartida por ambos blobs.

    El blob resultante contiene solo lo que ambos comparten y crea su propia
    zona de memoria, que ``a`` y ``b`` pueden leer y escribir por igual: los
    nombres que ambos declaran pasan a vivir en una celda común.
    """
    _require_blobs(a, b, "&")
    result = Blob(name=name or "<interseccion>", op="&", operands=[a, b])
    comunes = a.declared_names() & b.declared_names()
    for nombre in sorted(comunes):
        celda_a = a.storage.get(nombre)
        celda_b = b.storage.get(nombre)
        if celda_a is None or celda_b is None:
            # El nombre es una referencia a otro blob: se comparte en lectura.
            continue
        celda = _celda_compartida(a, b, nombre)
        a.storage[nombre] = celda
        b.storage[nombre] = celda
        result.storage[nombre] = celda
    if comunes:
        result.inherit(a, exclude=frozenset(a.declared_names() - comunes))
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
    return result


#: Tabla de las operaciones de conjuntos.
SET_FUNCTIONS = {
    "&": intersect,
    "|": union,
    "-": difference,
    "^": symmetric_difference,
}
