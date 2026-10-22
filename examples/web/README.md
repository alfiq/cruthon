# Venn: un servidor web en Cruthon

[`servidor.cruthon`](servidor.cruthon) es un servidor HTTP completo escrito
en Cruthon: abre un socket, lee la petición, la enruta, calcula, arma el HTML
y la respuesta. No hay clases en ninguna parte —ni `http.server`, ni
`BaseHTTPRequestHandler`—: la aplicación son blobs y operaciones de conjuntos,
como pide el documento de diseño.

La aplicación es un laboratorio de conjuntos: dos conjuntos escritos por
quien visita la página se combinan con los cuatro operadores (`&`, `|`, `-`,
`^`) y el resultado se muestra en cuatro tarjetas, con historial.

```bash
python -m cruthon examples/web/servidor.cruthon
# Venn escuchando en http://127.0.0.1:8100 (Ctrl+C para parar)

CRUTHON_PUERTO=9000 python -m cruthon examples/web/servidor.cruthon
```

Escucha en `127.0.0.1` y no en `0.0.0.0`: un ejemplo no tiene por qué quedar
expuesto a la red entera. El puerto sale de `CRUTHON_PUERTO`; sin variable,
8099.

## Los blobs

```
main
  transporte   sockets y HTTP: leer la petición, escribir la respuesta
  api          validación y cálculo; devuelve datos, nunca HTML
  sitio        el front end: arma el HTML que ve el navegador
  estado       = api & sitio   -> el único punto de contacto entre los dos
  bitacora     una línea por petición, por stderr
  servidor     = transporte | bitacora  -> mismo proceso, sin memoria común
```

* **`api` no ve `sitio` y `sitio` no ve `api`.** Cada uno escribe sus
  funciones con el ámbito de su propio blob; lo que no vive en él no existe
  para él. No hay marcadores de privado: es geometría.
* **`estado` es la intersección** y por eso tiene memoria compartida: el
  historial y el último resultado. `api` escribe ahí y `sitio` lee de ahí.
  Es el mismo patrón del PDF cuando pasa `carrito` a `frontend.dibujar_pagina`.
* **`servidor` es la unión** de `transporte` y `bitacora`: dos blobs que
  corren bajo el mismo proceso sin compartir memoria. El servidor no ve
  `api` ni `sitio` por pertenencia; se los pasan como argumentos, igual que
  `estado`.
* **Sin OOP, la tabla de rutas es un diccionario.** `rutas` mapea
  `(método, camino)` a un recurso. Un camino que no está ahí no existe; para
  distinguir 404 de 405 se buscan los métodos que sí lo tienen.

## Rutas

| Método | Ruta | Qué hace |
| --- | --- | --- |
| `GET` / `HEAD` | `/` | La página: formulario, resultado e historial |
| `POST` | `/operar` | Calcula y redirige (303) a `/?ok=1`: POST/redirect/GET |
| `POST` | `/api/operar` | Lo mismo, en JSON: `{"a": [...], "b": [...]}` |
| `GET` | `/api/estado` | Último resultado e historial |
| `GET` | `/salud` | Estado del servidor, para saber si está arriba |
| `GET` | `/estatico/hoja.css`, `/estatico/app.js`, `/estatico/icono.svg` | Los estáticos |

```bash
curl -s http://127.0.0.1:8099/salud
curl -s -X POST -H "Content-Type: application/json" \
     -d '{"a":["ana","beto"],"b":["beto","caro"]}' \
     http://127.0.0.1:8099/api/operar
```

## El front end

HTML generado en el blob `sitio`, hoja de estilos y guion servidos por la
misma tabla de rutas. El formulario **funciona sin JavaScript**: envía por
`POST`, el servidor calcula y redirige a la página con el resultado. El guion
es una mejora progresiva: si el navegador lo permite, manda el mismo cuerpo
a `/api/operar` y actualiza tarjetas e historial sin recargar.

El navegador no ejecuta Cruthon, así que ese guion es el único archivo que no
está escrito en Cruthon; todo lo demás (rutas, cálculo, HTML, CSS y la
respuesta HTTP) sale del mismo programa. El guion son 110 líneas de
JavaScript sin dependencias, y toda la lógica de conjuntos vive en el
servidor.

Las piezas de HTML que se arman con datos se escriben como literales
adyacentes, igual que en Python: `pie()` reparte el párrafo del contador en
cinco literales (tres f-strings y dos de texto) y el intérprete los evalúa
como una sola cadena. Eso es lo que permite que la línea más larga del
ejemplo se lea en renglones sin cambiar un byte de la respuesta; la paridad
está fijada en `tests/test_paridad.py`.

## Buenas prácticas, y dónde están

**Red y HTTP**

