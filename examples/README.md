# Ejemplos del documento de diseño

Cada archivo de este directorio es un ejemplo del PDF *"Cruthon: un lenguaje
basado en teoría de conjuntos"*, ejecutado tal cual por el intérprete. Los
resultados están guardados en `expected/` y se comparan de forma literal:

```bash
python tools/run_examples.py            # ejecuta y compara
python tools/run_examples.py --update   # regenera los resultados guardados
```

## Qué se modificó (y por qué)

Las modificaciones son **menores y simples**, y todas están anotadas en la
cabecera de cada archivo con su código (`[M1]`, `[M2]`, …). Hay tres causas,
todas del propio documento:

1. **Funciones que el PDF usa sin definir.** El documento llama a
   `conectar_base_datos()`, `render()`, `mostrar_checkout()` y `guardar_log()`
   sin definirlas, y usa el resultado de `conectar_base_datos()` como si
   tuviera métodos. Se declara un doble mínimo dentro del blob que lo usa, de
   modo que la línea original del PDF siga siendo válida.
2. **Funciones que el PDF define pero nunca llama.** `iniciar()`,
   `calcular(a, b)` e `iniciar_app(usuario, plan)` se invocan para que el
   ejemplo haga algo observable.
3. **El PDF no imprime nada.** Como el entregable exige que los ejemplos se
   ejecuten y sus resultados queden guardados, se añaden `print` que muestran
   el estado de los blobs y, sobre todo, los errores de alcance por conjuntos
   (`frontend` no ve `db`, `base_comun` no ve `soporte_24h`, …).

No se cambia ninguna regla del lenguaje: la sintaxis, la estructura de blobs
y las operaciones de conjuntos son las del documento.

## Los siete ejemplos

| Archivo | Origen en el PDF | Qué demuestra |
| --- | --- | --- |
| `01_carrito.cruthon` | pág. 5 | `main`, `frontend` y `backend` con una intersección `carrito`: la única zona compartida |
| `02_calculadora.cruthon` | págs. 6-7 | calculadora con historial; `math` de la librería estándar se usa sin adaptación y es invisible para el frontend |
| `03_interseccion.cruthon` | pág. 8 | intersección con memoria compartida (`token`) |
| `04_union.cruthon` | pág. 8 | unión: dos blobs bajo el mismo proceso, sin memoria común |
| `05_diferencia.cruthon` | pág. 9 | diferencia: sólo lo exclusivo de `plan_premium` |
| `06_diferencia_simetrica.cruthon` | pág. 9 | diferencia simétrica: lo que ninguno de los dos comparte |
| `07_combinado.cruthon` | págs. 9-10 | las cuatro operaciones conviviendo en el mismo `main` |

## Ejecutar uno a mano

```bash
python -m cruthon examples/01_carrito.cruthon
```

Salida de `01_carrito.cruthon`:

```text
[render] tienda.html ['zapatos'] 49.9
[checkout] listo
carrito.items = ['zapatos']
carrito.total = 49.9
backend.validar_stock('zapatos') = True
backend.calcular_envio(carrito.items) = 12.5
frontend ve el total compartido = 49.9
frontend NO ve db -> el blob 'frontend' no expone el miembro 'db': no vive dentro de su área (error de alcance por conjuntos)
```

Las dos últimas líneas son la prueba de la regla central del documento: el
frontend ve el `total` que vive en la intersección `carrito`, pero no ve `db`,
que vive dentro de `backend` (los blobs hermanos no se ven entre sí).
