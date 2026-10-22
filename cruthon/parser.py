"""Analizador sintáctico de Cruthon.

Parser descendente recursivo, con la misma precedencia y estructura que el
parser de CPython (``Parser/parser.c``) para la parte que Cruthon comparte con
Python, más las dos construcciones propias del lenguaje:

* ``blob nombre:`` — declara un blob (un conjunto) y su cuerpo.
* ``blob nombre = a & b:`` — declara el blob resultante de una operación de
  conjuntos entre blobs ya existentes.
"""

from __future__ import annotations

from typing import Any, List, Optional

from . import nodes as N
from .errors import CruthonSyntaxError, IncompleteInput
from .lexer import Token, TokenType, decode_escapes, tokenize

#: Operadores de asignación aumentada.
AUG_ASSIGN_OPS = {
    "+=": "+",
    "-=": "-",
    "*=": "*",
    "/=": "/",
    "//=": "//",
    "%=": "%",
    "**=": "**",
    "&=": "&",
    "|=": "|",
    "^=": "^",
    "<<=": "<<",
    ">>=": ">>",
}

#: Operadores de comparación.
COMPARE_OPS = {"<", ">", "==", ">=", "<=", "!="}

#: Operaciones de conjuntos entre blobs.
SET_OPERATORS = {"&", "|", "-", "^"}


