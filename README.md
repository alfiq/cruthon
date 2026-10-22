# Cruthon

**Cruthon: un lenguaje de programación basado en teoría de conjuntos.**

Cruthon es un lenguaje pequeño, construido sobre la estructura del repositorio
de CPython (léxico, analizador y evaluador escritos en Python), que sustituye
la jerarquía de clases por **álgebra de conjuntos**: las unidades del lenguaje
son *blobs* (conjuntos) que se combinan con intersección (`&`), unión (`|`),
diferencia (`-`) y diferencia simétrica (`^`).

Como Python, **Cruthon se interpreta**: el código se ejecuta conforme se lee,
instrucción por instrucción, tanto en un archivo como en la REPL.

> **Cómo se implementó.** El documento de diseño propone dos vías: un fork de
> CPython con un opcode `RESOLVE_MEMBER`, o un repositorio independiente que
> lo referencie. Aquí se tomó la segunda, y la decisión está escrita en
> [`docs/estrategia.md`](docs/estrategia.md), con el estado del fork
> experimental en [`fork/`](fork/).

---

## Instalación

```bash
pip install -e .          # instala el ejecutable `cruthon`
pip install -e ".[dev]"   # además: pytest, pypdf y reportlab
```

Sin instalar nada también funciona:

```bash
python -m cruthon ejemplos/01_carrito.cruthon
```

## Uso

```bash
cruthon                    # abre la REPL
cruthon ejemplo.cruthon    # ejecuta un archivo
cruthon -c "blob main: ..."# ejecuta un fragmento
cruthon -i ejemplo.cruthon # ejecuta el archivo y abre la REPL después
cruthon -p ejemplo.cruthon # imprime la tabla de pertenencia al terminar
```

### La REPL

La REPL se comporta como la consola de Python: cada instrucción se analiza y
se ejecuta en cuanto está completa, y su resultado se muestra mientras se
teclea. Los bloques (`blob`, `func`) se acumulan hasta que la línea siguiente
empieza en la primera columna o hasta que se escribe una línea en blanco, la
misma regla que usa `codeop` en CPython.

```console
$ cruthon
Cruthon 0.1.0 — lenguaje basado en teoría de conjuntos.
Interpretado como Python: cada instrucción se ejecuta conforme se lee.
Escribe 'salir()' para terminar, '%blobs' para ver los blobs vivos,
'%pertenencia [blob]' para ver la tabla de pertenencia de conjuntos.
>>> 1 + 1
2
>>> blob main:
...     token = "abc"
...
<blob main>
>>> main.token
'abc'
>>> salir()
```

Dos comandos de diagnóstico (no forman parte del lenguaje):

| Comando | Qué hace |
| --- | --- |
| `%blobs` | lista los blobs vivos |
| `%pertenencia [blob]` | imprime la tabla de pertenencia (nombre → blobs donde vive) |

## El lenguaje

### Blobs

Un *blob* es un conjunto con memoria propia. Sustituye a la clase *en el
modelo de conjuntos*: entre blobs no hay herencia, hay pertenencia geométrica.

```cruthon
blob main:
    blob backend:
        db = conectar_base_datos()

    blob frontend:
        func dibujar_pagina():
            print("html")

    blob carrito = frontend & backend:
        items = []
        total = 0.0
```

### Las cuatro operaciones de conjuntos

| Operación | Qué pasa en memoria | Qué pasa en ejecución |
| --- | --- | --- |
| `a & b` intersección | crea un blob nuevo con memoria propia, compartida por los dos blobs de origen (lectura y escritura por ambos) | funciona como canal controlado: sólo lo explícitamente puesto ahí se comparte |
| `a \| b` unión | no crea memoria compartida nueva; combina el espacio de ejecución completo de ambos blobs | equivale a correr dos programas bajo un mismo proceso, sin zona común de datos |
| `a - b` diferencia | blob que parte del contenido completo del primero y excluye, en tiempo de definición, los nombres que también existen en el segundo | ejecuta sólo lo exclusivo de un blob respecto a otro |
| `a ^ b` diferencia simétrica | memoria con lo que ninguno de los dos blobs comparte con el otro | útil para aislar el comportamiento único de cada blob |

El resultado de una operación de conjuntos es **un blob válido**: se puede
usar como operando en otra operación, sin límite de anidamiento. El conjunto
vacío también es legal.

### Visibilidad

La visibilidad es geométrica, no jerárquica:

* Un blob contenedor ve todo lo que está dentro de su área, incluidas las
  intersecciones que contiene.
* Dos blobs hermanos **no se ven** entre sí: lo que vive en `frontend` es
  inalcanzable desde `backend` y al revés.
* Un subconjunto que no es producto de ninguna intersección es *inalcanzable*
  desde fuera de su blob: no es privado por diseño, simplemente no vive dentro
  de ninguna zona compartida.
* Dos blobs que no se tocan pueden comunicarse encadenando intersecciones
  (`A & B` y `B & C`): `B` actúa de intermediario, sin que `A` y `C` se vean
  directamente.

