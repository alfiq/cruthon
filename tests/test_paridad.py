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
    # --- suites en una línea ---
    pytest.param("x = 5\nif x > 1: y = 10\nprint(y)", id="suite-en-una-linea-if"),
    pytest.param("def f(): return 42\nprint(f())", id="suite-en-una-linea-def"),
    pytest.param("class A: pass\nprint(A.__name__)", id="suite-en-una-linea-class"),
    pytest.param(
        "r = []\nfor i in range(3): r.append(i)\nwhile False: r.append(9)\n"
        "print(r)",
        id="suite-en-una-linea-for-y-while",
    ),
    pytest.param(
        "r = []\n"
        "try: r.append('t')\n"
        "except Exception: r.append('x')\n"
        "else: r.append('y')\n"
        "finally: r.append('f')\n"
        "print(r)",
        id="suite-en-una-linea-try",
    ),
    # --- try/except/else/finally ---
    pytest.param(
        "r = []\n"
        "try:\n"
        "    r.append('cuerpo')\n"
        "except ValueError:\n"
        "    r.append('except')\n"
        "else:\n"
        "    r.append('else')\n"
        "finally:\n"
        "    r.append('finally')\n"
        "print(r)",
        id="try-except-else-finally",
    ),
    pytest.param(
        "r = []\n"
        "try:\n"
        "    1 / 0\n"
        "except ZeroDivisionError:\n"
        "    r.append('except')\n"
        "else:\n"
        "    r.append('else')\n"
        "print(r)",
        id="try-except-else-con-error",
    ),
    pytest.param(
        "try:\n"
        "    raise ValueError('x')\n"
        "except (TypeError, ValueError):\n"
        "    print('tupla de tipos')",
        id="except-con-tupla-de-tipos",
    ),
    # --- atributos de instancia ---
    pytest.param(
        "class Caja:\n"
        "    def __init__(self):\n"
        "        self._v = 1\n"
        "        self.publico = 2\n"
        "    def ver(self):\n"
        "        return self._v + self.publico\n"
        "c = Caja()\n"
        "c._v = 10\n"
        "print(c.ver())",
        id="atributo-privado-de-instancia",
    ),
    # --- iterador propio ---
    pytest.param(
        "class Contador:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "        self.i = 0\n"
        "    def __iter__(self):\n"
        "        return self\n"
        "    def __next__(self):\n"
        "        if self.i >= self.n:\n"
        "            raise StopIteration\n"
        "        self.i += 1\n"
        "        return self.i\n"
        "print(list(Contador(3)))",
        id="iterador-propio",
    ),
    # --- property con setter ---
    pytest.param(
        "class Cuenta:\n"
        "    def __init__(self):\n"
        "        self._saldo = 0\n"
        "    @property\n"
        "    def saldo(self):\n"
        "        return self._saldo\n"
        "    @saldo.setter\n"
        "    def saldo(self, valor):\n"
        "        if valor < 0:\n"
        "            raise ValueError('negativo')\n"
        "        self._saldo = valor\n"
        "c = Cuenta()\n"
        "c.saldo = 10\n"
        "print(c.saldo)",
        id="property-con-setter",
    ),
    pytest.param(
        "class Cuenta:\n"
        "    def __init__(self):\n"
        "        self._saldo = 0\n"
        "    @property\n"
        "    def saldo(self):\n"
        "        return self._saldo\n"
        "    @saldo.setter\n"
        "    def saldo(self, valor):\n"
        "        if valor < 0:\n"
        "            raise ValueError('negativo')\n"
        "        self._saldo = valor\n"
        "c = Cuenta()\n"
        "try:\n"
        "    c.saldo = -1\n"
        "except ValueError:\n"
        "    print('rechazado')",
        id="property-con-setter-que-rechaza",
    ),
    # --- anotaciones de variable ---
    pytest.param("x: int = 5\nprint(x)", id="anotacion-de-variable"),
    pytest.param("x: int\nprint('ok')", id="anotacion-sin-valor"),
    # --- literales y expansiones ---
    pytest.param("print(Ellipsis is ...)", id="literal-ellipsis"),
    pytest.param("print({**{'a': 1}, 'b': 2})", id="expansion-en-diccionario"),
    pytest.param("z = 3j\nprint(z, z * 2)", id="literal-complejo"),
    # --- f-string de depuración ---
    pytest.param("x = 7\nprint(f'{x=}')", id="f-string-de-depuracion"),
    pytest.param("x = 7\nprint(f'{x=:.2f}')", id="f-string-de-depuracion-con-formato"),
    pytest.param("x = 'a'\nprint(f'{x=!r}')", id="f-string-de-depuracion-con-conversion"),
    # --- super explícito ---
    pytest.param(
        "class A:\n"
        "    def h(self):\n"
        "        return 'A'\n"
        "class B(A):\n"
        "    def h(self):\n"
        "        return 'B' + super(B, self).h()\n"
        "print(B().h())",
        id="super-explicito",
    ),
    # --- excepciones propias ---
    pytest.param(
        "class E(ValueError):\n"
        "    pass\n"
        "try:\n"
        "    raise E('mal')\n"
        "except ValueError as e:\n"
        "    print('capturada', e)",
        id="excepcion-propia-que-hereda-de-python",
    ),
    pytest.param(
        "try:\n"
        "    raise StopIteration\n"
        "except StopIteration:\n"
        "    print('ok')",
        id="raise-de-una-clase-sin-instanciar",
    ),
    # --- generación de nombres y alcance ---
    pytest.param(
        "try:\n"
        "    x\n"
        "except NameError:\n"
        "    print('name')",
        id="except-name-error",
    ),
    pytest.param(
        "x = 1\n"
        "def f():\n"
        "    try:\n"
        "        y\n"
        "    except NameError:\n"
        "        return 'name'\n"
        "print(f())",
        id="except-name-error-en-funcion",
    ),
    pytest.param("x = y = 5\nprint(x, y)", id="asignacion-encadenada"),
    pytest.param(
        "a = b = c = 1\nprint(a, b, c)",
        id="asignacion-encadenada-triple",
    ),
    # --- generadores entre paréntesis ---
    pytest.param(
        "print(list((i for i in range(3))))",
        id="expresion-generadora-entre-parentecis",
    ),
    pytest.param(
        "print(sum((i * i for i in range(4))))",
        id="expresion-generadora-entre-parentecis-con-suma",
    ),
    # --- builtins que faltaban ---
    pytest.param("print(pow(2, 3), pow(2, 3, 5))", id="builtin-pow"),
    pytest.param("print(complex(1, 2), complex(3))", id="builtin-complex"),
    pytest.param("print(type(globals()).__name__)", id="builtin-globals"),
    pytest.param(
        "def f():\n"
        "    v = 1\n"
        "    return sorted(locals())\n"
        "print(f())",
        id="builtin-locals",
    ),
    # --- anotaciones de función ---
    pytest.param(
        "def f(x: int, y: str = 'a') -> bool:\n"
        "    return True\n"
        "print(f.__annotations__)",
        id="anotaciones-de-funcion",
    ),
    pytest.param(
        "def f():\n"
        "    pass\n"
        "print(f.__annotations__)",
        id="anotaciones-vacias",
    ),
    pytest.param(
        "def f(x: int) -> str:\n"
        "    return 'a'\n"
        "print(f.__annotations__['x'] == int)",
        id="anotacion-de-un-parametro",
    ),
    pytest.param(
        "def f(*args: int, **kw: str) -> None:\n"
        "    return None\n"
        "print(sorted(f.__annotations__))",
        id="anotaciones-de-args-y-kwargs",
    ),
    # --- docstrings ---
    pytest.param(
        "def f():\n"
        "    '''Doc.'''\n"
        "    return 1\n"
        "print(f.__doc__)",
        id="docstring-de-funcion",
    ),
    pytest.param(
        "def f():\n"
        "    return 1\n"
        "print(repr(f.__doc__))",
        id="docstring-ausente-de-funcion",
    ),
    pytest.param(
        "class C:\n"
        "    '''Doc.'''\n"
        "print(C.__doc__)",
        id="docstring-de-clase",
    ),
    pytest.param(
        "class C:\n"
        "    '''Doc.'''\n"
        "print(C().__doc__)",
        id="docstring-de-instancia",
    ),
    pytest.param(
        "class C:\n"
        "    def m(self):\n"
        "        '''Doc.'''\n"
        "print(C.m.__doc__)",
        id="docstring-de-metodo",
    ),
    # --- nombre y docstring del módulo ---
    pytest.param("print(__name__)", id="nombre-del-modulo"),
    pytest.param("'''Mod.'''\nprint(__doc__)", id="docstring-del-modulo"),
    # --- eval y exec ---
    pytest.param("print(eval('1 + 1'))", id="builtin-eval"),
    pytest.param("x = 5\nprint(eval('x * 2'))", id="builtin-eval-con-contexto"),
    pytest.param("exec('y = 7')\nprint(y)", id="builtin-exec"),
    pytest.param(
        "def f():\n"
        "    x = 3\n"
        "    return eval('x * 2')\n"
        "print(f())",
        id="builtin-eval-en-funcion",
    ),
    pytest.param(
        "def f():\n"
        "    exec('y = 5')\n"
        "    try:\n"
        "        return y\n"
        "    except NameError:\n"
        "        return 'NameError'\n"
        "print(f())",
        id="builtin-exec-en-funcion-no-alcanza-el-ambito",
    ),
    # --- destinos con * y anidados ---
    pytest.param("*a, b = [1, 2, 3]\nprint(a, b)", id="desempaquetado-con-estrella-izquierda"),
    pytest.param("a, *b = [1, 2, 3]\nprint(a, b)", id="desempaquetado-con-estrella-derecha"),
    pytest.param(
        "for *a, b in [(1, 2), (3, 4)]:\n"
        "    print(a, b)",
        id="for-con-destino-estrellado",
    ),
    pytest.param(
        "(a, *b), c = (1, 2, 3), 4\n"
        "print(a, b, c)",
        id="desempaquetado-anidado-con-estrella",
    ),
    pytest.param(
        "a, (b, *c) = 1, (2, 3, 4)\n"
        "print(a, b, c)",
        id="desempaquetado-anidado-en-subtupla",
    ),
    # --- operadores aumentados en instancias ---
    pytest.param(
        "class C:\n"
        "    def __init__(self):\n"
        "        self.v = 0\n"
        "    def __iadd__(self, o):\n"
        "        self.v += o\n"
        "        return self\n"
        "    def __repr__(self):\n"
        "        return 'C(%d)' % self.v\n"
        "c = C()\n"
        "c += 5\n"
        "print(c)",
        id="iadd-en-instancia",
    ),
    pytest.param(
        "class C:\n"
        "    def __init__(self):\n"
        "        self.v = 0\n"
        "    def __iadd__(self, o):\n"
        "        return NotImplemented\n"
        "    def __add__(self, o):\n"
        "        return self.v + o\n"
        "c = C()\n"
        "c += 5\n"
        "print(c)",
        id="iadd-devuelve-notimplemented",
    ),
    pytest.param(
        "class C:\n"
        "    def __init__(self):\n"
        "        self.v = 0\n"
        "    def __imul__(self, o):\n"
        "        self.v *= o\n"
        "        return self\n"
        "c = C()\n"
        "c *= 4\n"
        "print(c.v)",
        id="imul-en-instancia",
    ),
    pytest.param(
        "class C:\n"
        "    def __init__(self):\n"
        "        self.v = 0\n"
        "    def __isub__(self, o):\n"
        "        self.v -= o\n"
        "        return self\n"
        "c = C()\n"
        "c -= 3\n"
        "print(c.v)",
        id="isub-en-instancia",
    ),
    # --- decoradores de clase ---
    pytest.param(
        "def deco(c):\n"
        "    c.marca = 1\n"
        "    return c\n"
        "@deco\n"
        "class C:\n"
        "    pass\n"
        "print(C.marca)",
        id="decorador-de-clase",
    ),
    pytest.param(
        "def uno(c):\n"
        "    print('uno')\n"
        "    return c\n"
        "def dos(c):\n"
        "    print('dos')\n"
        "    return c\n"
        "@uno\n"
        "@dos\n"
        "class C:\n"
        "    pass",
        id="decoradores-de-clase-apilados",
    ),
    pytest.param(
        "def deco(c):\n"
        "    return c\n"
        "@deco\n"
        "class C:\n"
        "    def m(self):\n"
        "        return 5\n"
        "print(C().m())",
        id="decorador-de-clase-con-metodos",
    ),
    # -- protocolo de métodos especiales --
    pytest.param(
        "class C:\n"
        "    def __getattr__(self, n):\n"
        "        return n.upper()\n"
        "print(C().abc)",
        id="getattr-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __setattr__(self, n, v):\n"
        "        object.__setattr__(self, n, v * 2)\n"
        "c = C()\n"
        "c.x = 5\n"
        "print(c.x)",
        id="setattr-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __delattr__(self, n):\n"
        "        object.__delattr__(self, n)\n"
        "c = C()\n"
        "c.x = 5\n"
        "del c.x\n"
        "print('borrado')",
        id="delattr-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __format__(self, spec):\n"
        "        return 'F' + spec\n"
        "print(f'{C():>5}')",
        id="format-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __index__(self):\n"
        "        return 3\n"
        "print([0, 1, 2, 3, 4][C():])",
        id="index-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __int__(self):\n"
        "        return 5\n"
        "print(int(C()))",
        id="int-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __float__(self):\n"
        "        return 2.5\n"
        "print(float(C()))",
        id="float-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __bytes__(self):\n"
        "        return b'B'\n"
        "print(bytes(C()))",
        id="bytes-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __reversed__(self):\n"
        "        return iter([3, 2, 1])\n"
        "print(list(reversed(C())))",
        id="reversed-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __round__(self, n=None):\n"
        "        return 4\n"
        "print(round(C()))",
        id="round-del-usuario",
    ),
    pytest.param(
        "import math\n"
        "class C:\n"
        "    def __trunc__(self):\n"
        "        return 4\n"
        "print(math.trunc(C()))",
        id="trunc-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __delitem__(self, i):\n"
        "        print('del', i)\n"
        "del C()[1]",
        id="delitem-del-usuario",
    ),
    pytest.param(
        "class C:\n"
        "    def __init__(self):\n"
        "        self.x = 1\n"
        "print(sorted(vars(C())))",
        id="vars-de-la-instancia",
    ),
    # -- reglas de llamada --
    pytest.param(
        "def f(a, /, b):\n    return (a, b)\nprint(f(1, 2))",
        id="parametro-solo-posicional",
    ),
    pytest.param(
        "def f(a, /, b):\n"
        "    return (a, b)\n"
        "try:\n"
        "    f(a=1, b=2)\n"
        "except TypeError:\n"
        "    print('TypeError')",
        id="parametro-solo-posicional-por-palabra-clave",
    ),
    pytest.param(
        "def f(a):\n"
        "    return a\n"
        "try:\n"
        "    f(1, 2)\n"
        "except TypeError:\n"
        "    print('TypeError')",
        id="argumentos-de-mas",
    ),
    pytest.param(
        "def f(a):\n"
        "    return a\n"
        "try:\n"
        "    f()\n"
        "except TypeError:\n"
        "    print('TypeError')",
        id="argumento-que-falta",
    ),
    pytest.param(
        "def f(a):\n"
        "    return a\n"
        "try:\n"
        "    f(b=1)\n"
        "except TypeError:\n"
        "    print('TypeError')",
        id="palabra-clave-inesperada",
    ),
    pytest.param("print(globals().get('__name__'))", id="globals-con-name"),
    pytest.param("print(globals().get('__doc__'))", id="globals-con-doc"),
    # -- valores por defecto: se evalúan al definir, no al llamar --
    pytest.param(
        "def f(x=[]):\n"
        "    x.append(1)\n"
        "    return x\n"
        "print(f(), f())",
        id="el-valor-por-defecto-se-evalua-al-definir",
    ),
    pytest.param(
        "def f(x=print('evaluado')):\n"
        "    return x\n"
        "f()\n"
        "f()",
        id="el-valor-por-defecto-se-evalua-una-sola-vez",
    ),
    pytest.param(
        "f = lambda x, y=1: x + y\nprint(f(2), f(2, 3))",
        id="lambda-con-valor-por-defecto",
    ),
    # -- errores de sintaxis de eval y exec, capturables --
    pytest.param(
        "try:\n"
        "    eval('1 +')\n"
        "except SyntaxError:\n"
        "    print('SyntaxError')",
        id="syntaxerror-de-eval-se-captura",
    ),
    pytest.param(
        "try:\n"
        "    exec('def')\n"
        "except SyntaxError:\n"
        "    print('SyntaxError')",
        id="syntaxerror-de-exec-se-captura",
    ),
    # -- eval y exec con diccionario propio --
    pytest.param(
        "ns = {}\nexec('x = 1', ns)\nprint(ns['x'])",
        id="exec-con-diccionario-propio",
    ),
    pytest.param(
        "ns = {}\nexec('def f():\\n    return 7', ns)\nprint(ns['f']())",
        id="exec-con-diccionario-propio-define-funciones",
    ),
    pytest.param(
        "ns = {'x': 4}\nprint(eval('x * 2', ns))",
        id="eval-con-diccionario-propio",
    ),
    # -- atributos de una función --
    pytest.param(
        "def f():\n"
        "    pass\n"
        "f.etiqueta = 'x'\n"
        "print(f.etiqueta)",
        id="atributo-dinamico-en-una-funcion",
    ),
    # -- el __init__ del usuario --
    pytest.param(
        "class C:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "print(C(3).v)",
        id="init-del-usuario",
    ),
    # -- el generador termina con StopIteration --
    pytest.param(
        "def g():\n"
        "    yield 1\n"
        "it = g()\n"
        "next(it)\n"
        "try:\n"
        "    it.send('x')\n"
        "except StopIteration as e:\n"
        "    print(repr(str(e)))",
        id="stopiteration-sin-valor",
    ),
    pytest.param(
        "def g():\n"
        "    yield 1\n"
        "    return 5\n"
        "it = g()\n"
        "next(it)\n"
        "try:\n"
        "    it.send(None)\n"
        "except StopIteration as e:\n"
        "    print(e.value)",
        id="stopiteration-con-valor",
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


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("*a, *b = [1, 2, 3]\nprint(a, b)", id="dos-estrellas-en-el-mismo-nivel"),
        pytest.param(
            "for *a, *b in [(1, 2)]:\n    print(a, b)",
            id="dos-estrellas-en-el-for",
        ),
        pytest.param(
            "[*a, *b] = [1, 2, 3]\nprint(a, b)",
            id="dos-estrellas-en-una-lista",
        ),
    ],
)
def test_dos_destinos_con_estrella_en_el_mismo_nivel_son_error_de_sintaxis(source: str):
    """Como en Python, dos ``*`` en el mismo nivel son un error de sintaxis."""
    en_cruthon = _cruthon(source)
    en_python = _cpython(source)
    assert en_python.returncode != 0
    assert en_cruthon.returncode != 0
    assert "SyntaxError" in en_cruthon.stderr


@pytest.mark.parametrize(
    "source",
    [
        pytest.param("yield 1\n", id="yield-fuera-de-funcion"),
        pytest.param("print((yield) if False else 1)\n", id="yield-en-una-expresion"),
        pytest.param("class C:\n    yield 1\n", id="yield-en-el-cuerpo-de-una-clase"),
    ],
)
def test_el_yield_fuera_de_una_funcion_es_error_de_sintaxis(source: str):
    """Como en Python, un ``yield`` fuera de una función no se analiza."""
    en_cruthon = _cruthon(source)
    en_python = _cpython(source)
    assert en_python.returncode != 0
    assert en_cruthon.returncode != 0
    assert "SyntaxError" in en_cruthon.stderr


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
