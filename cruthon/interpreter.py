"""Intérprete de Cruthon.

Evaluator que recorre el árbol de sintaxis como lo hace ``Python/compile.c``
generando bytecode y ``Python/ceval.c`` ejecutándolo, pero resolviendo los
nombres por *pertenencia a conjuntos* en vez de por MRO.

Reglas que implementa (todas tomadas del documento de diseño):

* Una función se ejecuta siempre con el ámbito del blob donde está físicamente
  definida, nunca con el de quien la llama.
* Un nombre sólo es visible si vive dentro del área del blob activo; si no,
  el error es de alcance (``ScopeError``), no un ``NameError`` de Python.
* Los blobs hermanos no se ven entre sí: lo que vive en ``frontend`` es
  inalcanzable desde ``backend`` y al revés.
* Una intersección crea memoria nueva que los dos blobs de origen leen y
  escriben por igual.
"""

from __future__ import annotations

import importlib
import operator
import queue
import threading
from typing import Any, Dict, List, Optional

from . import nodes as N
from .blobs import SET_FUNCTIONS, SET_OPERATIONS, Blob
from .builtins import make_builtins
from .classes import (
    AUSENTE,
    CruthonClass,
    CruthonInstance,
    CruthonMethod,
    SuperFabrica,
    _Docstring,
    buscar_metodo,
)
from .errors import (
    BlobTypeError,
    CruthonError,
    CruthonRuntimeError,
    CruthonSyntaxError,
    CruthonTypeError,
    DuplicateName,
    NameNotFound,
    ScopeError,
)
from .lexer import TokenType, tokenize
from .parser import Parser, parse


class ReturnSignal(Exception):
    """Salida de una función (``return``)."""

    def __init__(self, value: Any = None) -> None:
        super().__init__("return")
        self.value = value


class BreakSignal(Exception):
    """Salida de un bucle (``break``)."""


class ContinueSignal(Exception):
    """Salto al siguiente ciclo (``continue``)."""


class Frame:
    """Marco de ejecución local de una función o comprensión."""

    __slots__ = (
        "locals",
        "blob",
        "parent",
        "globals",
        "nonlocals",
        "is_class_body",
        "owner_class",
        "owner_instance",
        "generator",
        "escritura_aislada",
        "dict_globals",
        "dict_locals",
    )

    def __init__(
        self,
        blob: Blob,
        locals: Optional[Dict[str, Any]] = None,
        parent: Optional["Frame"] = None,
    ) -> None:
        self.locals = locals if locals is not None else {}
        self.blob = blob
        self.parent = parent
        self.globals: set = set()
        self.nonlocals: set = set()
        #: ``True`` en el cuerpo de una clase: ahí los nombres se enlazan en
        #: el espacio de nombres de la clase, no en el blob.
        self.is_class_body = False
        #: La clase a la que pertenece el método en curso (para ``super()``).
        self.owner_class: Optional[CruthonClass] = None
        #: La instancia del método en curso (para ``super()``).
        self.owner_instance: Optional[CruthonInstance] = None
        #: El generador que está ejecutando este marco, si lo hay.
        self.generator: Optional["CruthonGenerator"] = None
        #: ``True`` en los marcos de ``exec``: alli las asignaciones van a
        #: los globales y no a los locales de la función que las rodea, como
        #: en Python.
        self.escritura_aislada = False
        #: El diccionario pasado a ``exec(codigo, globals)`` o a
        #: ``eval(expr, globals)``, si lo hubo: es el ámbito global de ese
        #: código, como en Python.
        self.dict_globals: Optional[Dict[str, Any]] = None
        #: El diccionario pasado como ``locals`` a ``exec``/``eval``: ahí
        #: van las escrituras de ese código.
        self.dict_locals: Optional[Dict[str, Any]] = None


