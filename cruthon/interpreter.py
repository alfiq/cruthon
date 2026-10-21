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

import builtins
import importlib
import operator
from typing import Any, Dict, List, Optional

from . import nodes as N
from .blobs import SET_FUNCTIONS, SET_OPERATIONS, Blob
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

    __slots__ = ("locals", "blob", "parent", "globals")

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


class CruthonFunction:
    """Función de Cruthon.

    Guarda el blob donde fue definida: ese es su ámbito, siempre, sin
    importar quién la llame.
    """

    __slots__ = ("name", "args", "body", "blob")

    def __init__(self, name: str, args: N.Arguments, body: List[Any], blob: Blob) -> None:
        self.name = name
        self.args = args
        self.body = body
        self.blob = blob

    def __repr__(self) -> str:
        return f"<func {self.name}>"


def _make_builtins() -> Dict[str, Any]:
    """Builtin de Python disponibles dentro de cualquier blob."""
    table = {
        "abs": builtins.abs,
        "all": builtins.all,
        "any": builtins.any,
        "bin": builtins.bin,
        "bool": builtins.bool,
        "callable": builtins.callable,
        "chr": builtins.chr,
        "dict": builtins.dict,
        "divmod": builtins.divmod,
        "enumerate": builtins.enumerate,
        "filter": builtins.filter,
        "float": builtins.float,
        "format": builtins.format,
        "getattr": builtins.getattr,
        "hasattr": builtins.hasattr,
        "hash": builtins.hash,
        "hex": builtins.hex,
        "id": builtins.id,
        "input": builtins.input,
        "int": builtins.int,
        "isinstance": builtins.isinstance,
        "issubclass": builtins.issubclass,
        "iter": builtins.iter,
        "len": builtins.len,
        "list": builtins.list,
        "map": builtins.map,
        "max": builtins.max,
        "min": builtins.min,
        "next": builtins.next,
        "oct": builtins.oct,
        "ord": builtins.ord,
        "print": builtins.print,
        "range": builtins.range,
        "repr": builtins.repr,
        "reversed": builtins.reversed,
        "round": builtins.round,
        "set": builtins.set,
        "setattr": builtins.setattr,
        "sorted": builtins.sorted,
        "str": builtins.str,
        "sum": builtins.sum,
        "tuple": builtins.tuple,
        "zip": builtins.zip,
        "delattr": builtins.delattr,
    }
    for name in (
        "ArithmeticError",
        "AssertionError",
        "AttributeError",
        "Exception",
        "IndexError",
        "KeyError",
        "NameError",
        "RuntimeError",
        "StopIteration",
        "TypeError",
        "ValueError",
        "ZeroDivisionError",
    ):
        table[name] = getattr(builtins, name)
    # Los errores propios de Cruthon también se pueden capturar.
    table.update(
        {
            "CruthonError": CruthonError,
            "CruthonRuntimeError": CruthonRuntimeError,
            "CruthonSyntaxError": CruthonSyntaxError,
            "ScopeError": ScopeError,
            "NameNotFound": NameNotFound,
            "BlobTypeError": BlobTypeError,
        }
    )
    return table


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
        self.builtins = _make_builtins()

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
        current = self.eval(_as_read(node.target), blob, frame)
        operand = self.eval(node.value, blob, frame)
        value = _BIN_OPS[node.op](current, operand)
        self.assign(node.target, value, blob, frame)
        return None

    def _exec_blob_decl(self, node, blob, frame):
        if node.source is None:
            new_blob = Blob(node.name, parent=blob)
        else:
            new_blob = self.eval_blob_source(node.source, node.name, blob, frame)
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
        function = CruthonFunction(node.name, node.args, node.body, blob)
        blob.define(node.name, function)
        return None

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
        raise CruthonRuntimeError("'nonlocal' no está soportado en Cruthon")

    def _exec_assert(self, node, blob, frame):
        if not self.eval(node.test, blob, frame):
            message = "assertion failed"
            if node.msg is not None:
                message = self.eval(node.msg, blob, frame)
            raise AssertionError(message)
        return None

    def _exec_try(self, node, blob, frame):
        try:
            try:
                self.exec_block(node.body, blob, frame)
            except (BreakSignal, ContinueSignal, ReturnSignal):
                raise
            except Exception as error:  # noqa: BLE001 - se re-lanza si no casa
                handled = False
                for handler in node.handlers:
                    if handler.type is None:
                        matched = True
                    else:
                        expected = self.eval(handler.type, blob, frame)
                        matched = isinstance(error, expected) if isinstance(
                            expected, type
                        ) else False
                    if not matched:
                        continue
                    handled = True
                    if handler.name:
                        self._bind(handler.name, error, blob, frame)
                    self.exec_block(handler.body, blob, frame)
                    break
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
            values = list(value)
            if len(values) != len(target.elts):
                raise CruthonRuntimeError(
                    f"se esperaban {len(target.elts)} valores y llegaron {len(values)}"
                )
            for element, item in zip(target.elts, values):
                self.assign(element, item, blob, frame)
            return
        raise CruthonRuntimeError("destino de asignación inválido")

    def assign_name(self, name: str, value: Any, blob: Blob, frame: Optional[Frame]) -> None:
        """Asigna un nombre simple respetando las reglas de ámbito por conjuntos."""
        if frame is not None:
            if name in frame.globals:
                self.assign_name_in(self.globals, name, value)
                return
            current: Optional[Frame] = frame
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
        args = [self.eval(argument, blob, frame) for argument in node.args]
        kwargs = {}
        for keyword in node.keywords:
            if keyword.arg is None:
                raise CruthonRuntimeError("los argumentos **kwargs no están soportados")
            kwargs[keyword.arg] = self.eval(keyword.value, blob, frame)
        return self.call(func, args, kwargs)

    def call(self, func: Any, args: List[Any], kwargs: Dict[str, Any]) -> Any:
        if isinstance(func, CruthonFunction):
            return self.call_function(func, args, kwargs)
        if isinstance(func, Blob):
            raise ScopeError("un blob no es invocable")
        return func(*args, **kwargs)

    def call_function(
        self, function: CruthonFunction, args: List[Any], kwargs: Dict[str, Any]
    ) -> Any:
        """Llama a una función con el ámbito del blob donde fue definida."""
        frame = Frame(blob=function.blob)
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
        for name in spec.kwonlyargs:
            if name in kwargs:
                frame.locals[name.name] = kwargs.pop(name)
        if spec.kwarg:
            frame.locals[spec.kwarg] = kwargs
        for name, value in kwargs.items():
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
        if node.op in SET_OPERATIONS and isinstance(left, Blob) and isinstance(right, Blob):
            return SET_FUNCTIONS[node.op](left, right)
        return _BIN_OPS[node.op](left, right)

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
        return tuple(self.eval(element, blob, frame) for element in node.elts)

    def _eval_list(self, node, blob, frame):
        return [self.eval(element, blob, frame) for element in node.elts]

    def _eval_set(self, node, blob, frame):
        return {self.eval(element, blob, frame) for element in node.elts}

    def _eval_dict(self, node, blob, frame):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
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
        return CruthonFunction("<lambda>", arguments, body, blob)

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
    }

    # -- operaciones de blobs --------------------------------------------
    def eval_blob_source(self, source: Any, name: str, blob: Blob, frame: Optional[Frame]) -> Any:
        """Evalúa el operando de una declaración ``blob nombre = ...``."""
        if isinstance(source, N.BinOp) and source.op in SET_OPERATIONS:
            left = self.eval(source.left, blob, frame)
            right = self.eval(source.right, blob, frame)
            if isinstance(left, Blob) and isinstance(right, Blob):
                return SET_FUNCTIONS[source.op](left, right, name)
        return self.eval(source, blob, frame)


def _as_read(node: Any) -> Any:
    """Convierte un destino de asignación en una expresión legible."""
    if isinstance(node, N.Name):
        return node
    return node
