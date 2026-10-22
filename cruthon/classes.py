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


class _Docstring:
    """Descriptor del atributo ``__doc__`` de los objetos de Cruthon.

    El atributo ``__doc__`` lo ocupa el docstring que escribió el usuario
    (``f.__doc__``, ``C.__doc__``), que es lo que Python devuelve. El
    docstring de esta implementación se guarda aparte y se devuelve cuando
    se pide el de la clase (``CruthonClass.__doc__``) en vez del de una
    instancia.
    """

    __slots__ = ("_doc_de_la_implementacion",)

    def __init__(self, doc_de_la_implementacion: Optional[str] = None) -> None:
        self._doc_de_la_implementacion = doc_de_la_implementacion

    def __get__(self, obj: Any, objtype: Any = None) -> Any:
        if obj is None:
            # Se pide el docstring de la clase: el de esta implementación.
            return self._doc_de_la_implementacion
        return obj._docstring_del_usuario()


class _Ausente:
    """Marca de "no encontrado" (distinta de ``None``)."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return "<ausente>"


AUSENTE = _Ausente()

#: Los atributos internos de :class:`CruthonClass` (los de ``__slots__``).
#: Todo lo demás que se asigne a la clase es un atributo suyo, visible desde
#: sus instancias, como en Python.
_SLOTS_DE_LA_CLASE = frozenset(
    {"_nombre", "_bases", "_namespace", "_blob", "_mro", "_bases_py"}
)


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

    __doc__ = _Docstring(__doc__)

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

    @property
    def __mro__(self) -> Tuple[Any, ...]:
        """El MRO como tupla, como en Python (``C.__mro__``).

        Las clases de Cruthon van primero, después las bases que son tipos de
        Python (``class M(list):``) y al final ``object``, que en CPython
        cierra la cadena de herencia de todas las clases.
        """
        mro: List[Any] = list(self.mro)
        for base_py in self._bases_py:
            for clase in base_py.__mro__:
                if clase not in mro:
                    mro.append(clase)
        if object not in mro:
            mro.append(object)
        return tuple(mro)

    @property
    def __bases__(self) -> Tuple[Any, ...]:
        """Las bases directas, como en Python (``C.__bases__``)."""
        bases: List[Any] = list(self._bases)
        for base_py in self._bases_py:
            if base_py not in bases:
                bases.append(base_py)
        return tuple(bases)

    def _docstring_del_usuario(self) -> Optional[str]:
        """El docstring que escribió el usuario, si la clase tiene uno."""
        return self._namespace.get("__doc__")

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
        return self._valor_de_la_clase(encontrado)

    def __getattribute__(self, nombre: str) -> Any:
        """Da prioridad al ``__init__`` (y al resto de métodos especiales) que
        escribió el usuario.

        En Python, ``C.__init__`` es el ``__init__`` de la clase, no el de la
        maquinaria que la representa: el método especial del usuario manda
        sobre el de la implementación, igual que con cualquier otro atributo
        del cuerpo de la clase.
        """
        if nombre.startswith("__") and nombre.endswith("__"):
            if nombre not in ("__mro__", "__bases__", "__doc__"):
                namespace = object.__getattribute__(self, "_namespace")
                for clase in object.__getattribute__(self, "mro"):
                    if nombre in clase._namespace:
                        return self._valor_de_la_clase(clase._namespace[nombre])
        return object.__getattribute__(self, nombre)

    def _valor_de_la_clase(self, encontrado: Any) -> Any:
        """El valor de un miembro de la clase, como lo devolvería Python."""
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
        if nombre in _SLOTS_DE_LA_CLASE:
            # Los atributos internos de la clase (``_nombre``, ``_mro``...)
            # van al objeto; todo lo demás es un atributo de la clase.
            object.__setattr__(self, nombre, valor)
            return
        self._namespace[nombre] = valor

    def __repr__(self) -> str:
        # Como en Python, la repr de una clase incluye el módulo donde vive:
        # ``<class '__main__.C'>``. En Cruthon el módulo es el blob.
        modulo = getattr(self._blob, "name", None)
        if not modulo or modulo == "<blob>":
            return f"<class '{self._nombre}'>"
        return f"<class '{modulo}.{self._nombre}'>"


def _es_descriptor(valor: Any) -> bool:
    """¿Es un descriptor? (``property``, ``classmethod``, ``staticmethod``)."""
    if isinstance(valor, CruthonMethod):
        return False
    return hasattr(type(valor), "__get__")


def _es_descriptor_de_datos(valor: Any) -> bool:
    """¿Es un descriptor de datos? (los que además definen ``__set__``).

    Un descriptor de datos tiene prioridad sobre el diccionario de la
    instancia, como en Python: por eso una ``property`` con setter recibe
    la asignación en vez de guardarse como un atributo cualquiera.
    """
    if isinstance(valor, CruthonMethod):
        return False
    tipo = type(valor)
    return hasattr(tipo, "__set__") or hasattr(tipo, "__delete__")


class CruthonInstance:
    """Una instancia de una clase de Cruthon.

    Los atributos del usuario viven en el diccionario de la instancia, como
    en Python: así ``object.__setattr__(self, ...)`` funciona, ``vars()``
    devuelve lo que devolvería en CPython y el idiom
    ``def __setattr__(self, n, v): object.__setattr__(self, n, v)`` hace lo
    que el usuario espera.
    """

    __slots__ = ("_clase", "_base_obj", "__dict__")

    __doc__ = _Docstring(__doc__)

    def __init__(self, clase: CruthonClass, base_obj: Any = None) -> None:
        object.__setattr__(self, "_clase", clase)
        #: El objeto nativo sobre el que se apoya la instancia cuando la clase
        #: hereda de un tipo de Python (``class M(list):``).
        object.__setattr__(self, "_base_obj", base_obj)

    @property
    def base_obj(self) -> Any:
        """El objeto de Python en el que se apoya la instancia, si lo hay."""
        return object.__getattribute__(self, "_base_obj")

    def _docstring_del_usuario(self) -> Optional[str]:
        """El docstring de la clase, como en Python (``instancia.__doc__``)."""
        return self._clase.__doc__

    # -- acceso a atributos ---------------------------------------------
    @property
    def __class__(self) -> "CruthonClass":
        """La clase de la instancia, como en Python.

        ``tipo(instancia)`` sigue siendo ``CruthonInstance`` (el tipo real
        del objeto de Python), pero ``instancia.__class__`` es la clase de
        Cruthon, que es lo que el código de Python consulta.
        """
        return object.__getattribute__(self, "_clase")

    def __getattribute__(self, nombre: str) -> Any:
        """Busca un atributo con el orden de CPython.

        Primero los descriptores de datos de la clase (una ``property``
        manda sobre el diccionario de la instancia), después el diccionario
        de la instancia, después los demás miembros de la clase y, si no
        aparece nada, el ``__getattr__`` que haya escrito el usuario.
        """
        if nombre in ("_clase", "_base_obj"):
            return object.__getattribute__(self, nombre)
        clase = object.__getattribute__(self, "_clase")
        encontrado = clase.lookup(nombre)
        if encontrado is not AUSENTE and _es_descriptor_de_datos(encontrado):
            # Un descriptor de datos manda sobre el diccionario de la
            # instancia, como en Python: ``obj.x`` siempre pasa por el
            # ``property``, aunque ``x`` esté en el diccionario.
            return encontrado.__get__(self, clase)
        atributos = object.__getattribute__(self, "__dict__")
        if nombre in atributos:
            return atributos[nombre]
        if encontrado is not AUSENTE:
            if isinstance(encontrado, CruthonMethod):
                return encontrado.ligar(self)
            if _es_descriptor(encontrado):
                # ``property`` y compañía: el acceso dispara el descriptor.
                return encontrado.__get__(self, clase)
            return encontrado
        try:
            # Los atributos de la propia implementación (``__class__``,
            # ``__doc__`` y el resto del protocolo).
            return object.__getattribute__(self, nombre)
        except AttributeError:
            pass
        base = object.__getattribute__(self, "_base_obj")
        if base is not None:
            # La clase hereda de un tipo de Python: sus métodos y
            # atributos son los de ese tipo.
            return getattr(base, nombre)
        gancho = clase.lookup("__getattr__")
        if gancho is not AUSENTE:
            # El usuario escribió un ``__getattr__``: se llama con el
            # nombre del atributo que no se encontró.
            return self._llamar_al_gancho(gancho, nombre)
        raise AttributeError(
            f"'{clase.nombre}' object has no attribute '{nombre}'"
        )

    def __setattr__(self, nombre: str, valor: Any) -> None:
        clase = object.__getattribute__(self, "_clase")
        gancho = clase.lookup("__setattr__")
        if gancho is not AUSENTE:
            # El usuario escribió un ``__setattr__``: la asignación pasa por
            # él, como en Python.
            self._llamar_al_gancho(gancho, nombre, valor)
            return
        encontrado = clase.lookup(nombre)
        if encontrado is not AUSENTE and _es_descriptor_de_datos(encontrado):
            # Una ``property`` con setter: la asignación pasa por el setter
            # en vez de guardarse como un atributo cualquiera.
            encontrado.__set__(self, valor)
            return
        # Todos los atributos del usuario van al diccionario de la
        # instancia, también los que empiezan por ``_``: en Python
        # ``self._privado = 1`` es lo normal.
        object.__getattribute__(self, "__dict__")[nombre] = valor

    def __delattr__(self, nombre: str) -> None:
        clase = object.__getattribute__(self, "_clase")
        gancho = clase.lookup("__delattr__")
        if gancho is not AUSENTE:
            self._llamar_al_gancho(gancho, nombre)
            return
        encontrado = clase.lookup(nombre)
        if encontrado is not AUSENTE and hasattr(type(encontrado), "__delete__"):
            # Un descriptor con ``__delete__`` decide cómo se borra.
            encontrado.__delete__(self)
            return
        atributos = object.__getattribute__(self, "__dict__")
        if nombre in atributos:
            del atributos[nombre]
            return
        raise AttributeError(
            f"'{clase.nombre}' object has no attribute '{nombre}'"
        )

    def _llamar_al_gancho(self, gancho: Any, *args: Any) -> Any:
        """Llama a un método especial que escribió el usuario."""
        if isinstance(gancho, CruthonMethod):
            return gancho.ligar(self)(*args)
        return gancho(self, *args)

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

    def __format__(self, spec: str) -> str:
        ligable = _delega(self, "__format__")
        if ligable is AUSENTE:
            if spec:
                raise TypeError(
                    "unsupported format string passed to "
                    f"{object.__getattribute__(self, '_clase').nombre}.__format__"
                )
            return str(self)
        return str(ligable(spec))

    def __index__(self) -> int:
        ligable = _delega(self, "__index__")
        if ligable is AUSENTE:
            raise TypeError(
                f"'{object.__getattribute__(self, '_clase').nombre}' object "
                "cannot be interpreted as an integer"
            )
        return int(ligable())

    def __int__(self) -> int:
        ligable = _delega(self, "__int__")
        if ligable is AUSENTE:
            raise TypeError(
                "int() argument must be a string, a bytes-like object or a "
                f"real number, not '{object.__getattribute__(self, '_clase').nombre}'"
            )
        return int(ligable())

    def __float__(self) -> float:
        ligable = _delega(self, "__float__")
        if ligable is AUSENTE:
            raise TypeError(
                "float() argument must be a string or a real number, not "
                f"'{object.__getattribute__(self, '_clase').nombre}'"
            )
        return float(ligable())

    def __bytes__(self) -> bytes:
        ligable = _delega(self, "__bytes__")
        if ligable is AUSENTE:
            raise TypeError(
                f"cannot convert '{object.__getattribute__(self, '_clase').nombre}' "
                "object to bytes"
            )
        return bytes(ligable())

    def __reversed__(self) -> Any:
        ligable = _delega(self, "__reversed__")
        if ligable is AUSENTE:
            raise TypeError(
                f"'{object.__getattribute__(self, '_clase').nombre}' object "
                "is not reversible"
            )
        return ligable()

    def __round__(self, ndigits: Any = None) -> Any:
        ligable = _delega(self, "__round__")
        if ligable is AUSENTE:
            raise TypeError(
                f"type '{object.__getattribute__(self, '_clase').nombre}' "
                "doesn't define __round__ method"
            )
        if ndigits is None:
            return ligable()
        return ligable(ndigits)

    def __trunc__(self) -> int:
        ligable = _delega(self, "__trunc__")
        if ligable is AUSENTE:
            raise TypeError(
                f"'{object.__getattribute__(self, '_clase').nombre}' object "
                "cannot be interpreted as an integer"
            )
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
    # ``__next__`` hace falta para que un iterador propio funcione: Python
    # comprueba que el objeto devuelto por ``__iter__`` tenga ``__next__``.
    "__next__",
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

    __doc__ = _Docstring(__doc__)

    def __init__(self, funcion: Any, clase: Optional[CruthonClass]) -> None:
        self.funcion = funcion
        self.clase = clase

    def ligar(self, instancia: CruthonInstance) -> "CruthonBoundMethod":
        return CruthonBoundMethod(self.funcion, instancia, self.clase)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.funcion(*args, **kwargs)

    def _docstring_del_usuario(self) -> Optional[str]:
        """El docstring del método, como en Python (``C.m.__doc__``)."""
        return self.funcion.__doc__

    def __repr__(self) -> str:
        nombre = getattr(self.funcion, "name", "?")
        if self.clase is not None:
            nombre = f"{self.clase.nombre}.{nombre}"
        return f"<method {nombre}>"


class CruthonBoundMethod:
    """Un método con la instancia ya enlazada como primer argumento."""

    __slots__ = ("funcion", "instancia", "clase")

    __doc__ = _Docstring(__doc__)

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

    def _docstring_del_usuario(self) -> Optional[str]:
        """El docstring del método, como en Python (``obj.m.__doc__``)."""
        return self.funcion.__doc__

    def __repr__(self) -> str:
        # Como en Python: ``<bound method C.m of <C object at 0x...>>``.
        nombre = getattr(self.funcion, "name", "?")
        if self.clase is not None:
            nombre = f"{self.clase.nombre}.{nombre}"
        return f"<bound method {nombre} of {self.instancia!r}>"


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

    def __call__(self, *args: Any) -> "CruthonSuper":
        if not args:
            return CruthonSuper(self.clase, self.instancia)
        if len(args) == 2:
            # ``super(Clase, instancia)``: la forma explícita de Python.
            clase, instancia = args
            if not isinstance(clase, CruthonClass) or not isinstance(
                instancia, CruthonInstance
            ):
                raise TypeError(
                    "super() espera una clase y una instancia"
                )
            return CruthonSuper(clase, instancia)
        raise TypeError("super() acepta cero o dos argumentos")

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