class CruthonFunction:
    """Función de Cruthon.

    Guarda el blob donde fue definida: ese es su ámbito, siempre, sin
    importar quién la llame. ``closure`` es el marco de la función que la
    definió, si la hubo: las funciones anidadas ven las variables locales de
    su entorno, como en Python.

    Es además un *callable* de Python: implementa ``__call__``, de modo que
    puede entregarse a un builtin de la biblioteca estándar (``map``,
    ``filter``, ``sorted`` con ``key=``) sin adaptadores intermedios.
    """

    __slots__ = (
        "name",
        "args",
        "body",
        "blob",
        "closure",
        "interpreter",
        "owner_class",
        "is_generator",
        "doc",
        "__annotations__",
        # Los valores por defecto de los parámetros, ya evaluados: como en
        # Python, se evalúan al definir la función, no al llamarla.
        "defaults_valorados",
        # Como las funciones de Python, admiten atributos propios: un
        # decorador puede colgarlesmetadata (``f.etiqueta = 'x'``).
        "__dict__",
    )

    __doc__ = _Docstring(__doc__)

    def __init__(
        self,
        name: str,
        args: N.Arguments,
        body: List[Any],
        blob: Blob,
        closure: Optional["Frame"] = None,
        interpreter: Optional["Interpreter"] = None,
        owner_class: Optional[CruthonClass] = None,
        doc: Optional[str] = None,
        annotations: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.args = args
        self.body = body
        self.blob = blob
        self.closure = closure
        self.interpreter = interpreter
        #: La clase donde se definió el método (``None`` si es una función libre).
        self.owner_class = owner_class
        #: ``True`` si el cuerpo tiene un ``yield``: la función es generadora.
        self.is_generator = _tiene_yield(body)
        #: El docstring de la función, si su cuerpo empezaba con una cadena.
        self.doc = doc
        #: Las anotaciones de sus parámetros y de su retorno, ya evaluadas.
        self.__annotations__ = annotations if annotations is not None else {}
        #: Los valores por defecto de sus parámetros, ya evaluados.
        self.defaults_valorados: Dict[str, Any] = {}

    def _docstring_del_usuario(self) -> Optional[str]:
        """El docstring de la función, como en Python (``f.__doc__``)."""
        return self.doc

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.interpreter is None:  # pragma: no cover - defensivo
            raise CruthonRuntimeError(f"la función {self.name!r} no tiene intérprete")
        return self.interpreter.call_function(self, list(args), dict(kwargs))

    @property
    def __name__(self) -> str:
        """El nombre de la función, como en Python."""
        return self.name

    @__name__.setter
    def __name__(self, valor: str) -> None:
        """Como en Python, ``f.__name__`` se puede cambiar (un decorador lo
        hace a menudo para conservar el nombre original)."""
        self.name = valor

    def __repr__(self) -> str:
        # Como en Python: ``<function f>``, y ``<function C.m>`` para un
        # método. Sin la dirección de memoria, para que la salida sea
        # reproducible.
        nombre = self.name
        if self.owner_class is not None:
            nombre = f"{self.owner_class.nombre}.{nombre}"
        return f"<function {nombre}>"


def _tiene_yield(body: List[Any]) -> bool:
    """¿El cuerpo de la función contiene un ``yield``?

    No se baja al cuerpo de las funciones anidadas: un ``yield`` dentro de
    una función interna pertenece a esa función, como en Python.
    """
    return any(_nodo_tiene_yield(statement) for statement in body)


def _nodo_tiene_yield(node: Any) -> bool:
    if isinstance(node, N.Yield):
        return True
    if isinstance(node, (N.FuncDef, N.ClassDef, N.Lambda)):
        return False
    for valor in vars(node).values():
        if isinstance(valor, list):
            if any(isinstance(item, N.Node) and _nodo_tiene_yield(item) for item in valor):
                return True
        elif isinstance(valor, N.Node) and _nodo_tiene_yield(valor):
            return True
    return False


class _Fin:
    """Marca de fin del flujo de un generador."""

    __slots__ = ()


class _Cerrar:
    """Señal de cierre: el generador debe desenrollarse."""

    __slots__ = ()


class _CerrarGenerador(Exception):
    """Se lanza dentro del hilo del generador para desenrollarlo."""

    def __init__(self) -> None:
        super().__init__("close")


class CruthonGenexp:
    """Expresión generadora (``(x for x in xs)``).

    Envuelve al generador de Python que la implementa para que se comporte
    como el de CPython: se puede iterar, enviarle valores con ``send``,
    cerrarlo con ``close`` y su ``repr`` es la de Python.
    """

    __slots__ = ("_generador",)

    def __init__(self, generador: Any) -> None:
        self._generador = generador

    def __iter__(self) -> "CruthonGenexp":
        return self

    def __next__(self) -> Any:
        return next(self._generador)

    def send(self, valor: Any) -> Any:
        return self._generador.send(valor)

    def close(self) -> None:
        self._generador.close()

    def __repr__(self) -> str:
        return f"<generator object <genexpr> at 0x{id(self):x}>"


class CruthonGenerator:
    """Generador de Cruthon: el cuerpo se ejecuta en un hilo aparte.

    Cada ``yield`` entrega un valor y el hilo espera a que el consumidor pida
    el siguiente. Así el generador se comporta como el de Python sin tener que
    reescribir el evaluador entero como un generador: el hilo conserva el
    punto exacto en el que quedó la ejecución.
    """

    def __init__(
        self,
        interpreter: "Interpreter",
        function: CruthonFunction,
        args: List[Any],
        kwargs: Dict[str, Any],
        owner_class: Optional[CruthonClass] = None,
        owner_instance: Optional[CruthonInstance] = None,
    ) -> None:
        self._interpreter = interpreter
        self._function = function
        self._args = list(args)
        self._kwargs = dict(kwargs)
        self._owner_class = owner_class
        self._owner_instance = owner_instance
        self._valores: "queue.Queue[Any]" = queue.Queue()
        self._peticiones: "queue.Queue[Any]" = queue.Queue()
        self._error: Optional[BaseException] = None
        self._retorno: Any = None
        self._terminado = False
        #: ``True`` hasta que el generador produzca su primer valor.
        self._primer = True
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    def _correr(self) -> None:
        try:
            self._retorno = self._interpreter._ejecutar_funcion(
                self._function,
                self._args,
                self._kwargs,
                owner_class=self._owner_class,
                owner_instance=self._owner_instance,
                generator=self,
            )
        except BaseException as error:  # noqa: BLE001 - se relanza al consumidor
            self._error = error
        finally:
            self._valores.put(_Fin())

    def emitir(self, valor: Any) -> Any:
        """Entrega un valor y espera a que el consumidor pida el siguiente.

        Devuelve lo que el consumidor haya enviado (``None`` con ``next``,
        el valor de ``send``). Así ``x = yield 1`` recibe el valor enviado.
        """
        self._valores.put(valor)
        enviado = self._peticiones.get()
        if enviado is _Cerrar:
            raise _CerrarGenerador()
        return enviado

    def __iter__(self) -> "CruthonGenerator":
        return self

    def __next__(self) -> Any:
        return self._avanzar(None, es_envio=False)

    def send(self, valor: Any) -> Any:
        """Envía un valor al generador, como en Python."""
        if self._terminado:
            raise self._stop_iteration()
        return self._avanzar(valor, es_envio=True)

    def _stop_iteration(self) -> StopIteration:
        """La ``StopIteration`` que cierra el generador, como en Python.

        En Python, un generador que termina sin valor devuelveraises
        ``StopIteration()`` sin argumentos: ``str()`` de esa excepción es la
        cadena vacía. Sólo si el generador devuelve un valor, ese valor
        viaja en la excepción.
        """
        if self._retorno is None:
            return StopIteration()
        return StopIteration(self._retorno)

    def _avanzar(self, enviado: Any, es_envio: bool) -> Any:
        """Reanuda el generador con ``enviado`` y devuelve el valor siguiente.

        El orden importa: primero se entrega el valor al generador (que está
        suspendido en su ``yield``) y luego se espera el valor que produce.
        Así ``send`` se comporta como en Python: lo que se envía es el valor
        que toma la expresión ``yield``.

        La excepción es la primera llamada con ``next``: un ``next`` sobre un
        generador recién creado lo deja suspendido en su primer ``yield`` sin
        tomarlo (``x = yield 1`` todavía no tiene valor). Un ``send`` sí lo
        reanuda, porque en Python el valor enviado es lo que toma ese
        ``yield``.
        """
        if self._terminado:
            raise self._stop_iteration()
        if not (self._primer and not es_envio):
            self._peticiones.put(enviado)
        item = self._valores.get()
        if isinstance(item, _Fin):
            self._terminado = True
            if self._error is not None:
                error, self._error = self._error, None
                raise error
            raise self._stop_iteration()
        self._primer = False
        return item

    def close(self) -> None:
        """Cierra el generador: el hilo que lo ejecuta termina."""
        self._terminado = True
        self._peticiones.put(_Cerrar)
        self._hilo.join(timeout=1.0)


_BIN_OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
    "&": operator.and_,
    "|": operator.or_,
    "^": operator.xor,
    "<<": operator.lshift,
    ">>": operator.rshift,
    "@": operator.matmul,
}

_COMPARE_OPS = {
    "<": operator.lt,
    ">": operator.gt,
    "==": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
    "!=": operator.ne,
}

#: El método in-place de cada operador de asignación aumentada: ``x += y``
#: llama primero a ``__iadd__``, si el objeto lo define.
_AUG_ASSIGN_METODOS = {
    "+": "__iadd__",
    "-": "__isub__",
    "*": "__imul__",
    "/": "__itruediv__",
    "//": "__ifloordiv__",
    "%": "__imod__",
    "**": "__ipow__",
    "&": "__iand__",
    "|": "__ior__",
    "^": "__ixor__",
    "<<": "__ilshift__",
    ">>": "__irshift__",
    "@": "__imatmul__",
}


