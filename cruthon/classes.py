"""Clases de Python dentro de Cruthon.

Cruthon sustituye la jerarquía de clases por la geometría de conjuntos: un
*blob* es un conjunto con memoria propia. Pero un lenguaje que se ejecuta
"como Python" tiene que aceptar también ``class``, porque el código de la
biblioteca estándar y los programas existentes la usan.

Estas clases no son blobs: un blob es un conjunto, una clase es una plantilla
de objetos. Conviven en el mismo lenguaje y no se mezclan, igual que en el
documento de diseño el *blob* reemplaza a la clase pero no la prohibe.

El modelo sigue al de CPython: una clase tiene un espacio de nombres propio,
una instancia tiene el suyo, y el orden de resolución de métodos (MRO) se
calcula con la linealización C3. Lo que cambia es el ámbito: un método se
ejecuta con el ámbito del blob donde se definió la clase, no con el de quien
llama.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


class _Ausente:
    """Marca de "no encontrado" (distinta de ``None``)."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return "<ausente>"


AUSENTE = _Ausente()


def mro_de(clase: "CruthonClass") -> List["CruthonClass"]:
    """Calcula el MRO de ``clase`` con la linealización C3 de CPython."""
    bases = list(clase.bases)
    secuencias = [list(base.mro) for base in bases]
    secuencias.append(list(bases))
    return [clase] + _c3_merge(secuencias)


def _c3_merge(secuencias: List[List["CruthonClass"]]) -> List["CruthonClass"]:
    """Fusiona las secuencias de MRO como hace ``type_new`` en CPython."""
    pendientes = [list(secuencia) for secuencia in secuencias if secuencia]
    resultado: List[CruthonClass] = []
    while pendientes:
        candidato: Optional[CruthonClass] = None
        for secuencia in pendientes:
            cabeza = secuencia[0]
            if not any(cabeza in otra[1:] for otra in pendientes):
                candidato = cabeza
                break
        if candidato is None:
            raise TypeError(
                "no se puede crear un orden de resolución de métodos consistente"
            )
        resultado.append(candidato)
        for secuencia in pendientes:
            if secuencia and secuencia[0] is candidato:
                del secuencia[0]
        pendientes = [secuencia for secuencia in pendientes if secuencia]
    return resultado


class CruthonClass:
    """Una clase de Cruthon (``class Nombre(Base): ...``)."""

    __slots__ = ("_nombre", "_bases", "_namespace", "_blob", "_mro", "_bases_py")

    def __init__(
        self,
        nombre: str,
        bases: Sequence["CruthonClass"],
        namespace: Dict[str, Any],
        blob: Any = None,
        bases_py: Sequence[type] = (),
    ) -> None:
        self._nombre = nombre
        self._bases = tuple(bases)
        self._namespace = namespace
        self._blob = blob
        #: Las bases que son tipos de Python (``class M(list):``).
        self._bases_py = tuple(bases_py)
        self._mro: List[CruthonClass] = []

    # -- introspección --------------------------------------------------
    @property
    def nombre(self) -> str:
        return self._nombre

    @property
    def bases(self) -> Tuple["CruthonClass", ...]:
        return self._bases

    @property
    def bases_py(self) -> Tuple[type, ...]:
        """Las bases que son tipos de Python (``class M(list):``)."""
        return self._bases_py

    @property
    def namespace(self) -> Dict[str, Any]:
        return self._namespace

    @property
    def blob(self) -> Any:
        """El blob donde se definió la clase: el ámbito de sus métodos."""
        return self._blob

    @property
    def mro(self) -> List["CruthonClass"]:
        if not self._mro:
            self._mro = mro_de(self)
        return self._mro

    # -- búsqueda de nombres --------------------------------------------
    def lookup(self, nombre: str) -> Any:
        """Busca ``nombre`` en el MRO; devuelve ``AUSENTE`` si no está."""
        for clase in self.mro:
            if nombre in clase._namespace:
                return clase._namespace[nombre]
        return AUSENTE

    def __getattr__(self, nombre: str) -> Any:
        # Sólo se llama cuando el acceso normal falló: aquí, todo atributo
        # de la clase vive en su espacio de nombres.
        encontrado = self.lookup(nombre)
        if encontrado is AUSENTE:
            raise AttributeError(
                f"type object '{self._nombre}' has no attribute '{nombre}'"
            )
        if isinstance(encontrado, CruthonMethod):
            # En Python 3, ``A.f`` es la función suelta, no un método ligado.
            return encontrado.funcion
        if _es_descriptor(encontrado):
            # ``property``, ``classmethod`` y ``staticmethod`` son
            # descriptores: el acceso a la clase es lo que dispara el
            # protocolo, igual que en Python.
            return encontrado.__get__(None, self)
        return encontrado

    def __setattr__(self, nombre: str, valor: Any) -> None:
        if nombre.startswith("_"):
            object.__setattr__(self, nombre, valor)
            return
        self._namespace[nombre] = valor

    def __repr__(self) -> str:
        return f"<class '{self._nombre}'>"


