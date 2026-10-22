# Glosario: nombres, regiones y rutas

Vocabulario para quien escriba documentación de Cruthon. La regla de fondo es
una sola: **un blob es un área de un diagrama de Venn, no un nodo de un
árbol.** Todo lo demás —cómo se nombra, cómo se lee, cómo se califica— sale de
esa frase.

## Las dos relaciones

| Relación | Símbolo | Qué dice | Ejemplo |
| --- | --- | --- | --- |
| **Pertenencia** | `∈` | el nombre es un miembro del área | `x ∈ main.A` — el nombre `x` vive en la región `main.A` |
| **Inclusión** | `⊆` | una región está dentro de otra | `main.A.API ⊆ main.A` — la región `API` está incluida en `A` |

La pertenencia es de **nombres a regiones**; la inclusión es de **regiones a
regiones**. Un nombre no se incluye en nada y una región no pertenece a nada:
`API ⊆ A` y `x ∈ A` se escriben distinto porque dicen cosas distintas.

Frases correctas:

* «`API` está incluido en `A`.»
* «`A` incluye a `API`.»
* «`x` pertenece a `main.A`.»
* «`API` no está incluido en `B`: vive en otra área.»

## Ruta de inclusión

La **ruta de inclusión** de una región es el camino desde la raíz que la
nombra, uniendo con puntos las regiones que la incluyen:

* la ruta de inclusión de `API` es `main.A.API`;
* la ruta de inclusión de `y` en el blob `main` es `main.y`.

Sirve para dos cosas: es el nombre completo de la región y es la forma de
calificar una lectura. Desde `main` se escribe `A.API.x`; desde `A`, `API.x`;
desde `API`, `x`.

## Identidad por ruta

Dos regiones que se llaman igual en sitios distintos **son regiones
distintas**, porque su ruta de inclusión es distinta:
`main.A.API` y `main.B.API` no son la misma cosa, aunque las dos se llamen
`API`. Eso es la **identidad por ruta**: la identidad de una región es su
camino, no su etiqueta.

De ahí sale la consecuencia práctica: desde `main`, leer `API` a secas no
elige una —no hay una «primera declarada» que valga—, es ambiguo. Calificar
con la ruta es la respuesta, no adivinar el orden del archivo.

## La regla que aplica el intérprete

Las tres piezas están en [`cruthon/blobs.py`](../cruthon/blobs.py) y se
comprueban en [`tests/test_semantics.py`](../tests/test_semantics.py):

1. **Unicidad al declarar.** Un nombre es único dentro de su blob. Dos
   regiones del mismo blob no pueden compartirlo, y tampoco una región y un
   valor (`A = 5` donde ya vive `blob A:`). Falla en la línea del duplicado:

   ```console
   $ cruthon dup.cruthon
   DuplicateName: File "dup.cruthon", line 5
       ya vive un nombre 'A' en el blob 'main': dos regiones del mismo blob no pueden compartir nombre
   ```

2. **Ambigüedad al resolver.** Gana la declaración más cercana; si a esa
   distancia hay dos enlaces distintos, el nombre es ambiguo y el mensaje
   nombra las rutas que compiten, escritas desde donde se lee:

   ```console
   $ cruthon amb.cruthon
   AmbiguousName: el nombre 'y' vive en más de un área a la misma distancia: A.y y B.y; escribe la ruta de inclusión
   ```

   Dos rutas a la **misma celda** —la intersección comparte memoria con sus
   operandos— son un solo nombre: eso no es ambigüedad.

3. **La tabla muestra rutas.** `cruthon -p` y `%pertenencia` imprimen la ruta
   de inclusión de cada nombre, no sólo el blob donde vive (extracto):

   ```console
   Tabla de pertenencia (nombre -> rutas de inclusión):
     API: main.A.API, main.B.API
     x: main.A.API.x, main.B.API.x
     y: main.A.y, main.B.y, main.C.y
   ```

## Palabras que no se usan aquí

| Palabra | Por qué no | Qué escribir |
| --- | --- | --- |
| genealogía | sugiere un árbol de descendencia con antepasados; aquí no hay descendencia, hay áreas que se incluyen | «`API` está incluido en `A`» |
| jerarquía | sugiere niveles con autoridad u orden; la distancia aquí es geométrica: cuántas inclusiones hay hasta el nombre | «está a una inclusión de distancia» |
| padre / hijo | parentesco; una región no engendra a otra, sólo la incluye | «`A` incluye a `API`», «`API` está incluido en `A`» |
| herencia / hereda | en Cruthon la herencia existe **sólo** para `class` de Python; un blob no hereda nada: sus nombres viven en él o no viven | «`API` está incluido en `A`» |
| namespace / espacio de nombres | sugiere un diccionario plano con ámbito léxico; aquí el ámbito es un área con geometría | «región», «área», «tabla de pertenencia» |
| contención / contenedor / contenido | «contiene» se lee como una caja con dueño y tapa la relación de conjuntos | «el área de `A` incluye las regiones que están dentro» |

La excepción deliberada es la frase «esto no es una jerarquía»: ahí la palabra
aparece para negarla, y conviene que vaya seguida de lo que sí es (geometría,
inclusión, distancia).

## Pares correctos e incorrectos

* «`API` está incluido en `A`» · no «`API` es hijo de `A`».
* «la ruta de inclusión de `API` es `main.A.API`» · no «el namespace de `API`
  es `A`».
* «`main.A.API` y `main.B.API` son regiones distintas» · no «gana la primera
  declarada».
* «desde `main`, `API` es ambiguo» · no «`API` hereda de `A`».
* «`y` vive en `main.A.y` y en `main.B.y`» · no «`y` está en dos blobs, el
  primero manda».
