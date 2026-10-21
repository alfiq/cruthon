# Cruthon

**Cruthon: un lenguaje de programación basado en teoría de conjuntos.**

Cruthon es un lenguaje pequeño, construido sobre la estructura del repositorio
de CPython (léxico, analizador y evaluador escritos en Python), que sustituye
la jerarquía de clases por **álgebra de conjuntos**: las unidades del lenguaje
son *blobs* (conjuntos) que se combinan con intersección (`&`), unión (`|`),
diferencia (`-`) y diferencia simétrica (`^`).

Como Python, **Cruthon se interpreta**: el código se ejecuta conforme se lee,
instrucción por instrucción, tanto en un archivo como en la REPL.

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

Un *blob* es un conjunto con memoria propia. Reemplaza a la clase: no hay
herencia, hay pertenencia geométrica.

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
  a: __main__
  b: __main__
  main: __main__
  x: a, b
```

## Arquitectura

El intérprete sigue la estructura del repositorio de CPython, pero escrito
desde cero en Python puro (sin bytecode, sin C):

| Módulo | Equivalente en CPython | Qué hace |
| --- | --- | --- |
| [`cruthon/lexer.py`](cruthon/lexer.py) | `Parser/tokenizer.c` | tokenizador: sangría al estilo de Python, cadenas, f-strings, números |
| [`cruthon/nodes.py`](cruthon/nodes.py) | `Parser/ast.h` | nodos del árbol de sintaxis |
| [`cruthon/parser.py`](cruthon/parser.py) | `Parser/parser.c` | analizador descendente recursivo; añade `blob` y las operaciones de conjuntos |
| [`cruthon/blobs.py`](cruthon/blobs.py) | `Objects/typeobject.c` | el modelo de blobs: memoria, tabla de pertenencia y las cuatro operaciones |
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
python -m pytest            # 82 pruebas: léxico, sintaxis, semántica, REPL, CLI, ejemplos y PDF
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