* `Content-Length` acotado: un cuerpo de más de 8 KiB se rechaza con 413
  antes de leerlo; cabeceras de más de 100 o una línea de petición enorme se
  rechazan con 431. Un servidor que no acota lo que lee se tumba con una
  petición. Antes de cerrar el 413 se descarta un trozo acotado del cuerpo
  (64 KiB), porque cerrar mientras el cliente escribe le llega como un
  reinicio de conexión y se queda sin la respuesta.
* Tiempo de espera de 5 s por operación de red y `Connection: close` en cada
  respuesta: nada de conexiones colgadas acumulándose.
* `Date`, `Server`, `Content-Type` con `charset` y `Cache-Control` en todas
  las respuestas; `Cache-Control: public, max-age` en los estáticos y
  `no-store` en lo dinámico.
* 404 y 405 distinguidos de verdad (`Allow` en el 405), 415 si el cuerpo no
  es JSON, 400 si la petición no tiene forma de HTTP, 501 si el cuerpo viene
  con `Transfer-Encoding`.
* `HEAD` responde con las cabeceras y sin cuerpo.
* Fallos inesperados: 500 con un mensaje que no filtra detalles; el detalle
  va a la bitácora.

**Seguridad**

* Se escucha en `127.0.0.1`.
* Todas las respuestas llevan `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  `Cross-Origin-Opener-Policy: same-origin` y una política de contenido que
  prohíbe el estilo y el guion en línea (`default-src 'none'` con
  `style-src 'self'`, `script-src 'self'`, `connect-src 'self'`,
  `form-action 'self'`, `frame-ancestors 'none'`). Por eso la hoja y el guion
  son rutas y no bloques `<style>`/`<script>`.
* Todo lo que escribe la persona pasa por `html.escape` antes de tocar el
  HTML: un `<script>` en un conjunto se ve como texto.
* Los estáticos son un diccionario de tres rutas, no un servidor de archivos:
  no hay forma de pedir `../../etc/passwd` porque no hay rutas de archivo.
* Validación en el borde: máximo 64 elementos por conjunto y 40 caracteres
  por elemento, con un 400 y el formulario relleno.

**Front end**

* HTML semántico: `header`, `main`, `footer`, `section` con
  `aria-labelledby`, `table` con `caption` y `scope="col"`, `label` por cada
  campo, `aria-describedby` para las ayudas, enlace «saltar al contenido».
* Accesible con teclado: `:focus-visible` visible, mensajes de error con
  `role="alert"`, formulario con `aria-busy` mientras espera.
* Sin dependencias y sin `innerHTML` con datos del servidor: el guion usa
  `textContent`, así que no hay XSS por el lado del cliente.
* Estilos con variables CSS, `color-scheme` para claro/oscuro,
  `prefers-reduced-motion`, y una sola hoja sin estilos en línea.

**Operación**

* Una línea por petición en `stderr` con método, camino, código y
  milisegundos; los errores inesperados también quedan ahí.
* `/salud` para saber si está arriba sin pedir la página.
* Puerto configurable por entorno y `SO_REUSEADDR` para poder reiniciar sin
  esperar.
* **Ctrl+C no es un fallo**: el bucle espera conexiones con un plazo de medio
  segundo, `except KeyboardInterrupt` lo anota en la bitácora, el `finally`
  cierra el socket y el proceso sale con 0, sin traza. Los errores de red se
  capturan con `except OSError`, que es el tipo real de un puerto ocupado o
  de una lectura interrumpida.
* **Un puerto ocupado es un fracaso, y se nota**: se informa por `stderr` y
  el proceso sale con 1. En Windows el socket usa `SO_EXCLUSIVEADDRUSE`
  porque `SO_REUSEADDR` deja que un segundo proceso se quede con el mismo
  puerto en silencio; en POSIX se usa `SO_REUSEADDR`, que sólo evita esperar
  a `TIME_WAIT`.
* Versión en la URL de los estáticos (`?v=1`) para poder cachearlos sin
  quedarse con una hoja vieja.

## Lo que no tiene

Es un ejemplo, y conviene decir qué le falta antes de creérselo para
producción: no es concurrente (atiende una conexión por vez), no guarda el
historial en disco (vive en el blob `estado`, se pierde al parar), no tiene
TLS, ni `ETag`/`Last-Modified`, ni compresión, ni límite de tasa. Tampoco
sirve archivos del disco: los estáticos son tres strings.

## Pruebas

[`tests/test_web.py`](../../tests/test_web.py) arranca el servidor en un
puerto libre y le habla HTTP: página, estáticos, formulario con su 303,
API JSON, historial acotado a diez, escapado del HTML, 404/405/415/400/413 y
una petición basura que no debe tumbar el proceso.

```bash
python -m pytest tests/test_web.py
```