class Parser:
    """Convierte una lista de tokens en un árbol de sintaxis abstracta."""

    def __init__(self, tokens: List[Token], filename: str = "<cruthon>") -> None:
        self.tokens = tokens
        self.pos = 0
        self.filename = filename

    # -- utilidades sobre el flujo de tokens ---------------------------
    def peek(self, offset: int = 0) -> Token:
        index = self.pos + offset
        if index >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[index]

    def at(self, ttype: TokenType, value: Optional[str] = None) -> bool:
        token = self.peek()
        if token.type is not ttype:
            return False
        return value is None or token.value == value

    def at_keyword(self, *words: str) -> bool:
        token = self.peek()
        return token.type is TokenType.NAME and token.value in words

    def advance(self) -> Token:
        token = self.peek()
        if token.type is not TokenType.ENDMARKER:
            self.pos += 1
        return token

    def accept(self, ttype: TokenType, value: Optional[str] = None) -> Optional[Token]:
        if self.at(ttype, value):
            return self.advance()
        return None

    def expect(self, ttype: TokenType, value: Optional[str] = None) -> Token:
        if self.at(ttype, value):
            return self.advance()
        token = self.peek()
        if token.type is TokenType.ENDMARKER:
            raise IncompleteInput(
                "entrada incompleta (falta el final de la expresión)",
                self.filename,
                token.lineno,
            )
        expected = value if value is not None else ttype.name.lower()
        raise CruthonSyntaxError(
            f"se esperaba {expected!r} pero se encontró {token.value!r}",
            self.filename,
            token.lineno,
        )

    def error(self, message: str, token: Optional[Token] = None) -> CruthonSyntaxError:
        token = token or self.peek()
        return CruthonSyntaxError(message, self.filename, token.lineno)

    # -- programa ------------------------------------------------------
    def parse_module(self) -> N.Module:
        body: List[Any] = []
        while not self.at(TokenType.ENDMARKER):
            if self.at(TokenType.NEWLINE):
                self.advance()
                continue
            body.extend(self.parse_statement())
        return N.Module(body=body, lineno=1)

    # -- sentencias ----------------------------------------------------
    def parse_statement(self) -> List[Any]:
        token = self.peek()
        if token.type is TokenType.OP and token.value == "@":
            # Un decorador precede siempre a una ``def`` o a una ``class``.
            return [self.parse_decorated()]
        if token.type is TokenType.NAME:
            keyword = token.value
            if keyword == "blob":
                return [self.parse_blob_decl()]
            if keyword in ("func", "def"):
                return [self.parse_func_def()]
            if keyword == "if":
                return [self.parse_if()]
            if keyword == "while":
                return [self.parse_while()]
            if keyword == "for":
                return [self.parse_for()]
            if keyword == "try":
                return [self.parse_try()]
            if keyword == "class":
                return [self.parse_class_def()]
            if keyword == "with":
                return [self.parse_with()]
        return self.parse_simple_statement()

    def parse_simple_statement(self) -> List[Any]:
        statement = self.parse_small_statement()
        self.expect(TokenType.NEWLINE)
        return [statement]

    def parse_small_statement(self) -> Any:
        token = self.peek()
        if token.type is TokenType.NAME:
            keyword = token.value
            if keyword == "return":
                self.advance()
                value = None
                if not self.at(TokenType.NEWLINE):
                    value = self.parse_expr_list()
                return N.Return(value=value, lineno=token.lineno)
            if keyword == "pass":
                self.advance()
                return N.Pass(lineno=token.lineno)
            if keyword == "break":
                self.advance()
                return N.Break(lineno=token.lineno)
            if keyword == "continue":
                self.advance()
                return N.Continue(lineno=token.lineno)
            if keyword == "import":
                return self.parse_import()
            if keyword == "from":
                return self.parse_from_import()
            if keyword == "del":
                self.advance()
                targets = [self.parse_del_target()]
                while self.accept(TokenType.OP, ","):
                    targets.append(self.parse_del_target())
                return N.Delete(targets=targets, lineno=token.lineno)
            if keyword == "global":
                self.advance()
                names = [self.expect(TokenType.NAME).value]
                while self.accept(TokenType.OP, ","):
                    names.append(self.expect(TokenType.NAME).value)
                return N.Global(names=names, lineno=token.lineno)
            if keyword == "nonlocal":
                self.advance()
                names = [self.expect(TokenType.NAME).value]
                while self.accept(TokenType.OP, ","):
                    names.append(self.expect(TokenType.NAME).value)
                return N.Nonlocal(names=names, lineno=token.lineno)
            if keyword == "assert":
                self.advance()
                test = self.parse_test()
                msg = None
                if self.accept(TokenType.OP, ","):
                    msg = self.parse_test()
                return N.Assert(test=test, msg=msg, lineno=token.lineno)
            if keyword == "raise":
                self.advance()
                exc = None
                cause = None
                if not self.at(TokenType.NEWLINE) and not self.at(TokenType.ENDMARKER):
                    exc = self.parse_test()
                    if self.at_keyword("from"):
                        self.advance()
                        cause = self.parse_test()
                return N.Raise(exc=exc, cause=cause, lineno=token.lineno)

        expr = self.parse_expr_list()
        if isinstance(expr, N.NamedExpr):
            # En Python, ``(x := 1)`` sólo vale como expresión: como
            # sentencia es un error de sintaxis.
            raise self.error("no se puede usar una expresión de asignación como sentencia")
        if self.at(TokenType.OP, "="):
            targets = [self.to_target(expr)]
            while self.accept(TokenType.OP, ","):
                targets.append(self.to_target(self.parse_expr_list()))
            self.expect(TokenType.OP, "=")
            value = self.parse_expr_list()
            return N.Assign(targets=targets, value=value, lineno=expr.lineno)
        token = self.peek()
        if token.type is TokenType.OP and token.value in AUG_ASSIGN_OPS:
            self.advance()
            value = self.parse_expr_list()
            return N.AugAssign(
                target=self.to_target(expr),
                op=AUG_ASSIGN_OPS[token.value],
                value=value,
                lineno=expr.lineno,
            )
        return N.ExprStmt(value=expr, lineno=expr.lineno)

    def parse_import(self) -> N.Import:
        token = self.expect(TokenType.NAME, "import")
        names: List[N.ImportAlias] = []
        while True:
            name = self.expect(TokenType.NAME).value
            while self.accept(TokenType.OP, "."):
                name += "." + self.expect(TokenType.NAME).value
            asname = None
            if self.accept(TokenType.NAME, "as"):
                asname = self.expect(TokenType.NAME).value
            names.append(N.ImportAlias(name=name, asname=asname))
            if not self.accept(TokenType.OP, ","):
                break
        return N.Import(names=names, lineno=token.lineno)

    def parse_from_import(self) -> N.ImportFrom:
        token = self.expect(TokenType.NAME, "from")
        level = 0
        while self.accept(TokenType.OP, "."):
            level += 1
        module = ""
        while True:
            name = self.expect(TokenType.NAME).value
            while self.accept(TokenType.OP, "."):
                name += "." + self.expect(TokenType.NAME).value
            module = name if not module else module + "." + name
            if not self.accept(TokenType.OP, "."):
                break
        self.expect(TokenType.NAME, "import")
        names: List[N.ImportAlias] = []
        star = False
        if self.accept(TokenType.OP, "*"):
            star = True
        else:
            parenthesised = bool(self.accept(TokenType.OP, "("))
            while True:
                name = self.expect(TokenType.NAME).value
                asname = None
                if self.accept(TokenType.NAME, "as"):
                    asname = self.expect(TokenType.NAME).value
                names.append(N.ImportAlias(name=name, asname=asname))
                if not self.accept(TokenType.OP, ","):
                    break
            if parenthesised:
                self.expect(TokenType.OP, ")")
        node = N.ImportFrom(module=module, names=names, level=level, lineno=token.lineno)
        if star:
            node.names = [N.ImportAlias(name="*")]
        return node

    # -- construcciones compuestas -------------------------------------
    def parse_blob_decl(self) -> N.BlobDecl:
        token = self.expect(TokenType.NAME, "blob")
        name = self.expect(TokenType.NAME).value
        source = None
        if self.accept(TokenType.OP, "="):
            source = self.parse_expr_list()
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        return N.BlobDecl(name=name, source=source, body=body, lineno=token.lineno)

    def parse_func_def(self) -> N.FuncDef:
        token = self.advance()  # 'func' o 'def'
        name = self.expect(TokenType.NAME).value
        args = self.parse_arguments()
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        return N.FuncDef(name=name, args=args, body=body, lineno=token.lineno)

    def parse_decorated(self) -> Any:
        """Parsea ``@decorador`` seguido de la ``def`` o ``class`` que decora.

        Igual que en CPython, cada decorador va en su propia línea y se aplican
        de abajo hacia arriba: ``@a @b`` es ``a(b(objeto))``.
        """
        decorators: List[Any] = []
        while self.at(TokenType.OP, "@"):
            self.advance()
            decorators.append(self.parse_test())
            self.expect(TokenType.NEWLINE)
        token = self.peek()
        if token.type is TokenType.NAME and token.value in ("func", "def"):
            node = self.parse_func_def()
        elif token.type is TokenType.NAME and token.value == "class":
            node = self.parse_class_def()
        else:
            # Puede que la entrada esté incompleta (la REPL pide otra línea).
            raise IncompleteInput(
                "un decorador debe precede a una 'def' o a una 'class'",
                self.filename,
                token.lineno,
            )
        node.decorators = decorators
        return node

    def parse_class_def(self) -> N.ClassDef:
        """Parsea ``class Nombre(Base1, Base2):``."""
        token = self.expect(TokenType.NAME, "class")
        name = self.expect(TokenType.NAME).value
        bases: List[Any] = []
        if self.accept(TokenType.OP, "("):
            while not self.at(TokenType.OP, ")"):
                bases.append(self.parse_test())
                if not self.accept(TokenType.OP, ","):
                    break
            self.expect(TokenType.OP, ")")
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        return N.ClassDef(name=name, bases=bases, body=body, lineno=token.lineno)

    def parse_with(self) -> N.With:
        """Parsea ``with expr as nombre, expr2 as nombre2:``."""
        token = self.expect(TokenType.NAME, "with")
        items: List[N.WithItem] = []
        while True:
            context = self.parse_test()
            optional_vars = None
            if self.at_keyword("as"):
                self.advance()
                optional_vars = self.parse_with_target()
            items.append(N.WithItem(context_expr=context, optional_vars=optional_vars))
            if not self.accept(TokenType.OP, ","):
                break
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        return N.With(items=items, body=body, lineno=token.lineno)

    def parse_with_target(self) -> Any:
        """Destino de un ``as``: un solo nombre, o una tupla entre paréntesis.

        No puede ser una lista de destinos sin paréntesis: en
        ``with a() as x, b() as y:`` la coma separa los gestores de contexto,
        no los destinos. Como en Python, una tupla de destinos se escribe con
        paréntesis: ``with a() as (x, y):``.
        """
        if self.at(TokenType.OP, "(") or self.at(TokenType.OP, "["):
            return self.to_target(self.parse_target_list())
        return self.parse_target_element()

    def parse_arguments(self) -> N.Arguments:
        self.expect(TokenType.OP, "(")
        args: List[N.Arg] = []
        defaults: List[Any] = []
        kwonly: List[N.Arg] = []
        vararg: Optional[str] = None
        kwarg: Optional[str] = None
        seen_default = False
        solo_palabra_clave = False
        while not self.at(TokenType.OP, ")"):
            if self.accept(TokenType.OP, "**"):
                kwarg = self.expect(TokenType.NAME).value
            elif self.at(TokenType.OP, "*"):
                self.advance()
                if self.at(TokenType.NAME):
                    # ``*args``: argumentos posicionales extra.
                    vararg = self.expect(TokenType.NAME).value
                else:
                    # ``*`` a secas: lo que sigue es sólo por palabra clave.
                    solo_palabra_clave = True
            else:
                arg_name = self.expect(TokenType.NAME).value
                default = None
                if self.accept(TokenType.OP, "="):
                    default = self.parse_test()
                    seen_default = True
                elif seen_default and not solo_palabra_clave:
                    raise self.error("argumento sin valor por defecto tras otro con valor")
                arg = N.Arg(name=arg_name, default=default)
                if solo_palabra_clave:
                    kwonly.append(arg)
                else:
                    args.append(arg)
                    if default is not None:
                        defaults.append(default)
            if not self.accept(TokenType.OP, ","):
                break
        self.expect(TokenType.OP, ")")
        return N.Arguments(
            args=args,
            vararg=vararg,
            kwonlyargs=kwonly,
            kwarg=kwarg,
            defaults=defaults,
        )

    def parse_if(self) -> N.If:
        token = self.expect(TokenType.NAME, "if")
        test = self.parse_test()
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        orelse: List[Any] = []
        if self.at_keyword("elif"):
            orelse = [self.parse_if()]
        elif self.at_keyword("else"):
            self.advance()
            self.expect(TokenType.OP, ":")
            orelse = self.parse_block()
        return N.If(test=test, body=body, orelse=orelse, lineno=token.lineno)

    def parse_while(self) -> N.While:
        token = self.expect(TokenType.NAME, "while")
        test = self.parse_test()
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        orelse: List[Any] = []
        if self.at_keyword("else"):
            self.advance()
            self.expect(TokenType.OP, ":")
            orelse = self.parse_block()
        return N.While(test=test, body=body, orelse=orelse, lineno=token.lineno)

    def parse_for(self) -> N.For:
        token = self.expect(TokenType.NAME, "for")
        target = self.to_target(self.parse_target_list())
        self.expect(TokenType.NAME, "in")
        iterable = self.parse_expr_list()
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        orelse: List[Any] = []
        if self.at_keyword("else"):
            self.advance()
            self.expect(TokenType.OP, ":")
            orelse = self.parse_block()
        return N.For(target=target, iter=iterable, body=body, orelse=orelse, lineno=token.lineno)

    def parse_try(self) -> N.Try:
        token = self.expect(TokenType.NAME, "try")
        self.expect(TokenType.OP, ":")
        body = self.parse_block()
        handlers: List[N.ExceptHandler] = []
        while self.at_keyword("except"):
            self.advance()
            exc_type = None
            name = None
            if not self.at(TokenType.OP, ":"):
                exc_type = self.parse_test()
                if self.accept(TokenType.OP, ","):
                    name = self.expect(TokenType.NAME).value
                elif self.at_keyword("as"):
                    self.advance()
                    name = self.expect(TokenType.NAME).value
            self.expect(TokenType.OP, ":")
            handlers.append(
                N.ExceptHandler(type=exc_type, name=name, body=self.parse_block())
            )
        orelse: List[Any] = []
        finalbody: List[Any] = []
        if self.at_keyword("else"):
            self.advance()
            self.expect(TokenType.OP, ":")
            orelse = self.parse_block()
        if self.at_keyword("finally"):
            self.advance()
            self.expect(TokenType.OP, ":")
            finalbody = self.parse_block()
        return N.Try(
            body=body, handlers=handlers, orelse=orelse, finalbody=finalbody, lineno=token.lineno
        )

    def parse_block(self) -> List[Any]:
        """Parsea un bloque indentado tras los ``:``."""
        self.expect(TokenType.NEWLINE)
        if not self.at(TokenType.INDENT):
            if self.at(TokenType.DEDENT) or self.at(TokenType.ENDMARKER):
                # El bloque quedó abierto: la entrada aún no está completa
                # (igual que ``codeop`` en CPython pide otra línea).
                raise IncompleteInput(
                    "entrada incompleta (falta el cuerpo indentado)", self.filename
                )
            raise self.error("se esperaba un bloque indentado")
        self.advance()  # INDENT
        body: List[Any] = []
        while not self.at(TokenType.DEDENT):
            if self.at(TokenType.ENDMARKER):
                raise IncompleteInput(
                    "entrada incompleta (falta el final del bloque)", self.filename
                )
            if self.at(TokenType.NEWLINE):
                self.advance()
                continue
            body.extend(self.parse_statement())
        self.advance()  # DEDENT
        return body

    # -- expresiones ---------------------------------------------------
    def parse_expr_list(self) -> Any:
        """``a, b, c`` devuelve una tupla si hay comas, si no la primera expresión."""
        first = self.parse_test()
        if not self.at(TokenType.OP, ","):
            return first
        elements = [first]
        while self.accept(TokenType.OP, ","):
            if self.at(TokenType.NEWLINE) or self.at(TokenType.ENDMARKER):
                break
            elements.append(self.parse_star_or_test())
        return N.Tuple(elts=elements, lineno=first.lineno)

    def parse_star_or_test(self) -> Any:
        """Un elemento de tupla que puede ser ``*resto``.

        Sólo tiene sentido como destino (``a, *b = ...``); en una expresión
        el evaluador lo rechaza, igual que hace Python al desempaquetar.
        """
        if self.at(TokenType.OP, "*"):
            self.advance()
            return N.Starred(value=self.parse_test())
        return self.parse_test()

    def parse_test(self) -> Any:
        """Expresión condicional ``a if cond else b``."""
        if self.at_keyword("lambda"):
            return self.parse_lambda()
        if self.at_keyword("yield"):
            return self.parse_yield()
        body = self.parse_or_test()
        if self.at(TokenType.OP, ":="):
            # Expresión de asignación: ``(x := valor)``.
            self.advance()
            if not isinstance(body, N.Name):
                raise self.error("el destino de ':=' debe ser un nombre")
            value = self.parse_test()
            return N.NamedExpr(target=body, value=value, lineno=body.lineno)
        if self.at_keyword("if"):
            self.advance()
            test = self.parse_or_test()
            self.expect(TokenType.NAME, "else")
            orelse = self.parse_test()
            return N.IfExp(test=test, body=body, orelse=orelse, lineno=body.lineno)
        return body

    def parse_yield(self) -> N.Yield:
        """Parsea ``yield``, ``yield valor`` y ``yield from iterable``."""
        token = self.expect(TokenType.NAME, "yield")
        if self.at_keyword("from"):
            self.advance()
            value = self.parse_test()
            return N.Yield(value=value, delegate=True, lineno=token.lineno)
        if (
            self.at(TokenType.NEWLINE)
            or self.at(TokenType.ENDMARKER)
            or self.at(TokenType.OP, ")")
            or self.at(TokenType.OP, "]")
            or self.at(TokenType.OP, "}")
            or self.at(TokenType.OP, ",")
            or self.at(TokenType.OP, ";")
        ):
            return N.Yield(value=None, lineno=token.lineno)
        value = self.parse_expr_list()
        return N.Yield(value=value, lineno=token.lineno)

    def parse_lambda(self) -> N.Lambda:
        token = self.expect(TokenType.NAME, "lambda")
        args = N.Arguments()
        names: List[N.Arg] = []
        defaults: List[Any] = []
        while not self.at(TokenType.OP, ":"):
            name = self.expect(TokenType.NAME).value
            default = None
            if self.accept(TokenType.OP, "="):
                default = self.parse_test()
                defaults.append(default)
            names.append(N.Arg(name=name, default=default))
            if not self.accept(TokenType.OP, ","):
                break
        args.args = names
        args.defaults = defaults
        self.expect(TokenType.OP, ":")
        body = self.parse_test()
        return N.Lambda(args=args, body=body, lineno=token.lineno)

    def parse_or_test(self) -> Any:
        value = self.parse_and_test()
        while self.at_keyword("or"):
            self.advance()
            value = N.BoolOp(op="or", values=[value, self.parse_and_test()], lineno=value.lineno)
        return value

    def parse_and_test(self) -> Any:
        value = self.parse_not_test()
        while self.at_keyword("and"):
            self.advance()
            value = N.BoolOp(op="and", values=[value, self.parse_not_test()], lineno=value.lineno)
        return value

    def parse_not_test(self) -> Any:
        if self.at_keyword("not"):
            token = self.advance()
            return N.UnaryOp(op="not", operand=self.parse_not_test(), lineno=token.lineno)
        return self.parse_comparison()

    def parse_comparison(self) -> Any:
        left = self.parse_bitor()
        ops: List[str] = []
        comparators: List[Any] = []
        while True:
            token = self.peek()
            if token.type is TokenType.OP and token.value in COMPARE_OPS:
                self.advance()
                ops.append(token.value)
                comparators.append(self.parse_bitor())
                continue
            if self.at_keyword("in"):
                self.advance()
                ops.append("in")
                comparators.append(self.parse_bitor())
                continue
            if self.at_keyword("not") and self.peek(1).type is TokenType.NAME and self.peek(1).value == "in":
                self.advance()
                self.advance()
                ops.append("not in")
                comparators.append(self.parse_bitor())
                continue
            if self.at_keyword("is"):
                self.advance()
                if self.at_keyword("not"):
                    self.advance()
                    ops.append("is not")
                else:
                    ops.append("is")
                comparators.append(self.parse_bitor())
                continue
            break
        if ops:
            return N.Compare(left=left, ops=ops, comparators=comparators, lineno=left.lineno)
        return left

    def parse_bitor(self) -> Any:
        left = self.parse_bitxor()
        while self.at(TokenType.OP, "|"):
            self.advance()
            left = N.BinOp(left=left, op="|", right=self.parse_bitxor(), lineno=left.lineno)
        return left

    def parse_bitxor(self) -> Any:
        left = self.parse_bitand()
        while self.at(TokenType.OP, "^"):
            self.advance()
            left = N.BinOp(left=left, op="^", right=self.parse_bitand(), lineno=left.lineno)
        return left

    def parse_bitand(self) -> Any:
        left = self.parse_shift()
        while self.at(TokenType.OP, "&"):
            self.advance()
            left = N.BinOp(left=left, op="&", right=self.parse_shift(), lineno=left.lineno)
        return left

    def parse_shift(self) -> Any:
        left = self.parse_arith()
        while self.at(TokenType.OP, "<<") or self.at(TokenType.OP, ">>"):
            op = self.advance().value
            left = N.BinOp(left=left, op=op, right=self.parse_arith(), lineno=left.lineno)
        return left

    def parse_arith(self) -> Any:
        left = self.parse_term()
        while self.at(TokenType.OP, "+") or self.at(TokenType.OP, "-"):
            op = self.advance().value
            left = N.BinOp(left=left, op=op, right=self.parse_term(), lineno=left.lineno)
        return left

    def parse_term(self) -> Any:
        left = self.parse_factor()
        while (
            self.at(TokenType.OP, "*")
            or self.at(TokenType.OP, "/")
            or self.at(TokenType.OP, "//")
            or self.at(TokenType.OP, "%")
            or self.at(TokenType.OP, "@")
        ):
            op = self.advance().value
            left = N.BinOp(left=left, op=op, right=self.parse_factor(), lineno=left.lineno)
        return left

    def parse_factor(self) -> Any:
        token = self.peek()
        if token.type is TokenType.OP and token.value in ("+", "-", "~"):
            self.advance()
            return N.UnaryOp(op=token.value, operand=self.parse_factor(), lineno=token.lineno)
        return self.parse_power()

    def parse_power(self) -> Any:
        left = self.parse_postfix()
        if self.at(TokenType.OP, "**"):
            self.advance()
            return N.BinOp(left=left, op="**", right=self.parse_factor(), lineno=left.lineno)
        return left

    def parse_postfix(self) -> Any:
        node = self.parse_primary()
        while True:
            if self.at(TokenType.OP, "("):
                self.advance()
                args: List[Any] = []
                keywords: List[N.Keyword] = []
                primero = True
                while not self.at(TokenType.OP, ")"):
                    if self.at(TokenType.OP, "**"):
                        # ``f(**d)``: expands a mapping into keyword arguments.
                        self.advance()
                        keywords.append(
                            N.Keyword(arg=None, value=self.parse_test(), lineno=node.lineno)
                        )
                    elif self.at(TokenType.OP, "*"):
                        # ``f(*seq)``: expands a sequence into positional
                        # arguments.
                        self.advance()
                        args.append(N.Starred(value=self.parse_test(), lineno=node.lineno))
                    elif (
                        self.peek().type is TokenType.NAME
                        and self.peek().value not in ("not", "lambda")
                        and self.peek(1).type is TokenType.OP
                        and self.peek(1).value == "="
                    ):
                        arg_name = self.advance().value
                        self.advance()  # '='
                        keywords.append(
                            N.Keyword(arg=arg_name, value=self.parse_test(), lineno=node.lineno)
                        )
                    else:
                        args.append(self.parse_test())
                    if primero and self.at_keyword("for"):
                        # ``f(x for x in xs)``: expresión generadora. En Python
                        # sólo puede ser el único argumento de la llamada.
                        if len(args) != 1 or keywords:
                            raise self.error(
                                "una expresión generadora sólo puede ser el "
                                "único argumento de una llamada"
                            )
                        generadores = self.parse_comprehensions()
                        args = [
                            N.GeneratorExp(
                                elt=args[0], generators=generadores, lineno=node.lineno
                            )
                        ]
                        primero = False
                        # Tras la expresión generadora sólo puede venir el
                        # cierre de la llamada: ``f(x for x in xs, 2)`` es un
                        # error de sintaxis, como en Python.
                        break
                    primero = False
                    if not self.accept(TokenType.OP, ","):
                        break
                self.expect(TokenType.OP, ")")
                node = N.Call(func=node, args=args, keywords=keywords, lineno=node.lineno)
                continue
            if self.at(TokenType.OP, "["):
                self.advance()
                index = self.parse_subscript()
                self.expect(TokenType.OP, "]")
                node = N.Subscript(value=node, slice=index, lineno=node.lineno)
                continue
            if self.at(TokenType.OP, "."):
                self.advance()
                attr = self.expect(TokenType.NAME).value
                node = N.Attribute(value=node, attr=attr, lineno=node.lineno)
                continue
            break
        return node

    def parse_subscript(self) -> Any:
        lower = None
        if not self.at(TokenType.OP, ":"):
            lower = self.parse_test()
            if not self.at(TokenType.OP, ":"):
                return lower
        self.expect(TokenType.OP, ":")
        upper = None
        if not self.at(TokenType.OP, ":") and not self.at(TokenType.OP, "]"):
            upper = self.parse_test()
        step = None
        if self.accept(TokenType.OP, ":"):
            if not self.at(TokenType.OP, "]"):
                step = self.parse_test()
        return N.Slice(lower=lower, upper=upper, step=step)

    def parse_primary(self) -> Any:
        token = self.peek()
        if token.type is TokenType.NUMBER:
            self.advance()
            return N.Constant(value=token.value, lineno=token.lineno)
        if token.type is TokenType.STRING:
            self.advance()
            value = token.value
            while self.at(TokenType.STRING):  # concatenación implícita
                value += self.advance().value
            return N.Constant(value=value, lineno=token.lineno)
        if token.type is TokenType.FSTRING:
            self.advance()
            return self.build_joined_string(token)
        if token.type is TokenType.NAME:
            if token.value == "True":
                self.advance()
                return N.Constant(value=True, lineno=token.lineno)
            if token.value == "False":
                self.advance()
                return N.Constant(value=False, lineno=token.lineno)
            if token.value == "None":
                self.advance()
                return N.Constant(value=None, lineno=token.lineno)
            if token.value == "lambda":
                return self.parse_lambda()
            self.advance()
            return N.Name(id=token.value, lineno=token.lineno)
        if token.type is TokenType.OP and token.value == "(":
            self.advance()
            if self.at(TokenType.OP, ")"):
                self.advance()
                return N.Tuple(elts=[], lineno=token.lineno)
            first = self.parse_star_or_test()
            if self.at(TokenType.OP, ","):
                elements = [first]
                while self.accept(TokenType.OP, ","):
                    if self.at(TokenType.OP, ")"):
                        break
                    elements.append(self.parse_star_or_test())
                self.expect(TokenType.OP, ")")
                return N.Tuple(elts=elements, lineno=token.lineno)
            self.expect(TokenType.OP, ")")
            return first
        if token.type is TokenType.OP and token.value == "[":
            return self.parse_list_display()
        if token.type is TokenType.OP and token.value == "{":
            return self.parse_brace_display()
        raise self.error(f"syntaxis inválida cerca de {token.value!r}", token)

    def parse_list_display(self) -> Any:
        token = self.expect(TokenType.OP, "[")
        if self.at(TokenType.OP, "]"):
            self.advance()
            return N.List(elts=[], lineno=token.lineno)
        first = self.parse_star_or_test()
        if self.at_keyword("for"):
            if isinstance(first, N.Starred):
                raise self.error("no se puede usar '*' en una comprensión")
            generators = self.parse_comprehensions()
            self.expect(TokenType.OP, "]")
            return N.ListComp(elt=first, generators=generators, lineno=token.lineno)
        elements = [first]
        while self.accept(TokenType.OP, ","):
            if self.at(TokenType.OP, "]"):
                break
            elements.append(self.parse_star_or_test())
        self.expect(TokenType.OP, "]")
        return N.List(elts=elements, lineno=token.lineno)

    def parse_brace_display(self) -> Any:
        token = self.expect(TokenType.OP, "{")
        if self.at(TokenType.OP, "}"):
            self.advance()
            return N.Dict(keys=[], values=[], lineno=token.lineno)
        if self.at(TokenType.OP, "**"):
            # ``{**otro, 'b': 2}``: un diccionario con expansión.
            return self.parse_dict_with_unpacking(token)
        first = self.parse_star_or_test()
        if self.at(TokenType.OP, ":"):
            if isinstance(first, N.Starred):
                raise self.error("no se puede mezclar '*' con pares clave-valor")
            self.advance()
            value = self.parse_test()
            if self.at_keyword("for"):
                generators = self.parse_comprehensions()
                self.expect(TokenType.OP, "}")
                return N.DictComp(key=first, value=value, generators=generators, lineno=token.lineno)
            keys = [first]
            values = [value]
            while self.accept(TokenType.OP, ","):
                if self.at(TokenType.OP, "}"):
                    break
                key = self.parse_test()
                self.expect(TokenType.OP, ":")
                values.append(self.parse_test())
                keys.append(key)
            self.expect(TokenType.OP, "}")
            return N.Dict(keys=keys, values=values, lineno=token.lineno)
        if self.at_keyword("for"):
            if isinstance(first, N.Starred):
                raise self.error("no se puede usar '*' en una comprensión")
            generators = self.parse_comprehensions()
            self.expect(TokenType.OP, "}")
            return N.SetComp(elt=first, generators=generators, lineno=token.lineno)
        elements = [first]
        while self.accept(TokenType.OP, ","):
            if self.at(TokenType.OP, "}"):
                break
            elements.append(self.parse_star_or_test())
        self.expect(TokenType.OP, "}")
        return N.Set(elts=elements, lineno=token.lineno)

    def parse_dict_with_unpacking(self, token: Token) -> Any:
        """Parsea ``{**a, 'b': 1}``.

        Las expansiones se guardan como claves ``N.Starred``: el evaluador
        las distingue de una clave normal y actualiza el diccionario con
        ellas, como hace Python.
        """
        keys: List[Any] = []
        values: List[Any] = []
        while True:
            if self.at(TokenType.OP, "**"):
                self.advance()
                keys.append(N.Starred(value=self.parse_test(), lineno=token.lineno))
                values.append(None)
            else:
                key = self.parse_test()
                self.expect(TokenType.OP, ":")
                keys.append(key)
                values.append(self.parse_test())
            if not self.accept(TokenType.OP, ","):
                break
            if self.at(TokenType.OP, "}"):
                break
        self.expect(TokenType.OP, "}")
        return N.Dict(keys=keys, values=values, lineno=token.lineno)

    def parse_comprehensions(self) -> List[N.CompGenerator]:
        generators: List[N.CompGenerator] = []
        while self.at_keyword("for"):
            self.advance()
            target = self.to_target(self.parse_target_list())
            self.expect(TokenType.NAME, "in")
            iterable = self.parse_or_test()
            ifs: List[Any] = []
            while self.at_keyword("if"):
                self.advance()
                ifs.append(self.parse_or_test())
            generators.append(
                N.CompGenerator(target=target, iter=iterable, ifs=ifs)
            )
        return generators

    def build_joined_string(self, token: Token) -> N.JoinedStr:
        """Construye el nodo ``JoinedStr`` de una f-string."""
        from .lexer import split_fstring

        values: List[Any] = []
        for kind, payload in split_fstring(token.value):
            if kind == "str":
                values.append(N.Constant(value=payload, lineno=token.lineno))
                continue
            source, conversion, spec = payload  # type: ignore[misc]
            sub = Parser(tokenize(source, self.filename), self.filename)
            expression = sub.parse_test()
            format_spec = None
            if spec:
                # El formato de una f-string es texto literal, como en CPython:
                # ``f"{x:03d}"`` formatea con la especificación "03d".
                format_spec = N.Constant(
                    value=decode_escapes(spec, self.filename, token.lineno),
                    lineno=token.lineno,
                )
            values.append(
                N.FormattedValue(
                    value=expression,
                    conversion=conversion,
                    format_spec=format_spec,
                    lineno=token.lineno,
                )
            )
        return N.JoinedStr(values=values, lineno=token.lineno)

    # -- utilidades ----------------------------------------------------
    def parse_target_list(self) -> Any:
        """Destino de un bucle: sólo nombres, tuplas o listas.

        No se puede usar ``parse_expr_list`` aquí: ``for i in xs`` se
        interpretaría como la comparación ``i in xs`` (igual que hace el
        analizador de CPython, que usa ``star_targets`` y no una expresión).
        """
        if self.at(TokenType.OP, "(") or self.at(TokenType.OP, "["):
            closing = ")" if self.at(TokenType.OP, "(") else "]"
            self.advance()
            elements: List[Any] = []
            while not self.at(TokenType.OP, closing):
                elements.append(self.parse_target_element())
                if not self.accept(TokenType.OP, ","):
                    break
            self.expect(TokenType.OP, closing)
            if closing == ")":
                return N.Tuple(elts=elements)
            return N.List(elts=elements)
        first = self.parse_target_element()
        if not self.at(TokenType.OP, ","):
            return first
        elements = [first]
        while self.accept(TokenType.OP, ","):
            if self.at_keyword("in"):
                break
            elements.append(self.parse_target_element())
        return N.Tuple(elts=elements)

    def parse_target_element(self) -> Any:
        """Un elemento de destino: nombre, tupla/lista o ``*resto``."""
        if self.at(TokenType.OP, "*"):
            self.advance()
            return N.Starred(value=self.parse_target_element())
        token = self.expect(TokenType.NAME)
        return N.Name(id=token.value, lineno=token.lineno)

    def parse_del_target(self) -> Any:
        """Destino de un ``del``: un nombre, un atributo o un índice.

        Es lo mismo que acepta CPython: ``del x``, ``del obj.attr`` y
        ``del lista[0]``; lo demás es un error de sintaxis.
        """
        node = self.parse_primary()
        while True:
            if self.at(TokenType.OP, "["):
                self.advance()
                index = self.parse_subscript()
                self.expect(TokenType.OP, "]")
                node = N.Subscript(value=node, slice=index, lineno=node.lineno)
                continue
            if self.at(TokenType.OP, "."):
                self.advance()
                attr = self.expect(TokenType.NAME)
                node = N.Attribute(value=node, attr=attr.value, lineno=node.lineno)
                continue
            break
        if not isinstance(node, (N.Name, N.Attribute, N.Subscript)):
            raise self.error("no se puede eliminar esta expresión")
        return node

    def to_target(self, node: Any) -> Any:
        """Convierte una expresión en un destino asignable."""
        if isinstance(node, N.Name):
            return node
        if isinstance(node, (N.Attribute, N.Subscript)):
            return node
        if isinstance(node, (N.Tuple, N.List)):
            node.elts = [self.to_target(element) for element in node.elts]
            return node
        if isinstance(node, N.Starred):
            return node
        raise self.error("no se puede asignar a esta expresión")


def parse(source: str, filename: str = "<cruthon>") -> N.Module:
    """Analiza ``source`` y devuelve el módulo con su árbol de sintaxis."""
    tokens = tokenize(source, filename)
    parser = Parser(tokens, filename)
    return parser.parse_module()
