# Fork de CPython: la vía del opcode `RESOLVE_MEMBER`

Este directorio conserva el experimento de la **primera vía** que propone el
documento de diseño: modificar CPython para que la resolución de nombres
sea por pertenencia a conjuntos y no por MRO.

La decisión final del proyecto (intérprete propio en Python puro) y el
razonamiento están en [`../docs/estrategia.md`](../docs/estrategia.md). Aquí
solo se documenta qué hay en el fork y qué falta.

## Contenido

| Archivo | Qué es |
| --- | --- |
| [`cpython-fork-parche.patch`](cpython-fork-parche.patch) | El parche aplicado sobre CPython 3.14.7, listo para `git apply`. |

## Qué hace el parche

Sobre el árbol de CPython 3.14.7:

* **`Grammar/python.gram`** — añade la declaración `blob`:

  ```python
  blob_stmt[stmt_ty]:
      | 'blob' n=NAME s=['=' e=expression { e }] ':' b=block {
          _PyAST_Blob(n->v.Name.id, s, b, EXTRA) }
  ```

  y acepta `func` como alternativa a `def`.

* **`Parser/Python.asdl`**, **`Include/internal/pycore_ast.h`**,
  **`Include/internal/pycore_ast_state.h`** — el nodo AST `Blob`
  (nombre, origen opcional, cuerpo) y su visitor.

* **`Python/Python-ast.c`** — el código generado para el nodo nuevo.

* **`Lib/keyword.py`** — `blob` y `func` pasan a ser palabras reservadas.

Es decir: el **punto 2** del documento ("agregar la palabra clave `blob` al
tokenizador y al parser") queda hecho a nivel de gramática de CPython.

## Qué falta

El **punto 4**: el opcode `RESOLVE_MEMBER` en `Python/ceval.c`, y el
**punto 3**: la tabla de pertenencia por blob en `Python/compile.c`. Sin
ellos el fork no compila el lenguaje, sólo la gramática: el parser acepta
`blob`, pero no hay forma de ejecutarlo.

## Cómo continuarlo

1. `git apply fork/cpython-fork-parche.patch` sobre un clon de CPython 3.14.7.
2. Regenerar el parser: `make regen-pegen` (necesita un C99 y Python 3.9+).
3. Modelar un blob en C: un tipo nuevo con su tabla de pertenencia
   (nombre → conjunto de blobs donde vive) y sus celdas de memoria.
4. Traducir la semántica de [`../cruthon/blobs.py`](../cruthon/blobs.py):
   es la especificación ejecutable. En particular, la intersección crea
   memoria nueva **pero comparte la celda** con sus dos operandos, y por eso
   escribir por cualquiera de los tres se ve en los otros dos.
5. Emitir `RESOLVE_MEMBER` en lugar de `LOAD_ATTR` cuando el objeto es un
   blob, y lanzar el error de alcance cuando el nombre no vive en la tabla
   del blob activo ni en ninguna intersección alcanzable.

Las pruebas de contrato de [`../tests/test_semantics.py`](../tests/test_semantics.py)
y [`../tests/test_paridad.py`](../tests/test_paridad.py) son el criterio de
aceptación: el fork debe producir exactamente la misma salida observable.

## Nota sobre el entorno

Este trabajo se hizo sin un compilador disponible, así que el parche **no se
ha compilado ni ejecutado**. Se conserva como diseño y punto de partida, no
como implementación verificada.
