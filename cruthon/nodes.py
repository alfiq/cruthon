"""Nodos del árbol de sintaxis abstracta de Cruthon.

Siguen el criterio de ``Python/ast.c``: objetos planos con atributos, sin
comportamiento, que el evaluador (``cruthon/interpreter.py``) recorre como un
intérprete de árbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List as ListType, Optional


@dataclass
class Node:
    lineno: int = 0


# ---------------------------------------------------------------- expresiones
@dataclass
class Constant(Node):
    value: Any = None


@dataclass
class Name(Node):
    id: str = ""


@dataclass
class Attribute(Node):
    value: Any = None
    attr: str = ""


@dataclass
class Subscript(Node):
    value: Any = None
    slice: Any = None


@dataclass
class Call(Node):
    func: Any = None
    args: ListType[Any] = field(default_factory=list)
    keywords: ListType[Any] = field(default_factory=list)


@dataclass
class Keyword(Node):
    arg: Optional[str] = None
    value: Any = None


@dataclass
class BinOp(Node):
    left: Any = None
    op: str = "+"
    right: Any = None


@dataclass
class UnaryOp(Node):
    op: str = "-"
    operand: Any = None


@dataclass
class BoolOp(Node):
    op: str = "and"
    values: ListType[Any] = field(default_factory=list)


@dataclass
class Compare(Node):
    left: Any = None
    ops: List[str] = field(default_factory=list)
    comparators: ListType[Any] = field(default_factory=list)


@dataclass
class IfExp(Node):
    test: Any = None
    body: Any = None
    orelse: Any = None


@dataclass
class Tuple(Node):
    elts: ListType[Any] = field(default_factory=list)


@dataclass
class List(Node):
    elts: ListType[Any] = field(default_factory=list)


@dataclass
class Set(Node):
    elts: ListType[Any] = field(default_factory=list)


@dataclass
class Dict(Node):
    keys: ListType[Any] = field(default_factory=list)
    values: ListType[Any] = field(default_factory=list)


@dataclass
class Slice(Node):
    lower: Any = None
    upper: Any = None
    step: Any = None


@dataclass
class CompGenerator(Node):
    target: Any = None
    iter: Any = None
    ifs: ListType[Any] = field(default_factory=list)


@dataclass
class ListComp(Node):
    elt: Any = None
    generators: ListType[Any] = field(default_factory=list)


@dataclass
class SetComp(Node):
    elt: Any = None
    generators: ListType[Any] = field(default_factory=list)


@dataclass
class DictComp(Node):
    key: Any = None
    value: Any = None
    generators: ListType[Any] = field(default_factory=list)


@dataclass
class GeneratorExp(Node):
    elt: Any = None
    generators: ListType[Any] = field(default_factory=list)


@dataclass
class Lambda(Node):
    args: Any = None
    body: Any = None


@dataclass
class FormattedValue(Node):
    value: Any = None
    conversion: Optional[str] = None
    format_spec: Optional[str] = None


@dataclass
class JoinedStr(Node):
    values: ListType[Any] = field(default_factory=list)


# --------------------------------------------------------------- declaraciones
@dataclass
class Module(Node):
    body: ListType[Any] = field(default_factory=list)


@dataclass
class ExprStmt(Node):
    value: Any = None


@dataclass
class Assign(Node):
    targets: ListType[Any] = field(default_factory=list)
    value: Any = None


@dataclass
class AugAssign(Node):
    target: Any = None
    op: str = "+"
    value: Any = None


@dataclass
class BlobDecl(Node):
    """``blob nombre:`` o ``blob nombre = a & b:``."""

    name: str = ""
    source: Any = None
    body: ListType[Any] = field(default_factory=list)


@dataclass
class FuncDef(Node):
    name: str = ""
    args: Any = None
    body: ListType[Any] = field(default_factory=list)
    decorators: ListType[Any] = field(default_factory=list)


@dataclass
class Arg(Node):
    name: str = ""
    default: Any = None


@dataclass
class Arguments(Node):
    args: ListType[Any] = field(default_factory=list)
    vararg: Optional[str] = None
    kwonlyargs: ListType[Any] = field(default_factory=list)
    kwarg: Optional[str] = None
    defaults: ListType[Any] = field(default_factory=list)


@dataclass
class Return(Node):
    value: Any = None


@dataclass
class Pass(Node):
    pass


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class If(Node):
    test: Any = None
    body: ListType[Any] = field(default_factory=list)
    orelse: ListType[Any] = field(default_factory=list)


@dataclass
class While(Node):
    test: Any = None
    body: ListType[Any] = field(default_factory=list)
    orelse: ListType[Any] = field(default_factory=list)


@dataclass
class For(Node):
    target: Any = None
    iter: Any = None
    body: ListType[Any] = field(default_factory=list)
    orelse: ListType[Any] = field(default_factory=list)


@dataclass
class Import(Node):
    names: ListType[Any] = field(default_factory=list)


@dataclass
class ImportFrom(Node):
    module: str = ""
    names: ListType[Any] = field(default_factory=list)
    level: int = 0


@dataclass
class ImportAlias(Node):
    name: str = ""
    asname: Optional[str] = None


@dataclass
class Delete(Node):
    targets: ListType[Any] = field(default_factory=list)


@dataclass
class Global(Node):
    names: List[str] = field(default_factory=list)


@dataclass
class Nonlocal(Node):
    names: List[str] = field(default_factory=list)


@dataclass
class Assert(Node):
    test: Any = None
    msg: Any = None


@dataclass
class Try(Node):
    body: ListType[Any] = field(default_factory=list)
    handlers: ListType[Any] = field(default_factory=list)
    orelse: ListType[Any] = field(default_factory=list)
    finalbody: ListType[Any] = field(default_factory=list)


@dataclass
class ExceptHandler(Node):
    type: Any = None
    name: Optional[str] = None
    body: ListType[Any] = field(default_factory=list)
