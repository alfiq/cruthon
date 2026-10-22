"""Pruebas de contrato: paridad con Python en lo que Cruthon comparte.

Estas pruebas fijan el comportamiento que el documento de diseño da por
supuesto al decir que Cruthon "se ejecuta como Python": el mismo código, la
misma salida. Se escriben a nivel de fuente (se ejecuta el programa y se
compara lo que imprime), no sobre internos del intérprete.

Cada prueba compara contra el resultado real de CPython para el mismo
fragmento, de modo que la paridad no depende de una constante escrita a mano
que se pueda quedar vieja. Los dos lados se ejecutan como procesos
independientes (``python -m cruthon -c`` y ``python -c``) para que la
comparación sea justa: el mismo arranque, la misma captura y la misma
traducción de saltos de línea.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: Fragmentos que Python y Cruthon deben ejecutar igual.
PARIDAD = [
    pytest.param("print('\\t')", id="escape-tabulador"),
    pytest.param("print('\\n')", id="escape-nueva-linea"),
    pytest.param("print('\\r\\t\\\\')", id="escape-combinado"),
    pytest.param("print('\\x41\\101')", id="escape-hexadecimal-y-octal"),
    pytest.param("print('\\a\\b\\f\\v')", id="escape-control"),
    pytest.param("print('\\0')", id="escape-nulo"),
    pytest.param("print('\\u00e9\\N{LATIN SMALL LETTER E WITH ACUTE}')", id="escape-unicode"),
    pytest.param("print('it\\'s')", id="comilla-simple-escapada"),
    pytest.param("print('a' 'b')", id="concatenacion-implicita"),
    pytest.param("print(1 + 2 * 3 ** 2)", id="precedencia-aritmetica"),
    pytest.param("x = 5\nprint(x if x > 3 else 'no')", id="ternario"),
    pytest.param("print([i * 2 for i in range(3)])", id="comprension-de-lista"),
    pytest.param("print({i: i * i for i in range(3)})", id="comprension-de-diccionario"),
    pytest.param("print(len([1, 2]), max(3, 1), min([4, 2]))", id="builtins-basicos"),
    pytest.param("print(f'{1 + 1}')", id="f-string"),
    pytest.param("print(f'{3.14159:.2f}')", id="f-string-con-formato"),
    pytest.param("print(f'{42:>6}')", id="f-string-con-alineacion"),
    pytest.param("a, *resto = [1, 2, 3]\nprint(a, resto)", id="desempaquetado-con-estrella"),
    pytest.param("print(sorted([3, 1, 2]), sum([1, 2]))", id="sorted-y-sum"),
    pytest.param(
        "try:\n    1 / 0\nexcept ZeroDivisionError:\n    print('div')",
        id="try-except",
    ),
    pytest.param("print('a' in 'abc', 'z' not in 'abc')", id="pertenencia-a-cadena"),
    pytest.param("print([1, 2] + [3], [1] * 2)", id="operaciones-de-lista"),
    pytest.param("d = {'a': 1}\nprint(d['a'], 'a' in d)", id="diccionario"),
    pytest.param("def f(a, b=2):\n    return a + b\nprint(f(1), f(1, 3))", id="valor-por-defecto"),
    pytest.param("def f(*args):\n    return len(args)\nprint(f(1, 2, 3))", id="varargs"),
    pytest.param("def f(**kw):\n    return sorted(kw)\nprint(f(b=2, a=1))", id="kwargs"),
    pytest.param("def f(a, **kw):\n    return (a, sorted(kw))\nprint(f(1, b=2))", id="kwargs-con-posicional"),
    pytest.param("def f(a, *, b):\n    return a + b\nprint(f(1, b=2))", id="solo-por-palabra-clave"),
    pytest.param(
        "def outer(x):\n    def inner():\n        return x\n    return inner()\nprint(outer(7))",
        id="closure",
    ),
    pytest.param(
        "def outer():\n    x = 1\n    def inner():\n        nonlocal x\n        x = 2\n"
        "    inner()\n    return x\nprint(outer())",
        id="nonlocal",
    ),
    pytest.param("x = 1\ndef f():\n    global x\n    x = 5\nf()\nprint(x)", id="global"),
    pytest.param("x = 1\ndel x\nprint('borrado')", id="del"),
    pytest.param("print(list(range(3))[1:])", id="slice"),
    pytest.param("print(2 ** 10, 7 // 2, 7 % 2)", id="operadores"),
    pytest.param("print(abs(-3), round(2.567, 2), divmod(7, 2))", id="matematicas"),
    pytest.param("print(isinstance(1, int), callable(print))", id="introspeccion"),
    pytest.param("import math\nprint(math.sqrt(9))", id="import-de-la-stdlib"),
    pytest.param("assert True\nprint('ok')", id="assert"),
    pytest.param("for i in range(3):\n    print(i)", id="for"),
    pytest.param("i = 0\nwhile i < 2:\n    print(i)\n    i += 1", id="while"),
    pytest.param("print(1 if True else 2, None is None, True is not False)", id="comparaciones"),
    pytest.param("print('\\N{GREEK SMALL LETTER ALPHA}')", id="escape-nombre-unicode"),
    pytest.param("def f(a, b):\n    return a + b\nprint(f(*[1, 2]))", id="expansion-posicional"),
    pytest.param(
        "def f(a, b=0):\n    return a + b\nprint(f(1, **{'b': 3}))",
        id="expansion-por-palabra-clave",
    ),
    pytest.param(
        "def f(*args):\n    return args\nprint(f(*[1, 2]))",
        id="expansion-a-varargs",
    ),
    pytest.param("print(*[1, 2])", id="expansion-en-print"),
    pytest.param("print({**{'a': 1}, 'b': 2})", id="expansion-en-diccionario"),
    pytest.param(
        "def dobro(x):\n    return x * 2\nprint(list(map(dobro, [1, 2])))",
        id="map-con-funcion-de-cruthon",
    ),
    pytest.param(
        "def maior(x):\n    return x > 1\nprint(list(filter(maior, [1, 2, 3])))",
        id="filter-con-funcion-de-cruthon",
    ),
    pytest.param(
        "def chave(x):\n    return -x\nprint(sorted([1, 3, 2], key=chave))",
        id="sorted-con-key-de-cruthon",
    ),
    pytest.param("print(type(1) is int, type('a') is str)", id="builtin-type"),
    # --- ``raise`` y el protocolo de excepciones ---
    pytest.param(
        "try:\n    raise ValueError('x')\nexcept ValueError as e:\n    print('cazado', e)",
        id="raise-y-except",
    ),
    pytest.param(
        "try:\n    try:\n        raise ValueError('x')\n    except ValueError:\n        raise\n"
        "except ValueError as e:\n    print('re-lanzado', e)",
        id="raise-a-secas",
    ),
    pytest.param(
        "try:\n    raise ValueError('x')\nexcept (TypeError, ValueError):\n    print('tuple')",
        id="except-con-tuple",
    ),
    pytest.param(
        "try:\n    1 / 0\nexcept ZeroDivisionError:\n    print('cero')\nelse:\n"
        "    print('nunca')\nfinally:\n    print('siempre')",
        id="try-else-finally",
    ),
    # --- clases ---
    pytest.param(
        "class Punto:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n"
        "    def doble(self):\n"
        "        return self.x * 2\n"
        "p = Punto(4)\n"
        "print(p.x, p.doble())",
        id="clase-con-metodos",
    ),
    pytest.param(
        "class A:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "class B(A):\n"
        "    def suma(self):\n"
        "        return self.v + 1\n"
        "print(B(2).suma())",
        id="clase-con-herencia",
    ),
    pytest.param(
        "class A:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def ver(self):\n"
        "        return self.v\n"
        "class B(A):\n"
        "    def __init__(self, v):\n"
        "        super().__init__(v * 2)\n"
        "print(B(3).ver())",
        id="super-en-herencia",
    ),
    pytest.param(
        "class C:\n"
        "    def __repr__(self):\n"
        "        return 'C!'\n"
        "print(C())",
        id="clase-con-repr",
    ),
    pytest.param(
        "class Caja:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "    def agregar(self, x):\n"
        "        self.items.append(x)\n"
        "        return self\n"
        "c = Caja()\n"
        "c.agregar(1).agregar(2)\n"
        "print(c.items)",
        id="clase-con-metodos-encadenados",
    ),
    pytest.param(
        "class L:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "    def __len__(self):\n"
        "        return self.n\n"
        "print(len(L(3)))",
        id="clase-con-len",
    ),
    pytest.param(
        "class R:\n"
        "    def __init__(self):\n"
        "        self.v = 7\n"
        "    def __eq__(self, otro):\n"
        "        return self.v == otro.v\n"
        "print(R() == R())",
        id="clase-con-eq",
    ),
    # --- gestor de contexto ---
    pytest.param(
        "class G:\n"
        "    def __enter__(self):\n"
        "        return 'dentro'\n"
        "    def __exit__(self, *args):\n"
        "        return False\n"
        "with G() as v:\n"
        "    print(v)",
        id="with-clase",
    ),
    pytest.param(
        "class G:\n"
        "    def __enter__(self):\n"
        "        return self\n"
        "    def __exit__(self, *args):\n"
        "        return True\n"
        "with G():\n"
        "    raise ValueError('x')\n"
        "print('suprimido')",
        id="with-suprime-excepcion",
    ),
    # --- generadores ---
    pytest.param(
        "def cuenta(n):\n"
        "    for i in range(n):\n"
        "        yield i\n"
        "print(list(cuenta(3)))",
        id="generador",
    ),
    pytest.param(
        "def delegado():\n"
        "    yield 1\n"
        "    yield 2\n"
        "def externo():\n"
        "    yield 0\n"
        "    yield from delegado()\n"
        "    yield 3\n"
        "print(list(externo()))",
        id="yield-from",
    ),
    pytest.param(
        "def pares(n):\n"
        "    for i in range(n):\n"
        "        if i % 2 == 0:\n"
        "            yield i\n"
        "print(list(pares(6)))",
        id="generador-con-filtro",
    ),
    # --- expresiones de asignación y expansiones ---
    pytest.param("print((n := 10))", id="walrus"),
    pytest.param(
        "if (n := 5) > 3:\n    print('mayor', n)",
        id="walrus-en-if",
    ),
    pytest.param("print([*[1, 2], 3])", id="expansion-en-lista"),
    pytest.param("print({*[1, 2], 3})", id="expansion-en-conjunto"),
    pytest.param("print((*[1, 2], 3))", id="expansion-en-tupla"),
    # --- expresiones generadoras ---
    pytest.param("print(list(x * 2 for x in range(3)))", id="expresion-generadora"),
    pytest.param(
        "print(sum(x for x in range(5) if x % 2 == 0))",
        id="expresion-generadora-con-filtro",
    ),
    pytest.param(
        "print(list(x + y for x in range(2) for y in range(2)))",
        id="expresion-generadora-doble",
    ),
    # --- decoradores ---
    pytest.param(
        "def deco(f):\n"
        "    def envoltura():\n"
        "        return '[' + f() + ']'\n"
        "    return envoltura\n"
        "@deco\n"
        "def g():\n"
        "    return 'x'\n"
        "print(g())",
        id="decorador-de-funcion",
    ),
    pytest.param(
        "def deco(f):\n"
        "    return f\n"
        "@deco\n"
        "@deco\n"
        "def g():\n"
        "    return 1\n"
        "print(g())",
        id="decoradores-apilados",
    ),
    pytest.param(
        "class A:\n"
        "    @property\n"
        "    def v(self):\n"
        "        return 7\n"
        "print(A().v)",
        id="decorador-property",
    ),
    pytest.param(
        "class A:\n"
        "    @classmethod\n"
        "    def c(cls):\n"
        "        return cls.__name__\n"
        "print(A.c())",
        id="decorador-classmethod",
    ),
    pytest.param(
        "class A:\n"
        "    @staticmethod\n"
        "    def s():\n"
        "        return 's'\n"
        "print(A.s())",
        id="decorador-staticmethod",
    ),
    # --- herencia de tipos de Python ---
    pytest.param(
        "class M(list):\n"
        "    pass\n"
        "m = M([1, 2])\n"
        "print(len(m), m[0], list(m))",
        id="herencia-de-list",
    ),
    pytest.param(
        "class M(list):\n"
        "    def total(self):\n"
        "        return sum(self)\n"
        "print(M([1, 2, 3]).total())",
        id="herencia-de-list-con-metodo",
    ),
    pytest.param(
        "class M(dict):\n"
        "    pass\n"
        "m = M({'a': 1})\n"
        "print(m['a'], len(m))",
        id="herencia-de-dict",
    ),
    # --- gestor de contexto sobre objetos de Python ---
    pytest.param(
        "import io\n"
        "with io.StringIO('hola') as f:\n"
        "    print(f.read())",
        id="with-sobre-objeto-de-python",
    ),
    pytest.param(
        "import io\n"
        "with io.StringIO('a') as a, io.StringIO('b') as b:\n"
        "    print(a.read(), b.read())",
        id="with-varios-gestores",
    ),
]


def _correr(argv: list) -> subprocess.CompletedProcess:
    # ``PYTHONIOENCODING=utf-8``: sin esto, la salida de los subprocesos usa la
    # codificación de la consola (cp1252 en Windows) y un fragmento que
    # imprime un caracter no latino falla al codificar, tanto en CPython como
    # en Cruthon. Fijar la codificación hace la comparación justa.
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(ROOT),
        env=entorno,
    )


def _cruthon(source: str) -> subprocess.CompletedProcess:
    """Ejecuta el fragmento con el ejecutable de Cruthon."""
    return _correr([sys.executable, "-m", "cruthon", "-c", source])


def _cpython(source: str) -> subprocess.CompletedProcess:
    """Ejecuta el mismo fragmento con CPython."""
    return _correr([sys.executable, "-c", source])


@pytest.mark.parametrize("source", PARIDAD)
def test_cruthon_imprime_lo_mismo_que_python(source: str):
    """El fragmento se ejecuta igual en Cruthon que en Python."""
    en_cruthon = _cruthon(source)
    en_python = _cpython(source)
    assert en_cruthon.returncode == 0, en_cruthon.stderr
    assert en_python.returncode == 0, en_python.stderr
    assert en_cruthon.stdout == en_python.stdout


@pytest.mark.parametrize("source", PARIDAD)
def test_cruthon_falla_igual_que_python(source: str):
    """Los errores de Python son errores de Python también en Cruthon."""
    en_cruthon = _cruthon(source)
    en_python = _cpython(source)
    assert (en_cruthon.returncode == 0) == (en_python.returncode == 0)


# --------------------------------------------------------------- la REPL
def _repl(lineas: list) -> str:
    """Corre la REPL con ``lineas`` y devuelve lo que imprimió."""
    import builtins
    import io
    from contextlib import redirect_stdout

    from cruthon.repl import CruthonREPL

    entrada = iter(lineas)
    original = builtins.input
    buffer = io.StringIO()

    def _input(prompt: str = "") -> str:
        try:
            return next(entrada)
        except StopIteration:
            raise EOFError from None

    builtins.input = _input
    try:
        with redirect_stdout(buffer):
            CruthonREPL().interact(banner=False)
    finally:
        builtins.input = original
    return buffer.getvalue()


def test_la_repl_continua_una_expresion_multilinea():
    """Un paréntesis abierto arrastra la línea lógica, como en la consola de Python.

    Es la regla de ``codeop.compile_command``: si la entrada no está
    completa, la consola pide otra línea en vez de fallar.
    """
    salida = _repl(["x = (1 +", "2)", "print(x)", "salir()"])
    assert "3" in salida
    assert "SyntaxError" not in salida


def test_la_repl_acepta_una_llamada_multilinea():
    salida = _repl(["print(", "1 +", "1", ")", "salir()"])
    assert "2" in salida
    assert "SyntaxError" not in salida


def test_la_repl_acepta_una_lista_multilinea():
    salida = _repl(["x = [1,", "2,", "3]", "print(x)", "salir()"])
    assert "[1, 2, 3]" in salida
    assert "SyntaxError" not in salida


# ------------------------------------------------------- archivos y codificación
def test_un_archivo_con_declaracion_de_codificacion_se_lee(tmp_path):
    """Un archivo cp1252 con su declaración se lee y se ejecuta."""
    from cruthon.cli import main

    ruta = tmp_path / "cafe.cruthon"
    ruta.write_bytes("# -*- coding: cp1252 -*-\nprint('caf\xe9')\n".encode("cp1252"))
    import contextlib
    import io

    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        status = main([str(ruta)])
    assert status == 0
    assert salida.getvalue().strip() == "café"


def test_un_archivo_sin_declaracion_y_no_utf8_falla_como_en_python(tmp_path):
    """Sin declaración de codificación, CPython también lo rechaza (PEP 263)."""
    from cruthon.cli import main

    ruta = tmp_path / "roto.cruthon"
    ruta.write_bytes(b"print('caf\xe9')\n")
    import contextlib
    import io

    error = io.StringIO()
    with contextlib.redirect_stderr(error):
        status = main([str(ruta)])
    assert status == 1
    mensaje = error.getvalue()
    assert "Traceback" not in mensaje
    assert "UnicodeDecodeError" not in mensaje
    assert "encoding declaration" in mensaje or "codificaci" in mensaje


def test_un_archivo_que_no_se_puede_decodificar_no_revienta_con_traceback(tmp_path):
    """Un archivo que declara UTF-8 pero trae otros bytes da un mensaje claro."""
    from cruthon.cli import main

    ruta = tmp_path / "roto.cruthon"
    ruta.write_bytes("# -*- coding: utf-8 -*-\nprint('caf\xe9')\n".encode("cp1252"))
    import contextlib
    import io

    error = io.StringIO()
    with contextlib.redirect_stderr(error):
        status = main([str(ruta)])
    assert status == 1
    mensaje = error.getvalue()
    assert "Traceback" not in mensaje
    assert "UnicodeDecodeError" not in mensaje
    assert "no se pudo leer" in mensaje


# ------------------------------------------------------------- memoria compartida
def _cruthon_salida(source: str) -> str:
    """Ejecuta un fragmento y devuelve sólo su salida."""
    proceso = _cruthon(source)
    assert proceso.returncode == 0, proceso.stderr
    return proceso.stdout


def test_la_interseccion_comparte_celda_de_verdad():
    """La celda compartida es la misma en los tres blobs, no una copia."""
    source = """
blob a:
    comun = 1

blob b:
    comun = 1

blob c = a & b:
    pass

a.comun = 7
print(a.comun, b.comun, c.comun)
"""
    assert _cruthon_salida(source).split() == ["7", "7", "7"]


def test_el_cuerpo_de_la_interseccion_escribe_en_la_celda_compartida():
    source = """
blob a:
    comun = 1

blob b:
    comun = 1

blob c = a & b:
    comun = 3

print(a.comun, b.comun, c.comun)
"""
    assert _cruthon_salida(source).split() == ["3", "3", "3"]


def test_intersecciones_encadenadas_comparten_la_misma_celda():
    source = """
blob a:
    v = 1

blob b:
    v = 1

blob c:
    v = 1

blob ab = a & b:
    pass

blob bc = b & c:
    pass

ab.v = 5
print(a.v, b.v, c.v, ab.v, bc.v)
"""
    assert _cruthon_salida(source).split() == ["5", "5", "5", "5", "5"]


def test_la_union_no_comparte_memoria():
    source = """
blob a:
    x = 1

blob b:
    pass

blob u = a | b:
    pass

u.x = 5
print(a.x)
"""
    assert _cruthon_salida(source).split() == ["1"]