class Interpreter:
    """Evalúa un módulo de Cruthon."""

    def __init__(self, filename: str = "<cruthon>") -> None:
        self.filename = filename
        self.globals = Blob("__main__")
        self.builtins = make_builtins()
        #: La excepción que se está manejando ahora mismo (para ``raise``
        #: a secas).
        self.excepcion_actual: Optional[BaseException] = None
        #: El docstring del módulo en ejecución (su ``__doc__``).
        self.doc_modulo: Optional[str] = None

    # -- programa -------------------------------------------------------
    def run(self, module: N.Module) -> Any:
        """Ejecuta un módulo y devuelve el valor de su última expresión."""
        if module.doc is not None:
            # El docstring del módulo es su ``__doc__``, como en Python.
            self.doc_modulo = module.doc
        return self.exec_block(module.body, self.globals, None)

    def exec_block(self, body: List[Any], blob: Blob, frame: Optional[Frame]) -> Any:
        last = None
        for statement in body:
            value = self.exec_statement(statement, blob, frame)
            if isinstance(statement, N.ExprStmt):
                last = value
        return last

    # -- sentencias -----------------------------------------------------
    def exec_statement(self, node: Any, blob: Blob, frame: Optional[Frame]) -> Any:
        method = self._STATEMENTS.get(type(node))
        if method is None:
            raise CruthonRuntimeError(f"sentencia no soportada: {type(node).__name__}")
        return method(self, node, blob, frame)

    def _exec_expr_stmt(self, node, blob, frame):
        return self.eval(node.value, blob, frame)

    def _exec_assign(self, node, blob, frame):
        value = self.eval(node.value, blob, frame)
        for target in node.targets:
            self.assign(target, value, blob, frame)
        return None

    def _exec_aug_assign(self, node, blob, frame):
        current = self.eval(node.target, blob, frame)
        operand = self.eval(node.value, blob, frame)
        value = self._operador_aumentado(node.op, current, operand)
        self.assign(node.target, value, blob, frame)
        return None

    def _operador_aumentado(self, op: str, actual: Any, operando: Any) -> Any:
        """Calcula el valor de ``x += y`` (y de los demás ``+=``).

        Como en Python, manda el operador *in-place*: si el objeto define
        ``__iadd__`` (o ``__imul__``, ``__isub__``...) ese método es el que
        calcula el resultado. Sólo si no lo define, o si devuelve
        ``NotImplemented``, se usa el operador normal (``__add__``) y el
        resultado se asigna después.
        """
        nombre = _AUG_ASSIGN_METODOS.get(op)
        if nombre is not None:
            metodo = self._metodo_especial(actual, nombre)
            if metodo is not None:
                resultado = metodo(operando)
                if resultado is not NotImplemented:
                    return resultado
        return _BIN_OPS[op](actual, operando)

    def _exec_ann_assign(self, node, blob, frame):
        """Ejecuta ``x: int = 5``: anota y asigna.

        La anotación se evalúa siempre (por si tiene efectos secundarios) y,
        en el cuerpo de una clase, se guarda en ``__annotations__``, como en
        Python.
        """
        annotation = self.eval(node.annotation, blob, frame)
        nombre = node.target.id if isinstance(node.target, N.Name) else None
        if nombre is not None:
            if frame is not None and frame.is_class_body:
                anotaciones = frame.locals.setdefault("__annotations__", {})
                anotaciones[nombre] = annotation
            elif frame is None:
                ref = blob.lookup("__annotations__")
                if ref is None:
                    anotaciones = {}
                    blob.define("__annotations__", anotaciones)
                else:
                    anotaciones = ref.get()
                anotaciones[nombre] = annotation
        if node.value is not None:
            self.assign(node.target, self.eval(node.value, blob, frame), blob, frame)
        return None

    def _exec_blob_decl(self, node, blob, frame):
        # Un nombre es único dentro de su blob: dos regiones del mismo blob no
        # pueden compartirlo. Se falla en la declaración duplicada, que es
        # donde está el error, y no donde después se usa el nombre.
        if node.name in blob.declared_names():
            raise DuplicateName(
                f"ya vive un nombre {node.name!r} en el blob {blob.name!r}: "
                "dos regiones del mismo blob no pueden compartir nombre",
                self.filename,
                node.lineno,
            )
        if node.source is None:
            new_blob = Blob(node.name)
        else:
            new_blob = self._eval_blob_source(node.source, node.name, blob, frame)
            if not isinstance(new_blob, Blob):
                raise BlobTypeError(
                    f"la expresión de 'blob {node.name}' debe producir un blob, "
                    f"no {type(new_blob).__name__}"
                )
            if new_blob.name != node.name:
                new_blob.name = node.name
                if new_blob.op == "&":
                    for operand in new_blob.operands:
                        operand.reference(node.name, new_blob)
        blob.reference(node.name, new_blob)
        blob.add_child(new_blob)
        self.exec_block(node.body, new_blob, None)
        return None

    def _exec_func_def(self, node, blob, frame):
        owner_class = frame.owner_class if frame is not None else None
        function = CruthonFunction(
            node.name,
            node.args,
            node.body,
            blob,
            frame,
            self,
            owner_class,
            doc=node.doc,
            annotations=self._anotaciones_de(node, blob, frame),
        )
        function.defaults_valorados = self._valores_por_defecto(
            node.args, blob, frame
        )
        if frame is not None and frame.is_class_body:
            # Un método no cierra sobre el cuerpo de la clase: en Python,
            # ``self.x`` es la forma de llegar al atributo, no el nombre
            # desnudo. El ámbito del método es el blob donde se definió
            # la clase.
            function.closure = None
        # Los decoradores se aplican de abajo hacia arriba, como en Python:
        # ``@a @b`` es ``a(b(f))``.
        for decorator in reversed(node.decorators or []):
            function = self._aplicar_decorador(decorator, function, blob, frame)
        if frame is not None:
            # Como en Python, una función definida dentro de otra es una
            # variable local de la que la define (y la puede cerrar).
            self._bind(node.name, function, blob, frame)
        else:
            blob.define(node.name, function)
        return None

    def _valores_por_defecto(self, spec, blob, frame) -> Dict[str, Any]:
        """Los valores por defecto de los parámetros, ya evaluados.

        Como en Python, se evalúan al *definir* la función y no al llamarla:
        ``def f(x=[])`` comparte la misma lista entre todas las llamadas, y
        ``def f(x=now())`` llama a ``now()`` una sola vez.
        """
        valores: Dict[str, Any] = {}
        if spec.defaults:
            for arg, default in zip(
                spec.args[len(spec.args) - len(spec.defaults) :], spec.defaults
            ):
                valores[arg.name] = self.eval(default, blob, frame)
        for arg in spec.kwonlyargs:
            if arg.default is not None:
                valores[arg.name] = self.eval(arg.default, blob, frame)
        return valores

    def _anotaciones_de(self, node, blob, frame) -> Dict[str, Any]:
        """Las anotaciones de una función, ya evaluadas.

        Como en Python, las anotaciones se evalúan al *definir* la función
        (no al llamarla) y quedan en ``f.__annotations__``: las de cada
        parámetro por su nombre y la del retorno bajo la clave ``"return"``.
        """
        spec = node.args
        anotaciones: Dict[str, Any] = {}
        for arg in list(spec.args) + list(spec.kwonlyargs):
            if arg.annotation is not None:
                anotaciones[arg.name] = self.eval(arg.annotation, blob, frame)
        if spec.vararg is not None and spec.vararg_annotation is not None:
            anotaciones[spec.vararg] = self.eval(spec.vararg_annotation, blob, frame)
        if spec.kwarg is not None and spec.kwarg_annotation is not None:
            anotaciones[spec.kwarg] = self.eval(spec.kwarg_annotation, blob, frame)
        if node.returns is not None:
            anotaciones["return"] = self.eval(node.returns, blob, frame)
        return anotaciones

    def _aplicar_decorador(self, node, objetivo: Any, blob: Blob, frame: Optional[Frame]) -> Any:
        """Aplica un decorador: ``decorador(objetivo)``."""
        decorador = self.eval(node, blob, frame)
        if not callable(decorador):
            raise CruthonRuntimeError(
                f"un decorador debe ser invocable, no {type(decorador).__name__}"
            )
        return self.call(decorador, [objetivo], {})

    def _exec_class_def(self, node, blob, frame):
        """Ejecuta ``class Nombre(Base):``."""
        bases: List[CruthonClass] = []
        bases_py: List[type] = []
        for base_node in node.bases:
            base = self.eval(base_node, blob, frame)
            if isinstance(base, CruthonClass):
                bases.append(base)
                # Si la clase base se apoya en un tipo de Python
                # (``class A(list):``), la que hereda de ella también.
                for base_py in base.bases_py:
                    if base_py not in bases_py:
                        bases_py.append(base_py)
            elif isinstance(base, type):
                # Herencia de un tipo de Python (``class M(list):``): la
                # clase se apoya en el comportamiento del tipo nativo.
                bases_py.append(base)
            else:
                raise CruthonRuntimeError(
                    "la base de una clase debe ser una clase, no "
                    f"{type(base).__name__}"
                )
        namespace: Dict[str, Any] = {
            "__name__": node.name,
            "__qualname__": node.name,
            # El docstring de la clase, como en Python.
            "__doc__": node.doc,
        }
        clase = CruthonClass(node.name, bases, namespace, blob, bases_py)
        # El cuerpo de la clase se ejecuta en su propio espacio de nombres,
        # como en Python: lo que se define ahí son atributos de la clase.
        class_frame = Frame(blob=blob, parent=frame)
        class_frame.is_class_body = True
        class_frame.owner_class = clase
        class_frame.locals.update(namespace)
        self.exec_block(node.body, blob, class_frame)
        namespace.update(class_frame.locals)
        for clave, valor in list(namespace.items()):
            if isinstance(valor, CruthonFunction):
                # Las funciones del cuerpo de la clase son métodos: se
                # buscan en el MRO de la clase y se ligan a la instancia.
                namespace[clave] = CruthonMethod(valor, clase)
        clase = self._aplicar_decoradores_clase(clase, node, blob, frame)
        self._bind(node.name, clase, blob, frame)
        return None

    def _aplicar_decoradores_clase(self, clase: CruthonClass, node, blob, frame) -> Any:
        """Aplica los decoradores de una clase, de abajo hacia arriba."""
        for decorator in reversed(node.decorators or []):
            clase = self._aplicar_decorador(decorator, clase, blob, frame)
        return clase

    def _exec_with(self, node, blob, frame):
        """Ejecuta ``with``: uno o varios gestores de contexto."""
        gestores: List[Any] = []
        try:
            for item in node.items:
                contexto = self.eval(item.context_expr, blob, frame)
                entrar = self._metodo_especial(contexto, "__enter__")
                salir = self._metodo_especial(contexto, "__exit__")
                if entrar is None or salir is None:
                    raise CruthonRuntimeError(
                        "el objeto no admite el protocolo de gestor de "
                        f"contexto: {type(contexto).__name__}"
                    )
                valor = entrar()
                gestores.append((contexto, salir))
                if item.optional_vars is not None:
                    self.assign(item.optional_vars, valor, blob, frame)
            self.exec_block(node.body, blob, frame)
        except (BreakSignal, ContinueSignal, ReturnSignal) as control:
            # Un ``break``, ``continue`` o ``return`` cierra los gestores
            # como si el cuerpo hubiera terminado bien.
            self._cerrar_gestores(gestores, None)
            raise control
        except BaseException as error:  # noqa: BLE001 - se relanza si no casa
            if not self._cerrar_gestores(gestores, error):
                raise
            return None
        self._cerrar_gestores(gestores, None)
        return None

    @staticmethod
    def _cerrar_gestores(gestores: List[Any], error: Optional[BaseException]) -> bool:
        """Cierra los gestores de contexto; dice si el error quedó suprimido."""
        suprimido = False
        while gestores:
            contexto, salir = gestores.pop()
            if error is None:
                salir(None, None, None)
                continue
            if salir(type(error), error, error.__traceback__) and not suprimido:
                suprimido = True
                error = None
        return suprimido

    @staticmethod
    def _metodo_especial(obj: Any, nombre: str) -> Any:
        """Busca un método especial, como ``__enter__`` o ``__exit__``.

        En Python los métodos especiales se buscan en el tipo. Para una
        instancia de Cruthon, el equivalente es el MRO de su clase. Para un
        objeto de Python, se busca el método *ligado* a la instancia: pedirlo
        en el tipo devolvería el método sin ligar, que no sabe a qué objeto
        pertenece (``file.__enter__`` sin argumento no funciona).
        """
        if isinstance(obj, CruthonInstance):
            encontrado = buscar_metodo(obj, nombre)
            return None if encontrado is AUSENTE else encontrado
        return getattr(obj, nombre, None)

    def _exec_raise(self, node, blob, frame):
        """Ejecuta ``raise``, ``raise Excepción`` y ``raise Excepción desde ...``."""
        if node.exc is None:
            # ``raise`` a secas: vuelve a lanzar la excepción que se está
            # manejando en este momento.
            if self.excepcion_actual is None:
                raise CruthonRuntimeError("no hay ninguna excepción activa que relanzar")
            raise self.excepcion_actual
        excepcion = self.eval(node.exc, blob, frame)
        if isinstance(excepcion, CruthonInstance):
            # Una clase que hereda de una excepción de Python
            # (``class E(ValueError):``) se apoya en una excepción nativa:
            # la que se lanza es esa, para que ``except ValueError`` la
            # capture como en Python.
            base = excepcion.base_obj
            if isinstance(base, BaseException):
                try:
                    object.__setattr__(base, "__cruthon_clase__", excepcion.clase)
                except Exception:  # pragma: no cover - algunas excepciones no lo admiten
                    pass
                excepcion = base
        elif isinstance(excepcion, type) and issubclass(excepcion, BaseException):
            # ``raise StopIteration`` (sin paréntesis) lanza la clase, y
            # Python la instancia sola.
            excepcion = excepcion()
        if not isinstance(excepcion, BaseException):
            raise CruthonRuntimeError(
                "las excepciones deben derivar de BaseException, no de "
                f"{type(excepcion).__name__}"
            )
        if node.cause is not None:
            causa = self.eval(node.cause, blob, frame)
            excepcion.__cause__ = causa
        self.excepcion_actual = excepcion
        raise excepcion

    def _exec_return(self, node, blob, frame):
        value = None if node.value is None else self.eval(node.value, blob, frame)
        raise ReturnSignal(value)

    def _exec_pass(self, node, blob, frame):
        return None

    def _exec_break(self, node, blob, frame):
        raise BreakSignal()

    def _exec_continue(self, node, blob, frame):
        raise ContinueSignal()

    def _exec_if(self, node, blob, frame):
        if self.eval(node.test, blob, frame):
            return self.exec_block(node.body, blob, frame)
        return self.exec_block(node.orelse, blob, frame)

    def _exec_while(self, node, blob, frame):
        value = None
        while self.eval(node.test, blob, frame):
            try:
                value = self.exec_block(node.body, blob, frame)
            except BreakSignal:
                break
            except ContinueSignal:
                continue
        else:
            value = self.exec_block(node.orelse, blob, frame)
        return value

    def _exec_for(self, node, blob, frame):
        iterable = self.eval(node.iter, blob, frame)
        value = None
        broke = False
        for item in iterable:
            self.assign(node.target, item, blob, frame)
            try:
                value = self.exec_block(node.body, blob, frame)
            except BreakSignal:
                broke = True
                break
            except ContinueSignal:
                continue
        if not broke:
            value = self.exec_block(node.orelse, blob, frame)
        return value

    def _exec_import(self, node, blob, frame):
        for alias in node.names:
            module = importlib.import_module(alias.name)
            if alias.asname:
                name = alias.asname
            elif "." in alias.name:
                # ``import a.b`` enlaza el paquete de primer nivel, como en
                # Python: ``a`` queda bound al paquete, y ``a.b`` sigue
                # siendo accesible porque el sub-módulo queda como atributo
                # del paquete.
                name = alias.name.split(".")[0]
                module = importlib.import_module(name)
            else:
                name = alias.name
            self._bind(name, module, blob, frame)
        return None

    def _exec_import_from(self, node, blob, frame):
        module = importlib.import_module(node.module)
        for alias in node.names:
            if alias.name == "*":
                for key in dir(module):
                    if not key.startswith("_"):
                        self._bind(key, getattr(module, key), blob, frame)
                continue
            value = getattr(module, alias.name)
            self._bind(alias.asname or alias.name, value, blob, frame)
        return None

    def _exec_delete(self, node, blob, frame):
        for target in node.targets:
            if isinstance(target, N.Name):
                if frame is not None and target.id in frame.locals:
                    del frame.locals[target.id]
                    continue
                if blob.lookup(target.id) is not None:
                    ref = blob.lookup(target.id)
                    assert ref is not None
                    if ref.readonly:
                        raise ScopeError(
                            f"no se puede eliminar '{target.id}': es una vista de solo lectura"
                        )
                    ref.blob.storage.pop(target.id, None)
                    continue
                raise NameNotFound(f"nombre no definido: {target.id!r}")
            if isinstance(target, N.Attribute):
                value = self.eval(target.value, blob, frame)
                if isinstance(value, Blob):
                    value.storage.pop(target.attr, None)
                else:
                    delattr(value, target.attr)
                continue
            if isinstance(target, N.Subscript):
                value = self.eval(target.value, blob, frame)
                index = self.eval(target.slice, blob, frame)
                del value[index]
                continue
        return None

    def _exec_global(self, node, blob, frame):
        if frame is not None:
            frame.globals.update(node.names)
        return None

    def _exec_nonlocal(self, node, blob, frame):
        if frame is None:
            return None
        for name in node.names:
            if not self._nonlocal_existe(name, frame):
                raise NameNotFound(
                    f"no hay binding para 'nonlocal {name}' en ningún ámbito externo"
                )
            frame.nonlocals.add(name)
        return None

    @staticmethod
    def _nonlocal_existe(name: str, frame: Frame) -> bool:
        """¿El nombre vive en un marco de función externo?"""
        current = frame.parent
        while current is not None:
            if name in current.locals:
                return True
            current = current.parent
        return False

    def _exec_assert(self, node, blob, frame):
        if not self.eval(node.test, blob, frame):
            message = "assertion failed"
            if node.msg is not None:
                message = self.eval(node.msg, blob, frame)
            raise AssertionError(message)
        return None

    @staticmethod
    def _excepcion_casa(error: BaseException, esperado: Any) -> bool:
        """¿La excepción casa con el tipo (o tupla de tipos) del ``except``?

        Igual que en Python, ``except (TypeError, ValueError)`` acepta
        cualquiera de los dos; una tupla de tipos no es un ``type``, así que
        no se puede pasar tal cual a ``isinstance``.
        """
        if isinstance(esperado, CruthonClass):
            # Una clase de Cruthon que hereda de una excepción de Python:
            # la excepción nativa lleva el rastro de su clase.
            clase = getattr(error, "__cruthon_clase__", None)
            if clase is None:
                return False
            return any(candidata is esperado for candidata in clase.mro)
        if isinstance(esperado, tuple):
            return any(
                isinstance(candidato, type) and isinstance(error, candidato)
                for candidato in esperado
            )
        if isinstance(esperado, type):
            return isinstance(error, esperado)
        return False

    def _exec_try(self, node, blob, frame):
        try:
            try:
                self.exec_block(node.body, blob, frame)
            except (BreakSignal, ContinueSignal, ReturnSignal):
                raise
            # Se atrapa ``BaseException`` para que un ``except
            # KeyboardInterrupt`` escrito en Cruthon pueda despedirse en vez
            # de morir con una traza. ``except Exception`` sigue sin
            # capturarla, porque el que decide es el handler, no esta línea:
            # si ninguno casa, la excepción se relanza tal cual.
            except BaseException as error:  # noqa: BLE001 - se re-lanza si no casa
                handled = False
                anterior = self.excepcion_actual
                self.excepcion_actual = error
                try:
                    for handler in node.handlers:
                        if handler.type is None:
                            matched = True
                        else:
                            expected = self.eval(handler.type, blob, frame)
                            matched = self._excepcion_casa(error, expected)
                        if not matched:
                            continue
                        handled = True
                        if handler.name:
                            self._bind(handler.name, error, blob, frame)
                        self.exec_block(handler.body, blob, frame)
                        break
                finally:
                    self.excepcion_actual = anterior
                if not handled:
                    raise
            else:
                # La rama ``else`` de un ``try`` se ejecuta sólo cuando el
                # cuerpo no lanzó ninguna excepción, como en Python.
                self.exec_block(node.orelse, blob, frame)
        finally:
            if node.finalbody:
                self.exec_block(node.finalbody, blob, frame)
        return None

    _STATEMENTS = {
        N.ExprStmt: _exec_expr_stmt,
        N.Assign: _exec_assign,
        N.AugAssign: _exec_aug_assign,
        N.AnnAssign: _exec_ann_assign,
        N.BlobDecl: _exec_blob_decl,
        N.FuncDef: _exec_func_def,
        N.ClassDef: _exec_class_def,
        N.Return: _exec_return,
        N.Pass: _exec_pass,
        N.Break: _exec_break,
        N.Continue: _exec_continue,
        N.If: _exec_if,
        N.While: _exec_while,
        N.For: _exec_for,
        N.Import: _exec_import,
        N.ImportFrom: _exec_import_from,
        N.Delete: _exec_delete,
        N.Global: _exec_global,
        N.Nonlocal: _exec_nonlocal,
        N.Assert: _exec_assert,
        N.Try: _exec_try,
        N.Raise: _exec_raise,
        N.With: _exec_with,
    }

    # -- asignación -----------------------------------------------------
    def _bind(self, name: str, value: Any, blob: Blob, frame: Optional[Frame]) -> None:
        """Enlaza un nombre en el ámbito actual (función local o miembro del blob).

        En un ``exec``/``eval`` con diccionario propio, el nombre va al
        diccionario, como en Python.
        """
        if frame is not None:
            if name in frame.globals:
                if frame.dict_globals is not None:
                    frame.dict_globals[name] = value
                    return
                blob.define(name, value)
                return
            if frame.dict_locals is not None:
                frame.dict_locals[name] = value
                return
            if frame.dict_globals is not None:
                frame.dict_globals[name] = value
                return
            frame.locals[name] = value
            return
        blob.define(name, value)

    def assign(self, target: Any, value: Any, blob: Blob, frame: Optional[Frame]) -> None:
        if isinstance(target, N.Name):
            self.assign_name(target.id, value, blob, frame)
            return
        if isinstance(target, N.Attribute):
            obj = self.eval(target.value, blob, frame)
            self.set_attribute(obj, target.attr, value)
            return
        if isinstance(target, N.Subscript):
            obj = self.eval(target.value, blob, frame)
            index = self.eval(target.slice, blob, frame)
            obj[index] = value
            return
        if isinstance(target, (N.Tuple, N.List)):
            self._assign_sequence(target.elts, value, blob, frame)
            return
        raise CruthonRuntimeError("destino de asignación inválido")

    def _assign_sequence(self, elements, value, blob, frame) -> None:
        """Desempaqueta una secuencia sobre los destinos de una tupla/lista.

        Como en Python, un único destino con ``*resto`` captura el resto de
        los valores: ``a, *b = [1, 2, 3]`` deja ``b`` con ``[2, 3]``.
        """
        values = list(value)
        starred = [i for i, element in enumerate(elements) if isinstance(element, N.Starred)]
        if len(starred) > 1:
            raise CruthonRuntimeError("no puede haber más de un destino con '*'")
        if not starred:
            if len(values) != len(elements):
                raise CruthonRuntimeError(
                    f"se esperaban {len(elements)} valores y llegaron {len(values)}"
                )
            for element, item in zip(elements, values):
                self.assign(element, item, blob, frame)
            return
        index = starred[0]
        antes = index
        despues = len(elements) - index - 1
        if len(values) < antes + despues:
            raise CruthonRuntimeError(
                f"se esperaban al menos {antes + despues} valores y llegaron {len(values)}"
            )
        for element, item in zip(elements[:index], values[:index]):
            self.assign(element, item, blob, frame)
        resto = values[index : len(values) - despues]
        self.assign(elements[index].value, resto, blob, frame)
        for element, item in zip(elements[index + 1 :], values[len(values) - despues :]):
            self.assign(element, item, blob, frame)

    def assign_name(self, name: str, value: Any, blob: Blob, frame: Optional[Frame]) -> None:
        """Asigna un nombre simple respetando las reglas de ámbito por conjuntos."""
        if frame is not None:
            if name in frame.globals:
                if frame.dict_globals is not None:
                    # ``global x`` dentro de un ``exec`` con diccionario:
                    # la escritura va al diccionario de globales, como en
                    # Python.
                    frame.dict_globals[name] = value
                else:
                    self.assign_name_in(self.globals, name, value)
                return
            if name in frame.nonlocals:
                # ``nonlocal``: la escritura va al marco externo que lo declara.
                current: Optional[Frame] = frame.parent
                while current is not None:
                    if name in current.locals:
                        current.locals[name] = value
                        return
                    current = current.parent
            if frame.is_class_body:
                # El cuerpo de una clase enlaza sus nombres en el espacio de
                # nombres de la clase, como en Python.
                frame.locals[name] = value
                return
            if frame.dict_locals is not None or frame.dict_globals is not None:
                # Un ``exec``/``eval`` con diccionario propio: la escritura
                # va al diccionario, no al ámbito que lo rodea, como en
                # Python.
                destino = (
                    frame.dict_locals if frame.dict_locals is not None else frame.dict_globals
                )
                destino[name] = value  # type: ignore[index]
                return
            current = frame
            while current is not None:
                if name in current.locals:
                    current.locals[name] = value
                    return
                if frame.escritura_aislada:
                    # Un marco aislado (``exec``) no escribe ni en los
                    # locales de la función que lo rodea ni en los globales:
                    # sus nombres se pierden, como en Python.
                    frame.locals[name] = value
                    return
                current = current.parent
        ref = blob.lookup(name)
        if ref is None:
            if frame is not None:
                frame.locals[name] = value
            else:
                blob.define(name, value)
            return
        if ref.readonly:
            # Vista de solo lectura (unión/diferencia): la escritura se queda
            # en el blob actual, el operando no comparte memoria.
            blob.define(name, value)
            return
        ref.set(value)

    def assign_name_in(self, blob: Blob, name: str, value: Any) -> None:
        ref = blob.lookup(name)
        if ref is None or ref.readonly:
            blob.define(name, value)
        else:
            ref.set(value)

    # -- atributos ------------------------------------------------------
    def get_attribute(self, obj: Any, attr: str) -> Any:
        if isinstance(obj, Blob):
            ref = obj.lookup(attr)
            if ref is None:
                raise ScopeError(
                    f"el blob '{obj.name}' no expone el miembro '{attr}': "
                    "no vive dentro de su área (error de alcance por conjuntos)"
                )
            return ref.get()
        return getattr(obj, attr)

    def set_attribute(self, obj: Any, attr: str, value: Any) -> None:
        if isinstance(obj, Blob):
            ref = obj.lookup(attr)
            if ref is None or ref.readonly:
                obj.define(attr, value)
            else:
                ref.set(value)
            return
        setattr(obj, attr, value)

    # -- evaluación de expresiones --------------------------------------
    def eval(self, node: Any, blob: Blob, frame: Optional[Frame]) -> Any:
        method = self._EXPRESSIONS.get(type(node))
        if method is None:
            raise CruthonRuntimeError(f"expresión no soportada: {type(node).__name__}")
        return method(self, node, blob, frame)

    def _eval_constant(self, node, blob, frame):
        return node.value

    def _eval_name(self, node, blob, frame):
        return self.lookup_name(node.id, blob, frame)

    def lookup_name(self, name: str, blob: Blob, frame: Optional[Frame]) -> Any:
        current = frame
        while current is not None:
            if current.dict_locals is not None and name in current.dict_locals:
                return current.dict_locals[name]
            if current.dict_globals is not None and name in current.dict_globals:
                return current.dict_globals[name]
            if name in current.locals:
                return current.locals[name]
            current = current.parent
        if frame is not None and name in frame.globals:
            ref = self.globals.lookup(name)
            if ref is not None:
                return ref.get()
        if name == "super" and frame is not None and frame.owner_class is not None:
            # ``super()`` sólo existe dentro de un método: necesita saber la
            # clase y la instancia del método en curso.
            return SuperFabrica(frame.owner_class, frame.owner_instance)
        ref = blob.lookup(name)
        if ref is not None:
            return ref.get()
        if name == "globals":
            # El "global" de Cruthon es el área del blob donde corre el
            # código: sus miembros, y no los de los blobs hermanos. Como en
            # Python, el diccionario incluye ``__name__`` y ``__doc__``.
            def globals_() -> Dict[str, Any]:
                espacio = {n: celda.value for n, celda in blob.storage.items()}
                espacio.setdefault("__name__", blob.name)
                espacio.setdefault("__doc__", self.doc_modulo)
                return espacio

            return globals_
        if name == "locals":
            # En una función, los locales son los de su marco; fuera de una
            # función, el local es el propio blob.
            if frame is not None:
                return lambda: dict(frame.locals)
            return lambda: {n: celda.value for n, celda in blob.storage.items()}
        if name == "__name__":
            # El nombre del módulo: en Cruthon, el del blob donde se ejecuta
            # el código, que es su ámbito.
            return blob.name
        if name == "__doc__":
            # El docstring del módulo, como en Python.
            return self.doc_modulo
        if name in ("eval", "exec"):
            return self._builtin_de_codigo(name, blob, frame)
        if name in self.builtins:
            return self.builtins[name]
        raise NameNotFound(
            f"nombre no definido: {name!r} (no vive dentro del área del blob "
            f"'{blob.name}')"
        )

    # -- builtins que dependen del ámbito --------------------------------
    def _builtin_de_codigo(self, name: str, blob: Blob, frame: Optional[Frame]) -> Any:
        """``eval`` y ``exec``: el código se evalúa donde se escribe.

        A diferencia del resto de builtins, estos dos dependen del ámbito
        del punto de llamada: el código se evalúa en el blob y el marco
        donde aparecen, no en un ámbito global cualquiera.
        """
        if name == "eval":

            def eval_(source: Any, globals: Any = None, locals: Any = None) -> Any:
                """Evalúa una expresión de Cruthon en el ámbito actual."""
                marco = self._marco_de(globals, locals, blob, frame)
                return self.eval(self._expresion_de(source), blob, marco)

            return eval_

        def exec_(source: Any, globals: Any = None, locals: Any = None) -> Any:
            """Ejecuta código de Cruthon en el ámbito actual."""
            marco = self._marco_de(globals, locals, blob, frame)
            if marco is frame and frame is not None:
                # Como en Python, ``exec`` dentro de una función no añade
                # nombres a sus locales: el código corre en un marco aparte,
                # aunque sí ve los locales de la función.
                marco = Frame(blob=blob, parent=frame)
                marco.escritura_aislada = True
            self.exec_block(self._modulo_de(source).body, blob, marco)
            return None

        return exec_

    def _marco_de(
        self, globals: Any, locals: Any, blob: Blob, frame: Optional[Frame]
    ) -> Optional[Frame]:
        """El marco en el que se evalúa ``eval``/``exec``.

        Sin ``globals`` ni ``locals`` es el marco de la llamada. Con ellos,
        un marco nuevo que usa los diccionarios dados como su ámbito, como
        en Python: en ``exec(codigo, g)`` las escrituras van a ``g`` y en
        ``exec(codigo, g, l)`` van a ``l``; las lecturas miran primero ``l``
        y después ``g``.
        """
        if globals is None and locals is None:
            return frame
        marco = Frame(blob=blob, parent=frame)
        if isinstance(globals, dict):
            marco.dict_globals = globals
        if isinstance(locals, dict):
            marco.dict_locals = locals
        elif isinstance(globals, dict):
            # ``exec(codigo, g)``: el mismo diccionario es el ámbito global
            # y el local, como en Python.
            marco.dict_locals = globals
        return marco

    def _expresion_de(self, source: Any) -> Any:
        """Analiza el texto de una expresión, para ``eval``."""
        if not isinstance(source, str):
            raise TypeError("eval() espera una cadena")
        parser = Parser(tokenize(source, "<eval>"), "<eval>")
        nodo = parser.parse_test()
        token = parser.peek()
        if token.type not in (TokenType.NEWLINE, TokenType.ENDMARKER):
            raise CruthonSyntaxError(
                "eval() sólo admite una expresión", "<eval>", token.lineno
            )
        return nodo

    def _modulo_de(self, source: Any) -> N.Module:
        """Analiza el texto de un módulo, para ``exec``."""
        if not isinstance(source, str):
            raise TypeError("exec() espera una cadena")
        return parse(source, "<exec>")

    def _eval_attribute(self, node, blob, frame):
        obj = self.eval(node.value, blob, frame)
        return self.get_attribute(obj, node.attr)

    def _eval_subscript(self, node, blob, frame):
        obj = self.eval(node.value, blob, frame)
        index = self.eval(node.slice, blob, frame)
        return obj[index]

    def _eval_slice(self, node, blob, frame):
        lower = None if node.lower is None else self.eval(node.lower, blob, frame)
        upper = None if node.upper is None else self.eval(node.upper, blob, frame)
        step = None if node.step is None else self.eval(node.step, blob, frame)
        return slice(lower, upper, step)

    def _eval_call(self, node, blob, frame):
        func = self.eval(node.func, blob, frame)
        args: List[Any] = []
        for argument in node.args:
            if isinstance(argument, N.Starred):
                # ``f(*seq)``: la secuencia se expande en argumentos
                # posicionales, como en Python.
                args.extend(self.eval(argument.value, blob, frame))
                continue
            args.append(self.eval(argument, blob, frame))
        kwargs = {}
        for keyword in node.keywords:
            value = self.eval(keyword.value, blob, frame)
            if keyword.arg is None:
                # ``f(**d)``: el diccionario se expande en argumentos por
                # palabra clave.
                if not isinstance(value, dict):
                    raise CruthonRuntimeError(
                        "los argumentos **kwargs deben ser un diccionario"
                    )
                kwargs.update(value)
                continue
            kwargs[keyword.arg] = value
        return self.call(func, args, kwargs)

    def call(self, func: Any, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        if isinstance(func, CruthonFunction):
            return self.call_function(func, args, kwargs)
        if isinstance(func, CruthonClass):
            return self.instantiate(func, args, kwargs)
        if isinstance(func, Blob):
            raise ScopeError("un blob no es invocable")
        return func(*args, **kwargs)

    def instantiate(
        self, clase: CruthonClass, args: List[Any], kwargs: Dict[str, Any]
    ) -> CruthonInstance:
        """Crea una instancia de ``clase`` y llama a su ``__init__``."""
        inicializador = clase.lookup("__init__")
        base = None
        if clase.bases_py:
            # La clase hereda de un tipo de Python: la instancia se apoya en
            # un objeto de ese tipo. Si la propia clase define ``__init__``,
            # los argumentos son para él y el objeto nativo nace vacío; si no,
            # los argumentos inicializan el tipo nativo, como en Python.
            tipo_base = clase.bases_py[0]
            if inicializador is AUSENTE:
                base = tipo_base(*args, **kwargs)
            else:
                base = tipo_base()
        instancia = CruthonInstance(clase, base)
        if inicializador is AUSENTE:
            return instancia
        if isinstance(inicializador, CruthonMethod):
            inicializador.ligar(instancia)(*args, **kwargs)
        else:
            inicializador(*args, **kwargs)
        return instancia

    def call_method(
        self,
        function: CruthonFunction,
        instancia: CruthonInstance,
        args: List[Any],
        kwargs: Dict[str, Any],
    ) -> Any:
        """Llama a un método con la instancia ya enlazada como ``self``."""
        return self._ejecutar_funcion(
            function,
            [instancia] + list(args),
            kwargs,
            owner_class=function.owner_class,
            owner_instance=instancia,
        )

    def call_function(
        self, function: CruthonFunction, args: List[Any], kwargs: Dict[str, Any]
    ) -> Any:
        """Llama a una función con el ámbito del blob donde fue definida."""
        if function.is_generator:
            # Una función generadora no se ejecuta al llamarla: devuelve el
            # generador, que va avanzando bajo demanda.
            return CruthonGenerator(self, function, args, kwargs)
        owner_class = function.owner_class
        owner_instance = None
        if owner_class is not None and args:
            # Un descriptor (una ``property``) llama a su función con la
            # instancia como primer argumento: esa instancia es la del
            # método en curso, y es lo que ``super()`` necesita.
            candidato = args[0]
            if isinstance(candidato, CruthonInstance):
                owner_instance = candidato
        return self._ejecutar_funcion(
            function,
            args,
            kwargs,
            owner_class=owner_class,
            owner_instance=owner_instance,
        )

    def _ejecutar_funcion(
        self,
        function: CruthonFunction,
        args: List[Any],
        kwargs: Dict[str, Any],
        owner_class: Optional[CruthonClass] = None,
        owner_instance: Optional[CruthonInstance] = None,
        generator: Optional[CruthonGenerator] = None,
    ) -> Any:
        """Ejecuta el cuerpo de una función y devuelve lo que devuelve."""
        # El marco nuevo cuelga del marco donde se definió la función: así una
        # función anidada ve las variables locales de su entorno.
        frame = Frame(blob=function.blob, parent=function.closure)
        frame.owner_class = owner_class
        frame.owner_instance = owner_instance
        frame.generator = generator
        spec = function.args
        names = [arg.name for arg in spec.args]
        if len(args) > len(names) and spec.vararg is None:
            raise CruthonTypeError(
                f"{function.name}() takes {len(names)} positional "
                f"argument{'' if len(names) == 1 else 's'} but {len(args)} "
                "were given"
            )
        # Los valores por defecto se alinean con los últimos parámetros,
        # como en Python: ``def f(a, b=1)`` da ``b`` el valor por defecto.
        por_defecto: Dict[str, Any] = {}
        if spec.defaults:
            for arg, default in zip(
                spec.args[len(spec.args) - len(spec.defaults) :], spec.defaults
            ):
                por_defecto[arg.name] = default
        faltantes: List[str] = []
        for index, name in enumerate(names):
            if index < len(args):
                frame.locals[name] = args[index]
            elif name in kwargs:
                if spec.args[index].posonly:
                    # Un parámetro sólo posicional no se puede pasar por
                    # palabra clave, como en Python.
                    raise CruthonTypeError(
                        f"{function.name}() got some positional-only arguments "
                        f"passed as keyword arguments: '{name}'"
                    )
                frame.locals[name] = kwargs.pop(name)
            elif name in por_defecto:
                frame.locals[name] = function.defaults_valorados[name]
            else:
                faltantes.append(name)
        if faltantes:
            raise CruthonTypeError(
                self._mensaje_de_faltantes(function.name, "positional", faltantes)
            )
        if spec.vararg:
            frame.locals[spec.vararg] = tuple(args[len(names) :])
        for arg in spec.kwonlyargs:
            if arg.name in kwargs:
                frame.locals[arg.name] = kwargs.pop(arg.name)
            elif arg.default is not None:
                frame.locals[arg.name] = function.defaults_valorados[arg.name]
            else:
                raise CruthonTypeError(
                    self._mensaje_de_faltantes(
                        function.name, "keyword-only", [arg.name]
                    )
                )
        if spec.kwarg:
            # ``**kwargs``: los argumentos sobrantes se recogen en un
            # diccionario, como en Python.
            frame.locals[spec.kwarg] = dict(kwargs)
        else:
            for name in kwargs:
                if name not in frame.locals:
                    raise CruthonTypeError(
                        f"{function.name}() got an unexpected keyword argument "
                        f"'{name}'"
                    )
        try:
            self.exec_block(function.body, function.blob, frame)
        except ReturnSignal as signal:
            return signal.value
        return None

    @staticmethod
    def _mensaje_de_faltantes(
        nombre: str, clase: str, faltantes: List[str]
    ) -> str:
        """El mensaje de error de los argumentos que faltan, como en CPython.

        ``f() missing 1 required positional argument: 'a'`` y
        ``f() missing 2 required positional arguments: 'a' and 'b'``.
        """
        if len(faltantes) == 1:
            return (
                f"{nombre}() missing 1 required {clase} argument: "
                f"'{faltantes[0]}'"
            )
        nombres = " and ".join(f"'{falta}'" for falta in faltantes)
        return f"{nombre}() missing {len(faltantes)} required {clase} arguments: {nombres}"

    def _eval_binop(self, node, blob, frame):
        left = self.eval(node.left, blob, frame)
        right = self.eval(node.right, blob, frame)
        if node.op in SET_OPERATIONS:
            resultado = self._operacion_conjuntos(node.op, left, right)
            if resultado is not None:
                return resultado
        return _BIN_OPS[node.op](left, right)

    def _eval_blob_source(self, source, name, blob, frame):
        """Evalúa el operando de una declaración ``blob nombre = ...``.

        La operación de conjuntos se resuelve aquí, y no en el evaluador
        genérico de operadores, porque necesita el nombre final del blob
        (``blob c = a & b:``) para registrarlo en sus dos operandos.
        """
        if isinstance(source, N.BinOp) and source.op in SET_OPERATIONS:
            left = self.eval(source.left, blob, frame)
            right = self.eval(source.right, blob, frame)
            resultado = self._operacion_conjuntos(source.op, left, right, name)
            if resultado is not None:
                return resultado
            return _BIN_OPS[source.op](left, right)
        return self.eval(source, blob, frame)

    def _eval_unaryop(self, node, blob, frame):
        value = self.eval(node.operand, blob, frame)
        if node.op == "-":
            return -value
        if node.op == "+":
            return +value
        if node.op == "~":
            return ~value
        return not value

    def _eval_boolop(self, node, blob, frame):
        if node.op == "and":
            result: Any = True
            for value_node in node.values:
                result = self.eval(value_node, blob, frame)
                if not result:
                    return result
            return result
        result = False
        for value_node in node.values:
            result = self.eval(value_node, blob, frame)
            if result:
                return result
        return result

    def _eval_compare(self, node, blob, frame):
        left = self.eval(node.left, blob, frame)
        result = True
        for op, comparator in zip(node.ops, node.comparators):
            right = self.eval(comparator, blob, frame)
            if op == "in":
                ok = left in right
            elif op == "not in":
                ok = left not in right
            elif op == "is":
                ok = left is right
            elif op == "is not":
                ok = left is not right
            else:
                ok = _COMPARE_OPS[op](left, right)
            if not ok:
                return False
            left = right
        return result

    def _eval_ifexp(self, node, blob, frame):
        if self.eval(node.test, blob, frame):
            return self.eval(node.body, blob, frame)
        return self.eval(node.orelse, blob, frame)

    def _eval_tuple(self, node, blob, frame):
        elementos: List[Any] = []
        for element in node.elts:
            if isinstance(element, N.Starred):
                elementos.extend(self.eval(element.value, blob, frame))
            else:
                elementos.append(self.eval(element, blob, frame))
        return tuple(elementos)

    def _eval_list(self, node, blob, frame):
        elementos: List[Any] = []
        for element in node.elts:
            if isinstance(element, N.Starred):
                elementos.extend(self.eval(element.value, blob, frame))
            else:
                elementos.append(self.eval(element, blob, frame))
        return elementos

    def _eval_set(self, node, blob, frame):
        elementos: set = set()
        for element in node.elts:
            if isinstance(element, N.Starred):
                elementos.update(self.eval(element.value, blob, frame))
            else:
                elementos.add(self.eval(element, blob, frame))
        return elementos

    def _eval_dict(self, node, blob, frame):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
            if isinstance(key_node, N.Starred):
                # ``{**otro}``: se actualiza con el diccionario expandido.
                extra = self.eval(key_node.value, blob, frame)
                if not isinstance(extra, dict):
                    raise CruthonRuntimeError(
                        "la expansión de un diccionario debe ser un diccionario"
                    )
                result.update(extra)
                continue
            result[self.eval(key_node, blob, frame)] = self.eval(value_node, blob, frame)
        return result

    def _eval_listcomp(self, node, blob, frame):
        values = []
        for generators, _ in self._iterate_comprehension(node.generators, blob, frame):
            values.append(self.eval(node.elt, blob, generators))
        return values

    def _eval_setcomp(self, node, blob, frame):
        values = []
        for generators, _ in self._iterate_comprehension(node.generators, blob, frame):
            values.append(self.eval(node.elt, blob, generators))
        return set(values)

    def _eval_dictcomp(self, node, blob, frame):
        result = {}
        for generators, _ in self._iterate_comprehension(node.generators, blob, frame):
            result[self.eval(node.key, blob, generators)] = self.eval(
                node.value, blob, generators
            )
        return result

    def _eval_generator(self, node, blob, frame):
        """Evalúa una expresión generadora y devuelve su generador."""
        return CruthonGenexp(self._iterar_generador(node, blob, frame))

    def _iterar_generador(self, node, blob, frame):
        for generators, _ in self._iterate_comprehension(node.generators, blob, frame):
            yield self.eval(node.elt, blob, generators)

    def _iterate_comprehension(self, generators, blob, frame):
        """Itera los generadores en cascada, entrega (marco, valor)."""
        def recurse(index, current_frame):
            if index == len(generators):
                yield current_frame, None
                return
            generator = generators[index]
            iterable = self.eval(generator.iter, blob, current_frame)
            for item in iterable:
                child = Frame(blob=blob, parent=current_frame)
                self.assign(generator.target, item, blob, child)
                if all(self.eval(condition, blob, child) for condition in generator.ifs):
                    yield from recurse(index + 1, child)

        for result in recurse(0, frame):
            yield result

    def _eval_lambda(self, node, blob, frame):
        spec = node.args
        arguments = N.Arguments(
            args=spec.args, vararg=spec.vararg, kwonlyargs=spec.kwonlyargs,
            kwarg=spec.kwarg, defaults=spec.defaults,
        )
        body = [N.Return(value=node.body)]
        function = CruthonFunction("<lambda>", arguments, body, blob, None, self)
        # Los valores por defecto se evalúan al crear el ``lambda``, como
        # en Python: ``lambda x=[]: x`` comparte la misma lista.
        function.defaults_valorados = self._valores_por_defecto(arguments, blob, frame)
        return function

    def _eval_namedexpr(self, node, blob, frame):
        """Evalúa ``(x := valor)``: asigna y devuelve el valor."""
        value = self.eval(node.value, blob, frame)
        self.assign_name(node.target.id, value, blob, frame)
        return value

    def _eval_yield(self, node, blob, frame):
        """Evalúa ``yield valor`` y ``yield from iterable``."""
        generador = frame.generator if frame is not None else None
        if generador is None:
            raise CruthonRuntimeError(
                "'yield' fuera de una función generadora"
            )
        if node.delegate:
            for item in self.eval(node.value, blob, frame):
                generador.emitir(item)
            return None
        value = None if node.value is None else self.eval(node.value, blob, frame)
        # Lo que el consumidor envíe vuelve como valor de la expresión
        # ``yield``: ``x = yield 1`` recibe lo que se le manda.
        return generador.emitir(value)

    def _eval_joinedstr(self, node, blob, frame):
        pieces = []
        for value_node in node.values:
            pieces.append(str(self.eval(value_node, blob, frame)))
        return "".join(pieces)

    def _eval_formattedvalue(self, node, blob, frame):
        value = self.eval(node.value, blob, frame)
        if node.conversion == "r":
            value = repr(value)
        elif node.conversion == "s":
            value = str(value)
        elif node.conversion == "a":
            value = ascii(value)
        if node.format_spec is not None:
            spec = self.eval(node.format_spec, blob, frame)
            value = format(value, spec)
        else:
            value = str(value)
        if node.debug:
            # ``f"{x=}"``: el texto de la expresión, el ``=`` y el valor,
            # como en Python.
            return node.debug + value
        return value

    _EXPRESSIONS = {
        N.Constant: _eval_constant,
        N.Name: _eval_name,
        N.Attribute: _eval_attribute,
        N.Subscript: _eval_subscript,
        N.Slice: _eval_slice,
        N.Call: _eval_call,
        N.BinOp: _eval_binop,
        N.UnaryOp: _eval_unaryop,
        N.BoolOp: _eval_boolop,
        N.Compare: _eval_compare,
        N.IfExp: _eval_ifexp,
        N.Tuple: _eval_tuple,
        N.List: _eval_list,
        N.Set: _eval_set,
        N.Dict: _eval_dict,
        N.ListComp: _eval_listcomp,
        N.SetComp: _eval_setcomp,
        N.DictComp: _eval_dictcomp,
        N.GeneratorExp: _eval_generator,
        N.Lambda: _eval_lambda,
        N.JoinedStr: _eval_joinedstr,
        N.FormattedValue: _eval_formattedvalue,
        N.NamedExpr: _eval_namedexpr,
        N.Yield: _eval_yield,
    }

    # -- operaciones de blobs --------------------------------------------
    def _operacion_conjuntos(
        self, op: str, left: Any, right: Any, name: Optional[str] = None
    ) -> Optional[Blob]:
        """Aplica una operación de conjuntos si ambos operandos son blobs.

        Devuelve ``None`` si los operandos no son blobs: en ese caso la
        operación es la de Python (bit a bit, por ejemplo) y no de conjuntos.
        """
        if not isinstance(left, Blob) or not isinstance(right, Blob):
            return None
        return SET_FUNCTIONS[op](left, right, name)