def _es_descriptor(valor: Any) -> bool:
    """¿Es un descriptor? (``property``, ``classmethod``, ``staticmethod``)."""
    if isinstance(valor, CruthonMethod):
        return False
    return hasattr(type(valor), "__get__")


class CruthonInstance:
    """Una instancia de una clase de Cruthon."""

    __slots__ = ("_clase", "_atributos", "_base_obj")

    def __init__(self, clase: CruthonClass, base_obj: Any = None) -> None:
        object.__setattr__(self, "_clase", clase)
        object.__setattr__(self, "_atributos", {})
        #: El objeto nativo sobre el que se apoya la instancia cuando la clase
        #: hereda de un tipo de Python (``class M(list):``).
        object.__setattr__(self, "_base_obj", base_obj)

    @property
    def base_obj(self) -> Any:
        """El objeto de Python en el que se apoya la instancia, si lo hay."""
        return object.__getattribute__(self, "_base_obj")

    # -- acceso a atributos ---------------------------------------------
    def __getattr__(self, nombre: str) -> Any:
        atributos = object.__getattribute__(self, "_atributos")
        if nombre in atributos:
            return atributos[nombre]
        clase = object.__getattribute__(self, "_clase")
        encontrado = clase.lookup(nombre)
        if encontrado is AUSENTE:
            base = object.__getattribute__(self, "_base_obj")
            if base is not None:
                # La clase hereda de un tipo de Python: sus métodos y
                # atributos son los de ese tipo.
                return getattr(base, nombre)
            raise AttributeError(
                f"'{clase.nombre}' object has no attribute '{nombre}'"
            )
        if isinstance(encontrado, CruthonMethod):
            return encontrado.ligar(self)
        if _es_descriptor(encontrado):
            # ``property`` y compañía: el acceso dispara el descriptor.
            return encontrado.__get__(self, clase)
        return encontrado

    def __setattr__(self, nombre: str, valor: Any) -> None:
        if nombre.startswith("_"):
            object.__setattr__(self, nombre, valor)
            return
        object.__getattribute__(self, "_atributos")[nombre] = valor

    def __delattr__(self, nombre: str) -> None:
        atributos = object.__getattribute__(self, "_atributos")
        if nombre in atributos:
            del atributos[nombre]
            return
        object.__delattr__(self, nombre)

    @property
    def clase(self) -> CruthonClass:
        return object.__getattribute__(self, "_clase")

    def __repr__(self) -> str:
        clase = object.__getattribute__(self, "_clase")
        encontrado = clase.lookup("__repr__")
        if encontrado is not AUSENTE:
            if isinstance(encontrado, CruthonMethod):
                return str(encontrado.ligar(self)())
            return str(encontrado)
        base = object.__getattribute__(self, "_base_obj")
        if base is not None:
            # La clase hereda de un tipo de Python: se representa como él.
            return repr(base)
        return f"<{clase.nombre} object>"

    def __str__(self) -> str:
        clase = object.__getattribute__(self, "_clase")
        encontrado = clase.lookup("__str__")
        if encontrado is not AUSENTE:
            if isinstance(encontrado, CruthonMethod):
                return str(encontrado.ligar(self)())
            return str(encontrado)
        base = object.__getattribute__(self, "_base_obj")
        if base is not None:
            return str(base)
        return self.__repr__()

    # -- protocolos especiales -------------------------------------------
    # Los métodos especiales los busca Python en el *tipo*, no en la
    # instancia: por eso cada uno se declara aquí y delega en el MRO de la
    # clase del objeto.
    def __len__(self) -> int:
        ligable = _delega(self, "__len__")
        if ligable is AUSENTE:
            raise TypeError(f"object of type '{self.clase.nombre}' has no len()")
        return ligable()

    def __getitem__(self, clave: Any) -> Any:
        ligable = _delega(self, "__getitem__")
        if ligable is AUSENTE:
            raise TypeError(
                f"'{self.clase.nombre}' object is not subscriptable"
            )
        return ligable(clave)

    def __setitem__(self, clave: Any, valor: Any) -> None:
        ligable = _delega(self, "__setitem__")
        if ligable is AUSENTE:
            raise TypeError(
                f"'{self.clase.nombre}' object does not support item assignment"
            )
        ligable(clave, valor)

    def __delitem__(self, clave: Any) -> None:
        ligable = _delega(self, "__delitem__")
        if ligable is AUSENTE:
            raise TypeError(f"'{self.clase.nombre}' object does not support item deletion")
        ligable(clave)

    def __iter__(self) -> Any:
        ligable = _delega(self, "__iter__")
        if ligable is AUSENTE:
            raise TypeError(f"'{self.clase.nombre}' object is not iterable")
        return ligable()

    def __contains__(self, elemento: Any) -> bool:
        ligable = _delega(self, "__contains__")
        if ligable is AUSENTE:
            raise TypeError(
                f"argument of type '{self.clase.nombre}' is not iterable"
            )
        return bool(ligable(elemento))

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        ligable = _delega(self, "__call__")
        if ligable is AUSENTE:
            raise TypeError(f"'{self.clase.nombre}' object is not callable")
        return ligable(*args, **kwargs)

    def __eq__(self, otro: Any) -> Any:
        ligable = _delega(self, "__eq__")
        if ligable is AUSENTE:
            return NotImplemented
        return ligable(otro)

    def __ne__(self, otro: Any) -> Any:
        ligable = _delega(self, "__ne__")
        if ligable is AUSENTE:
            resultado = self.__eq__(otro)
            if resultado is NotImplemented:
                return NotImplemented
            return not resultado
        return ligable(otro)

    def __bool__(self) -> bool:
        ligable = _delega(self, "__bool__")
        if ligable is AUSENTE:
            return True
        return bool(ligable())

    def __hash__(self) -> int:
        ligable = _delega(self, "__hash__")
        if ligable is AUSENTE:
            return object.__hash__(self)
        return int(ligable())


