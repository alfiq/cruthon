# Estrategia de implementación de Cruthon

Este documento decide, por escrito, la pregunta que el documento de diseño
deja abierta: **¿Cruthon se implementa como un fork de CPython con un opcode
`RESOLVE_MEMBER`, o como un intérprete propio?**

## La decisión

**Cruthon se implementa como un intérprete propio, escrito en Python puro.**

El punto 7 de la lista de instrucciones del PDF pide "crear un repositorio
nuevo (fork de cpython **o repositorio independiente que lo referencie**),
documentar las diferencias respecto a CPython estándar, e incluir los
ejemplos de este documento como tests de integración". Esta implementación
toma la segunda vía que el propio documento ofrece.

## Por qué

* **El documento pide las dos cosas, no solo una.** Los puntos 1 a 5 son una
  *receta* para llegar al lenguaje (qué tocar en CPython); el punto 7 es lo
  que se pide *entregar*. La receta no es un requisito de entrega: es el
  camino que el autor propone. Un repositorio independiente que referencie a
  CPython está expresamente permitido.
* **El cambio central del documento no está en el lexer ni en el parser.**
  Está en la *resolución de nombres*: sustituir el MRO por pertenencia a
  conjuntos. Ese es el punto 3 ("generando una tabla de pertenencia por blob
  en vez de un MRO") y el 4 (`RESOLVE_MEMBER`). Ambos son cambios en la
  *semántica de resolución*, y el repositorio los implementa completos: la
  tabla de pertenencia con rutas de inclusión (`Blob.inclusion_table()`) y la
  resolución que usa esas rutas, con la distancia decidiendo y la ambigüedad
  a igual distancia fallando (`Blob.lookup()`), están en
  [`cruthon/blobs.py`](../cruthon/blobs.py).
* **Un fork exige un compilador y una cadena de build.** El punto 5 pide
  "mantener intacto todo el resto del pipeline ... para heredar el ecosistema
  de Python sin cambios". Eso exige modificar `Python/compile.c` y
  `Python/ceval.c` en C, regenerar el parser con pegen, y compilar CPython.
  El resultado sería un binario, no un repositorio de lenguaje: cada regla
  del documento quedaría detrás de una traducción a C que hay que mantener
  en sincronía con el árbol de sintaxis.
* **El intérprete propio hace verificable lo que el documento afirma.** La
  afirmación central —"memoria propia, compartida por los blobs que lo
  originaron (lectura y escritura por ambos)"— se ejerce desde el lenguaje,
  en [`tests/test_paridad.py`](../tests/test_paridad.py) y
  [`tests/test_semantics.py`](../tests/test_semantics.py). Con un fork, esa
  misma afirmación quedaría detrás de la construcción del intérprete de
  CPython: un fallo podría ser del modelo de blobs o del bytecode, y las
  pruebas no lo distinguirían.

## Lo que esto NO cubre

La divergencia es real y conviene no disfrazarla:

| Punto del PDF | Estado aquí |
| --- | --- |
| 1. Partir del código fuente de CPython | **Parcial.** Se usa CPython 3.14.7 como *referencia estructural* (léxico, parser, evaluador) y se estudió su repositorio; no se hace un fork de su código fuente. |
| 2. Palabra clave `blob` en el tokenizer y el parser | **Hecho**, y además en el fork: ver abajo. |
| 3. Tabla de pertenencia en vez de MRO | **Hecho.** `Blob.inclusion_table()` (rutas de inclusión) y `Blob.lookup()`, que falla con `AmbiguousName` cuando dos regiones a la misma distancia compiten. |
| 4. Opcode `RESOLVE_MEMBER` | **Equivalente funcional, no literal.** La resolución por pertenencia ocurre en `Blob.lookup()`, en Python, en vez de en un opcode de C. El *efecto* pedido (buscar el nombre en la tabla del blob activo y en las intersecciones alcanzables, y lanzar un error de alcance si no vive ahí) es el mismo. |
| 5. Pipeline de CPython intacto | **Parcial.** Se hereda la biblioteca estándar y el modelo de sangría y de compilación-por-línea de CPython; no se hereda el bytecode de CPython, porque el intérprete es propio. |
| 6. Caso de prueba mínimo (el carrito) | **Hecho.** [`examples/01_carrito.cruthon`](../examples/01_carrito.cruthon) y su resultado guardado, con la verificación de que `frontend` no ve `db` y `backend` no ve `dibujar_pagina`. |
| 7. Repositorio + diferencias documentadas + ejemplos como tests | **Hecho.** Este repositorio, la sección "Diferencias respecto a CPython" del [README](../README.md), y los siete ejemplos del PDF como tests de integración. |

## El fork: qué hay hecho y por qué no se termina

El punto 2 del PDF *sí* se puede hacer en CPython, y se hizo, como
experimento: sobre un clon de CPython 3.14.7 se añadió la palabra clave
`blob`, la declaración `blob_stmt` en la gramática, el nodo AST `Blob`, y la
alternativa `func` a `def`. El parche está en
[`fork/cpython-fork-parche.patch`](../fork/cpython-fork-parche.patch).

Lo que **no** se hizo es el punto 4: el opcode `RESOLVE_MEMBER` en
`Python/ceval.c` y la tabla de pertenencia en `Python/compile.c`. Sin
compilador en el entorno de trabajo, ese trabajo no se podía verificar, y un
fork de CPython sin compilar es peor que no tenerlo: parece implementado y
no lo está.

**Un fork de CPython sigue siendo viable** si someday se quiere el bytecode
de CPython. El parche de la gramática es la parte menos difícil; lo difícil
es el modelo de memoria en C: un blob con tabla de pertenencia, celdas
compartidas entre blobs y una intersección que crea memoria nueva, pero
comparte la celda con sus dos operandos. La semántica que hay que traducir a
C está escrita y probada en [`cruthon/blobs.py`](../cruthon/blobs.py): las
pruebas de contrato de ese archivo son la especificación ejecutable de lo que
el opcode tendría que hacer.

## Cómo leer este repositorio

* [`docs/especificacion-texto.md`](especificacion-texto.md) — el documento de
  diseño completo en texto plano.
* [`cruthon/blobs.py`](../cruthon/blobs.py) — el modelo de conjuntos: la
  parte del documento que no es Python.
* [`cruthon/lexer.py`](../cruthon/lexer.py),
  [`cruthon/parser.py`](../cruthon/parser.py),
  [`cruthon/interpreter.py`](../cruthon/interpreter.py) — la parte que es
  Python.
* [`tests/test_paridad.py`](../tests/test_paridad.py) — el contrato de
  paridad: los mismos fragmentos, la misma salida que en CPython.
