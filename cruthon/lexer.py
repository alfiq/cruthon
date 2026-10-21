"""Tokenizador de Cruthon.

Modelado sobre el tokenizador de CPython (``Parser/tokenizer.c`` y
``Lib/tokenize.py``): mismo algoritmo de sangrías (INDENT/DEDENT), mismo
tratamiento de cadenas, números y operadores, con dos diferencias propias del
lenguaje:

* ``blob`` es una palabra clave nueva que abre un bloque, equivalente a una
  clase sin herencia implícita.
* ``func`` es la palabra clave para declarar funciones (``def`` sigue
  aceptándose como alias, para que el código Python se pueda portar tal cual).
"""

from __future__ import annotations

from enum import Enum, auto
from typing import List, Tuple

from .errors import CruthonSyntaxError, IncompleteInput


class TokenType(Enum):
    NAME = auto()
    NUMBER = auto()
    STRING = auto()
    FSTRING = auto()
    OP = auto()
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    ENDMARKER = auto()


#: Palabras clave de Python que Cruthon mantiene.
PYTHON_KEYWORDS = frozenset(
    {
        "False", "None", "True", "and", "as", "assert", "async", "await",
        "break", "class", "continue", "def", "del", "elif", "else", "except",
        "finally", "for", "from", "global", "if", "import", "in", "is",
        "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
        "while", "with", "yield",
    }
)

#: Palabras clave propias de Cruthon.
CRUTHON_KEYWORDS = frozenset({"blob", "func"})

KEYWORDS = PYTHON_KEYWORDS | CRUTHON_KEYWORDS

#: Operadores, del más largo al más corto (coincidencia maximal).
OPERATORS: Tuple[str, ...] = (
    "**=", "//=", ">>=", "<<=", "!=", ">=", "<=", "==", "->", ":=", "...",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "@=", "**", "//", ">>", "<<",
    "+", "-", "*", "/", "%", "@", "&", "|", "^", "~", "<", ">", "(", ")", "[",
    "]", "{", "}", ",", ":", ".", ";", "=",
)

#: Prefijos de cadena admitidos (como en CPython).
STRING_PREFIXES = frozenset(
    {"", "r", "b", "u", "f", "rb", "br", "rf", "fr", "br", "rb"}
)

_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_CONT = _IDENT_START | set("0123456789")
_DIGITS = set("0123456789")


class Token:
    """Un token del flujo de Cruthon."""

    __slots__ = ("type", "value", "lineno", "colno")

    def __init__(self, type: TokenType, value, lineno: int, colno: int) -> None:
        self.type = type
        self.value = value
        self.lineno = lineno
        self.colno = colno

    def __repr__(self) -> str:  # pragma: no cover - ayuda de depuración
        return f"Token({self.type.name}, {self.value!r}, line={self.lineno})"