def _delega(instancia: "CruthonInstance", nombre: str) -> Any:
    """Busca un método especial en el MRO de la clase de la instancia.

    Si la clase hereda de un tipo de Python, se recurre a los métodos
    especiales de ese tipo: es lo que hace que ``len(m)`` funcione en
    ``class M(list):``.
    """
    clase = object.__getattribute__(instancia, "_clase")
    encontrado = clase.lookup(nombre)
    if encontrado is AUSENTE:
        base = object.__getattribute__(instancia, "_base_obj")
        if base is not None:
            metodo = getattr(type(base), nombre, None)
            if metodo is not None:
                return metodo.__get__(base, type(base))
        return AUSENTE
    if isinstance(encontrado, CruthonMethod):
        return encontrado.ligar(instancia)
    return encontrado


def buscar_metodo(instancia: "CruthonInstance", nombre: str) -> Any:
    """Busca un método en el MRO de la clase de la instancia.

    Devuelve ``AUSENTE`` si la clase no lo define. Es la puerta de entrada
    para los protocolos especiales (``__enter__``, ``__exit__``...), que en
    Python se buscan en el tipo y aquí en el MRO de la clase.
    """
    return _delega(instancia, nombre)


def _metodo_delegado(nombre: str):
    """Crea un método especial que delega en el MRO de la clase."""

    def metodo(self, *args: Any, **kwargs: Any) -> Any:
        ligable = _delega(self, nombre)
        if ligable is AUSENTE:
            raise TypeError(
                f"'{object.__getattribute__(self, '_clase').nombre}' object "
                f"does not support {nombre}"
            )
        return ligable(*args, **kwargs)

    metodo.__name__ = nombre
    metodo.__qualname__ = f"CruthonInstance.{nombre}"
    return metodo