Cuando un nombre no vive dentro del área del blob activo, el intérprete lanza
un `ScopeError`: el equivalente cruthoniano del `AttributeError` de CPython,
pero por razón de conjuntos y no de jerarquía.

### Scope

Una función se ejecuta **siempre con el ámbito del blob donde está físicamente
definida**, nunca con el de quien la llama. Quien llama sólo entra por la
puerta de su blob; adentro, la función ve lo que vive en el suyo.

### Clases

`class` existe y funciona como en Python, con una diferencia de ámbito: un
método se ejecuta con el ámbito del blob donde se definió la clase, no con el
de quien llama. Es la misma regla que rige a las funciones.

```cruthon
class Contador:
    def __init__(self, inicio=0):
        self.valor = inicio

    def sumar(self, n):
        self.valor += n
        return self.valor

c = Contador(10)
print(c.sumar(5))          # 15
```

Hay herencia, `super()`, decoradores (`@property`, `@classmethod`,
`@staticmethod` y los propios) y los métodos especiales de Python
(`__init__`, `__len__`, `__getitem__`, `__iter__`, `__eq__`…). También se
pueden heredar tipos de Python: la instancia se apoya entonces en un objeto
nativo.

```cruthon
class Pila(list):
    def top(self):
        return self[-1]

p = Pila([1, 2, 3])
print(p.top(), len(p))     # 3 3
```

Pero **`class` no interactúa con los blobs**: un blob no es una clase y una
clase no es un blob. Es la frontera explícita del lenguaje, y el motivo por
el que el documento de diseño puede decir que el blob *sustituye* a la clase
sin tener que prohibirse la clase.

### La tabla de pertenencia

Cada blob mantiene una tabla que dice **en qué blobs vive cada nombre**. Es el
sustituto del MRO de CPython: en vez de una cadena lineal de herencia, cada
nombre dice dónde está.

```console
$ cruthon -p -c "blob main:
...     blob a:
...         x = 1
...     blob b:
...         x = 2"

Tabla de pertenencia (nombre -> blobs donde vive):
  a: main
  b: main
  main: __main__
  x: a, b
```

## Diferencias respecto a CPython estándar

El punto 7 del documento pide "documentar las diferencias respecto a CPython
estándar". Esta es la lista completa.

### El lenguaje comparte las reglas de Python, con dos construcciones propias

Cruthon **no** es un superconjunto de Python. Comparte casi toda la sintaxis
y se ejecuta como Python, pero difiere en tres puntos concretos:

1. Los cuatro operadores `&`, `|`, `-` y `^` entre dos *blobs* ejecutan
   operaciones de conjuntos. Entre cualquier otro valor son los operadores
   bit a bit de Python, sin cambios.
2. El ámbito de una función es el blob donde está definida, no el espacio de
   nombres donde se escribe.
3. Un nombre que no vive dentro del área del blob activo produce un
   `ScopeError`, no un `NameError` ni un `AttributeError`.

Las dos construcciones *nuevas* son:

| Cruthon | Equivalente en CPython |
| --- | --- |
| `blob nombre:` | una clase, sin herencia implícita |
| `blob nombre = a & b:` | una intersección entre dos clases |