def split_fstring(raw: str) -> List[Tuple[str, object]]:
    """Divide el cuerpo de una f-string en literales y expresiones.

    Devuelve una lista de tuplas ``('str', texto)`` y ``('expr', (codigo,
    conversion, format_spec))`` siguiendo la gramática de CPython para
    ``JoinedStr``.
    """
    parts: List[Tuple[str, object]] = []
    buffer: List[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "{":
            if raw[i : i + 2] == "{{":
                buffer.append("{")
                i += 2
                continue
            j = _find_closing_brace(raw, i)
            expr_src, conversion, spec = _split_expression(raw[i + 1 : j])
            if buffer:
                parts.append(("str", "".join(buffer)))
                buffer = []
            parts.append(("expr", (expr_src, conversion, spec)))
            i = j + 1
            continue
        if ch == "}":
            if raw[i : i + 2] == "}}":
                buffer.append("}")
                i += 2
                continue
            raise CruthonSyntaxError("f-string: '}' sin escapar")
        buffer.append(ch)
        i += 1
    if buffer:
        parts.append(("str", "".join(buffer)))
    return parts


def _find_closing_brace(raw: str, start: int) -> int:
    """Devuelve el índice de la ``}`` que cierra la expresión abierta en ``start``."""
    depth = 0
    j = start
    n = len(raw)
    while j < n:
        ch = raw[j]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return j
        elif ch in "\"'":
            quote = ch
            j += 1
            while j < n and raw[j] != quote:
                if raw[j] == "\\":
                    j += 1
                j += 1
        j += 1
    raise IncompleteInput("cadena f sin cerrar")


def _split_expression(src: str):
    """Separa ``expr``, ``!conversion`` y ``:spec`` de una expresión de f-string."""
    depth = 0
    conv = None
    spec = None
    cut = len(src)
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch in "\"'":
            quote = ch
            i += 1
            while i < n and src[i] != quote:
                if src[i] == "\\":
                    i += 1
                i += 1
        elif depth == 0 and ch == "!" and i + 1 < n and src[i + 1] != "=":
            conv = src[i + 1]
            cut = min(cut, i)
        elif depth == 0 and ch == ":":
            spec = src[i + 1 :]
            cut = min(cut, i)
        i += 1
    return src[:cut].strip(), conv, spec


class Lexer:
    """Convierte código fuente de Cruthon en una lista de tokens."""

    def __init__(self, source: str, filename: str = "<cruthon>") -> None:
        self.source = source
        self.filename = filename
        self.tokens: List[Token] = []
        self._indents = [0]
        self._paren = 0
        self._lineno = 1

    # -- API pública ---------------------------------------------------
    def tokenize(self) -> List[Token]:
        """Tokeniza el código completo y devuelve la lista de tokens."""
        src = self.source
        n = len(src)
        i = 0
        atbol = True
        while i < n:
            if atbol and self._paren == 0:
                i, atbol = self._handle_indentation(i)
                continue
            ch = src[i]
            if ch == "\n":
                if self._paren > 0:
                    i += 1
                    self._lineno += 1
                    continue
                self._emit(TokenType.NEWLINE, "\n")
                i += 1
                self._lineno += 1
                atbol = True
                continue
            if ch in " \t\r\f\v":
                i += 1
                continue
            if ch == "\\" and i + 1 < n and src[i + 1] == "\n":
                i += 2
                self._lineno += 1
                continue
            if ch == "#":
                while i < n and src[i] != "\n":
                    i += 1
                continue
            if ch in "\"'":
                i = self._lex_string(i, "")
                continue
            if ch in _IDENT_START:
                i = self._lex_name_or_prefix(i)
                continue
            if ch in _DIGITS or (ch == "." and i + 1 < n and src[i + 1] in _DIGITS):
                i = self._lex_number(i)
                continue
            for op in OPERATORS:
                if src.startswith(op, i):
                    self._emit(TokenType.OP, op)
                    i += len(op)
                    if op in "([{":
                        self._paren += 1
                    elif op in ")]}":
                        self._paren = max(0, self._paren - 1)
                    break
            else:
                raise CruthonSyntaxError(
                    f"carácter inesperado {ch!r}", self.filename, self._lineno, i
                )
        # Fin del fichero: cerramos bloques y línea.
        if self.tokens and self.tokens[-1].type not in (TokenType.NEWLINE,):
            self._emit(TokenType.NEWLINE, "")
        while len(self._indents) > 1:
            self._indents.pop()
            self._emit(TokenType.DEDENT, "")
        self._emit(TokenType.ENDMARKER, "")
        return self.tokens

    # -- Interno -------------------------------------------------------
    def _emit(self, ttype: TokenType, value) -> None:
        self.tokens.append(Token(ttype, value, self._lineno, 0))

    def _handle_indentation(self, i: int) -> tuple:
        """Calcula la sangría de la línea y emite INDENT/DEDENT.

        Devuelve ``(posicion, sigue_en_inicio_de_linea)``. La segunda bandera
        vale ``True`` cuando la línea estaba en blanco o sólo era un
        comentario: entonces no se emitieron tokens de sangría y hay que
        seguir midiendo la sangría de la línea siguiente.
        """
        src = self.source
        n = len(src)
        width = 0
        j = i
        while j < n and src[j] in " \t":
            if src[j] == "\t":
                width = (width // 8 + 1) * 8
            else:
                width += 1
            j += 1
        # Línea en blanco o solo comentario: no genera tokens de sangría.
        k = j
        while k < n and src[k] in " \r":
            k += 1
        if k >= n or src[k] == "\n" or src[k] == "#":
            while k < n and src[k] != "\n":
                k += 1
            if k < n:
                k += 1
                self._lineno += 1
            return k, True
        if width > self._indents[-1]:
            self._indents.append(width)
            self._emit(TokenType.INDENT, "")
        else:
            while width < self._indents[-1]:
                self._indents.pop()
                self._emit(TokenType.DEDENT, "")
            if width != self._indents[-1]:
                raise CruthonSyntaxError(
                    "sangría inconsistente", self.filename, self._lineno, width
                )
        return j, False

    def _lex_name_or_prefix(self, i: int) -> int:
        src = self.source
        n = len(src)
        j = i
        while j < n and src[j] in _IDENT_CONT:
            j += 1
        word = src[i:j]
        # ¿Es un prefijo de cadena? (``r"x"``, ``f"x"``, ``rb"x"``...)
        if j < n and src[j] in "\"'" and word.lower() in STRING_PREFIXES:
            return self._lex_string(i, word)
        self._emit(TokenType.NAME, word)
        return j

    def _lex_string(self, i: int, prefix: str) -> int:
        src = self.source
        n = len(src)
        j = i + len(prefix)
        quote = src[j]
        raw = "b" in prefix.lower()
        is_fstring = "f" in prefix.lower()
        triple = src.startswith(quote * 3, j)
        if triple:
            j += 3
        else:
            j += 1
        chunks: List[str] = []
        while True:
            if j >= n:
                raise IncompleteInput("cadena sin cerrar", self.filename, self._lineno)
            ch = src[j]
            if not triple and ch == "\n":
                raise CruthonSyntaxError(
                    "cadena sin cerrar (salto de línea)", self.filename, self._lineno
                )
            if triple and src.startswith(quote * 3, j):
                j += 3
                break
            if not triple and ch == quote:
                j += 1
                break
            if ch == "\\":
                if j + 1 >= n:
                    raise IncompleteInput("cadena sin cerrar", self.filename, self._lineno)
                nxt = src[j + 1]
                if raw:
                    chunks.append(ch)
                    if nxt != "\n":
                        chunks.append(nxt)
                    j += 2
                else:
                    chunks.append(ch)
                    chunks.append(nxt)
                    j += 2
                if nxt == "\n":
                    self._lineno += 1
                continue
            if ch == "\n":
                self._lineno += 1
            chunks.append(ch)
            j += 1
        body = "".join(chunks)
        if is_fstring:
            self._emit(TokenType.FSTRING, body)
        else:
            self._emit(TokenType.STRING, body)
        return j

    def _lex_number(self, i: int) -> int:
        src = self.source
        n = len(src)
        j = i
        if src[j] == "0" and j + 1 < n and src[j + 1] in "xXoObB":
            j += 2
            while j < n and (src[j] in _IDENT_CONT):
                j += 1
            text = src[i:j]
            try:
                value = int(text, 0)
            except ValueError:
                raise CruthonSyntaxError(
                    f"literal numérico inválido {text!r}", self.filename, self._lineno
                ) from None
            self._emit(TokenType.NUMBER, value)
            return j
        is_float = False
        while j < n and (src[j] in _DIGITS or src[j] == "_"):
            j += 1
        if j < n and src[j] == "." and not (j + 1 < n and src[j + 1] == "."):
            is_float = True
            j += 1
            while j < n and (src[j] in _DIGITS or src[j] == "_"):
                j += 1
        if j < n and src[j] in "eE":
            k = j + 1
            if k < n and src[k] in "+-":
                k += 1
            if k < n and src[k] in _DIGITS:
                is_float = True
                j = k
                while j < n and src[j] in _DIGITS:
                    j += 1
        text = src[i:j].replace("_", "")
        if is_float:
            self._emit(TokenType.NUMBER, float(text))
        else:
            self._emit(TokenType.NUMBER, int(text))
        return j


def tokenize(source: str, filename: str = "<cruthon>") -> List[Token]:
    """Tokeniza ``source`` y devuelve la lista de tokens."""
    return Lexer(source, filename).tokenize()