#: Métodos especiales que se delegan tal cual en el MRO de la clase.
_METODOS_DELEGADOS = (
    "__lt__", "__le__", "__gt__", "__ge__",
    "__add__", "__radd__", "__sub__", "__rsub__",
    "__mul__", "__rmul__", "__truediv__", "__rtruediv__",
    "__floordiv__", "__rfloordiv__", "__mod__", "__rmod__",
    "__pow__", "__rpow__", "__neg__", "__pos__", "__abs__",
    "__and__", "__or__", "__xor__", "__invert__",
    "__lshift__", "__rshift__",
)

for _nombre in _METODOS_DELEGADOS:
    setattr(CruthonInstance, _nombre, _metodo_delegado(_nombre))
del _nombre


class CruthonMethod:
    """Una función que vive en el espacio de nombres de una clase."""

    __slots__ = ("funcion", "clase")

    def __init__(self, funcion: Any, clase: Optional[CruthonClass]) -> None:
        self.funcion = funcion
        self.clase = clase

    def ligar(self, instancia: CruthonInstance) -> "CruthonBoundMethod":
        return CruthonBoundMethod(self.funcion, instancia, self.clase)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.funcion(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<method {getattr(self.funcion, 'name', '?')}>"


class CruthonBoundMethod:
    """Un método con la instancia ya enlazada como primer argumento."""

    __slots__ = ("funcion", "instancia", "clase")

    def __init__(
        self,
        funcion: Any,
        instancia: CruthonInstance,
        clase: Optional[CruthonClass],
    ) -> None:
        self.funcion = funcion
        self.instancia = instancia
        self.clase = clase

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        interprete = self.funcion.interpreter
        return interprete.call_method(
            self.funcion, self.instancia, list(args), dict(kwargs)
        )

    def __repr__(self) -> str:
        return f"<bound method {getattr(self.funcion, 'name', '?')}>"


class SuperFabrica:
    """Lo que devuelve el nombre ``super`` dentro de un método.

    En Python ``super`` es una clase y ``super()`` construye el objeto. Aquí
    ``super`` es una fábrica: al llamarla sin argumentos devuelve el
    ``CruthonSuper`` de la clase y la instancia del método en curso.
    """

    __slots__ = ("clase", "instancia")

    def __init__(self, clase: CruthonClass, instancia: CruthonInstance) -> None:
        self.clase = clase
        self.instancia = instancia

    def __call__(self) -> "CruthonSuper":
        return CruthonSuper(self.clase, self.instancia)

    def __repr__(self) -> str:
        return "<super>"


class CruthonSuper:
    """``super()``: empieza la búsqueda en la clase siguiente del MRO."""

    __slots__ = ("clase", "instancia")

    def __init__(self, clase: CruthonClass, instancia: CruthonInstance) -> None:
        self.clase = clase
        self.instancia = instancia

    def lookup(self, nombre: str) -> Any:
        return _buscar_super(self.clase, self.instancia, nombre)

    def __getattribute__(self, nombre: str) -> Any:
        # ``super()`` tiene que encontrar los métodos de la clase base aunque
        # se llamen como ``__init__``: por eso se intercepta *toda* lectura
        # de atributo y no sólo los que no existen (``__getattr__`` nunca
        # llega a ejecutarse para los nombres que ya existen en ``object``).
        if nombre in ("clase", "instancia"):
            return object.__getattribute__(self, nombre)
        clase = object.__getattribute__(self, "clase")
        instancia = object.__getattribute__(self, "instancia")
        encontrado = _buscar_super(clase, instancia, nombre)
        if encontrado is not AUSENTE:
            return encontrado
        return object.__getattribute__(self, nombre)

    def __repr__(self) -> str:
        return "<super>"


def _buscar_super(clase: CruthonClass, instancia: CruthonInstance, nombre: str) -> Any:
    """Busca ``nombre`` en el MRO, empezando después de ``clase``."""
    mro = clase.mro
    indice = mro.index(clase)
    for otra in mro[indice + 1 :]:
        if nombre in otra.namespace:
            encontrado = otra.namespace[nombre]
            if isinstance(encontrado, CruthonMethod):
                return encontrado.ligar(instancia)
            return encontrado
    return AUSENTE