`func` es un alias de `def`. `class` también existe, con herencia, `super()`,
decoradores, herencia de tipos de Python y los métodos especiales de Python
(ver [Clases](#clases)); pero **no** interactúa con los blobs: un blob no es
una clase y una clase no es un blob.

### Lo que cambia al definir el lenguaje

* **El scope.** Una función se ejecuta siempre con el ámbito del blob donde
  está físicamente definida. En Python, una función ve el espacio de nombres
  donde se *define*, pero hereda el global; aquí el ámbito es el blob, y el
  de quien llama no se mezcla. Esto también afecta a los métodos: un método
  se ejecuta con el ámbito del blob donde se definió la clase.
* **El error de alcance.** Cuando un nombre no vive dentro del área del blob
  activo, Cruthon lanza `ScopeError` (un `RuntimeError`) en vez de un
  `AttributeError` o un `NameError`. El nombre existe en el programa, pero
  en otro blob: es un problema de geometría, no de jerarquía.
* **Los cuatro operadores.** `&`, `|`, `-` y `^` entre dos *blobs* ejecutan
  operaciones de conjuntos. Entre cualquier otro valor son los operadores
  bit a bit de Python, sin cambios.

### Lo que no está implementado

* `nonlocal` funciona, pero `global` sólo aplica al ámbito global del
  intérprete.
* La biblioteca estándar se importa completa, pero sólo un subconjunto de
  *builtins* está disponible dentro de un blob (la lista está en
  [`cruthon/builtins.py`](cruthon/builtins.py)); lo que no está, no se ve.
* No hay `async`/`await` ni `match`.
* El intérprete no genera bytecode: es un evaluador de árbol de sintaxis,
  como el de un intérprete pedagogy. La diferencia observable es el
  rendimiento, no la semántica.

### Lo que se verificó

[`tests/test_paridad.py`](tests/test_paridad.py) ejecuta los mismos
fragmentos en Cruthon y en CPython y compara la salida byte a byte. Cubre
escapes de cadena, f-strings, comprehensions (y expresiones generadoras),
`del`, `global`, `nonlocal`, closures, `*args`/`**kwargs`, argumentos por
palabra clave, slices, excepciones (`raise`, `except` con tupla, `finally`),
`import` de la biblioteca estándar, las operaciones aritméticas, las clases
(herencia, `super()`, `__init__`, `__len__`, `__eq__`, `__repr__`), los
decoradores (`@property`, `@classmethod`, `@staticmethod`), los generadores
(`yield`, `yield from`), el gestor de contexto (`with`, con uno y con varios
gestores) y la herencia de tipos de Python (`class M(list):`).

## Arquitectura

El intérprete sigue la estructura del repositorio de CPython, pero escrito
desde cero en Python puro (sin bytecode, sin C):

| Módulo | Equivalente en CPython | Qué hace |
| --- | --- | --- |
| [`cruthon/lexer.py`](cruthon/lexer.py) | `Parser/tokenizer.c` | tokenizador: sangría al estilo de Python, cadenas, f-strings, números |
| [`cruthon/nodes.py`](cruthon/nodes.py) | `Parser/ast.h` | nodos del árbol de sintaxis |
| [`cruthon/parser.py`](cruthon/parser.py) | `Parser/parser.c` | analizador descendente recursivo; añade `blob` y las operaciones de conjuntos |
| [`cruthon/blobs.py`](cruthon/blobs.py) | `Objects/typeobject.c` | el modelo de blobs: memoria, tabla de pertenencia y las cuatro operaciones |
| [`cruthon/classes.py`](cruthon/classes.py) | `Objects/typeobject.c` + `Objects/classobject.c` | las clases de Python: MRO con linealización C3, instancias, `super()` y el protocolo de métodos especiales |
| [`cruthon/interpreter.py`](cruthon/interpreter.py) | `Python/compile.c` + `Python/ceval.c` | evaluador: resuelve nombres por pertenencia a conjuntos en vez de por MRO |
| [`cruthon/repl.py`](cruthon/repl.py) | `Lib/code.py` | la REPL: lee, analiza y ejecuta conforme se lee |
| [`cruthon/cli.py`](cruthon/cli.py) | `Programs/_freeze_module.c` | el ejecutable `cruthon` |

La idea central del documento de diseño —sustituir el MRO por la resolución
por pertenencia a conjuntos— está implementada en
`Blob.membership_table()` y en `Blob.lookup()`: cuando un nombre no está en la
tabla del blob activo ni en ninguna intersección alcanzable, el intérprete
lanza un error de alcance.

## Ejemplos

Los siete ejemplos del documento de diseño están en [`examples/`](examples/),
ejecutados y con sus resultados guardados en `examples/expected/`. Las
modificaciones menores respecto al PDF (funciones que el documento usa sin
definir, funciones que define sin llamar y la ausencia de `print`) están
anotadas en cada archivo y resumidas en
[`examples/README.md`](examples/README.md).

```bash
python tools/run_examples.py            # ejecuta y compara con lo guardado
python tools/run_examples.py --update   # regenera los resultados
```

## Pruebas

```bash
python -m pytest            # 265 pruebas: léxico, sintaxis, semántica, REPL, CLI, paridad, ejemplos y PDF
```

## El documento de diseño

[`Cruthon un lenguaje basado en teoría de conjuntos.pdf`](Cruthon%20un%20lenguaje%20basado%20en%20teor%C3%ADa%20de%20conjuntos.pdf)
es el documento original (11 páginas). La última página se añadió con
[`tools/pdf_interpreted_page.py`](tools/pdf_interpreted_page.py) y documenta
explícitamente que **Cruthon se interpreta como Python y se ejecuta conforme
se lee**. El texto extraído del PDF está en
[`docs/especificacion-texto.md`](docs/especificacion-texto.md).

## Estado y preguntas abiertas

El documento de diseño deja varias preguntas abiertas; este repositorio
implementa la parte que se puede decidir hoy:

* **Resuelto en el documento y aquí implementado:** el scope por definición
  física de la función, el resultado de una operación de conjuntos como blob
  válido y encadenable, el conjunto vacío como resultado legal, y el
  encadenamiento de intersecciones como puente entre blobs que no se tocan.
* **Decidido aquí por coherencia con los ejemplos:** un blob contenedor ve
  todo lo que está dentro de su área (incluidos sus sub-blobs), mientras que
  los blobs hermanos no se ven entre sí. Es lo que exige el caso de prueba
  mínimo del PDF (`frontend` no ve `db`, `backend` no ve `dibujar_pagina`).
* **Sintaxis concreta:** `blob nombre:` y `blob nombre = a & b:` son la
  sintaxis propuesta en el PDF; el documento la marca como ilustrativa, y aquí
  queda fijada con esas dos formas.
* **Pendiente:** el IDE visual con diagrama de Venn en vivo, la
  observabilidad en tiempo de ejecución y el servidor MCP para asistentes de
  código.

## Licencia

Apache License 2.0 (ver [LICENSE](LICENSE)). El documento de diseño y su
contenido conservan los derechos que el propio documento declara.
