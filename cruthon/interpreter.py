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
    buscar_metodo,
)
from .errors import (
    BlobTypeError,
    CruthonError,
    CruthonRuntimeError,
    CruthonSyntaxError,
    NameNotFound,
    ScopeError,
)


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
    )

    def __init__(
        self,
        name: str,
        args: N.Arguments,
        body: List[Any],
        blob: Blob,
        closure: Optional["Frame"] = None,
        interpreter: Optional["Interpreter"] = None,
        owner_class: Optional[CruthonClass] = None,
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

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.interpreter is None:  # pragma: no cover - defensivo
            raise CruthonRuntimeError(f"la función {self.name!r} no tiene intérprete")
        return self.interpreter.call_function(self, list(args), dict(kwargs))

    def __repr__(self) -> str:
        return f"<func {self.name}>"


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

    def emitir(self, valor: Any) -> None:
        """Entrega un valor y espera a que el consumidor pida el siguiente."""
        self._valores.put(valor)
        self._peticiones.get()

    def __iter__(self) -> "CruthonGenerator":
        return self

    def __next__(self) -> Any:
        if self._terminado:
            raise StopIteration(self._retorno)
        item = self._valores.get()
        if isinstance(item, _Fin):
            self._terminado = True
            if self._error is not None:
                error, self._error = self._error, None
                raise error
            raise StopIteration(self._retorno)
        self._peticiones.put(True)
        return item

    def close(self) -> None:
        """Cierra el generador: el hilo que lo ejecuta termina."""
        self._terminado = True
        self._peticiones.put(True)
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


class Interpreter:
    """Evalúa un módulo de Cruthon."""

    def __init__(self, filename: str = "<cruthon>") -> None:
        self.filename = filename
        self.globals = Blob("__main__")
        self.builtins = make_builtins()
        #: La excepción que se está manejando ahora mismo (para ``raise``
        #: a secas).
        self.excepcion_actual: Optional[BaseException] = None

    # -- programa -------------------------------------------------------
    def run(self, module: N.Module) -> Any:
        """Ejecuta un módulo y devuelve el valor de su última expresión."""
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
        value = _BIN_OPS[node.op](current, operand)
        self.assign(node.target, value, blob, frame)
        return None

    def _exec_blob_decl(self, node, blob, frame):
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
            node.name, node.args, node.body, blob, frame, self, owner_class
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
            frame.locals[node.name] = function
        else:
            blob.define(node.name, function)
        return None

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
            name = alias.asname or alias.name.split(".")[0]
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
            except Exception as error:  # noqa: BLE001 - se re-lanza si no casa
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
        finally:
            if node.finalbody:
                self.exec_block(node.finalbody, blob, frame)
        return None

    _STATEMENTS = {
        N.ExprStmt: _exec_expr_stmt,
        N.Assign: _exec_assign,
        N.AugAssign: _exec_aug_assign,
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
        """Enlaza un nombre en el ámbito actual (función local o miembro del blob)."""
        if frame is not None and name not in frame.globals:
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
            current = frame
            while current is not None:
                if name in current.locals:
                    current.locals[name] = value
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
        if name in self.builtins:
            return self.builtins[name]
        raise NameNotFound(
            f"nombre no definido: {name!r} (no vive dentro del área del blob "
            f"'{blob.name}')"
        )

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
        return self._ejecutar_funcion(function, args, kwargs)

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
            raise CruthonRuntimeError(
                f"la función {function.name!r} acepta {len(names)} argumentos "
                f"y recibió {len(args)}"
            )
        for index, name in enumerate(names):
            if index < len(args):
                frame.locals[name] = args[index]
            elif name in kwargs:
                frame.locals[name] = kwargs.pop(name)
            else:
                default_index = index - (len(names) - len(spec.defaults))
                if default_index < 0:
                    raise CruthonRuntimeError(
                        f"falta el argumento '{name}' de {function.name!r}"
                    )
                frame.locals[name] = self.eval(spec.defaults[default_index], function.blob, None)
        if spec.vararg:
            frame.locals[spec.vararg] = tuple(args[len(names) :])
        for arg in spec.kwonlyargs:
            if arg.name in kwargs:
                frame.locals[arg.name] = kwargs.pop(arg.name)
            elif arg.default is not None:
                frame.locals[arg.name] = self.eval(arg.default, function.blob, None)
            else:
                raise CruthonRuntimeError(
                    f"falta el argumento '{arg.name}' de {function.name!r}"
                )
        if spec.kwarg:
            # ``**kwargs``: los argumentos sobrantes se recogen en un
            # diccionario, como en Python.
            frame.locals[spec.kwarg] = dict(kwargs)
        else:
            for name in kwargs:
                if name not in frame.locals:
                    raise CruthonRuntimeError(
                        f"argumento inesperado '{name}' para {function.name!r}"
                    )
        try:
            self.exec_block(function.body, function.blob, frame)
        except ReturnSignal as signal:
            return signal.value
        return None

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
        return CruthonFunction("<lambda>", arguments, body, blob, None, self)

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
        generador.emitir(value)
        return None

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
            return format(value, spec)
        return str(value)

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
