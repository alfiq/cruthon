"""Pruebas del servidor de ejemplo ``examples/web/servidor.cruthon``.

Se arranca el servidor en un puerto libre y se le habla HTTP como lo haría
un navegador: se pide la página, se envía el formulario, se consulta la API,
se piden los estáticos y se comprueban los códigos de error. La comparación
es sobre la respuesta real, no sobre funciones internas del intérprete.
"""

from __future__ import annotations

import _thread
import http.client
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from cruthon.cli import main

ROOT = Path(__file__).resolve().parent.parent
SERVIDOR = ROOT / "examples" / "web" / "servidor.cruthon"


def puerto_libre() -> int:
    with socket.socket() as sonda:
        sonda.bind(("127.0.0.1", 0))
        return sonda.getsockname()[1]


def pedir(base: str, ruta: str, metodo: str = "GET", cuerpo=None, cabeceras=None):
    """Una petición HTTP sin seguir redirecciones ni lanzar por un 4xx."""
    puerto = int(base.rsplit(":", 1)[1])
    conexion = http.client.HTTPConnection("127.0.0.1", puerto, timeout=5)
    try:
        conexion.request(metodo, ruta, body=cuerpo, headers=cabeceras or {})
        respuesta = conexion.getresponse()
        texto = respuesta.read().decode("utf-8")
        return respuesta.status, {k.lower(): v for k, v in respuesta.getheaders()}, texto
    finally:
        conexion.close()


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    puerto = puerto_libre()
    registro = tmp_path_factory.mktemp("venn") / "servidor.log"
    entorno = dict(os.environ, CRUTHON_PUERTO=str(puerto), PYTHONIOENCODING="utf-8")
    with registro.open("w", encoding="utf-8") as salida:
        proceso = subprocess.Popen(
            [sys.executable, "-m", "cruthon", str(SERVIDOR)],
            cwd=str(ROOT),
            env=entorno,
            stdout=salida,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
    base = f"http://127.0.0.1:{puerto}"
    limite = time.time() + 20
    while time.time() < limite:
        if proceso.poll() is not None:
            pytest.fail(f"el servidor terminó al arrancar:\n{registro.read_text(encoding='utf-8')}")
        try:
            estado, _, _ = pedir(base, "/salud")
            if estado == 200:
                break
        except OSError:
            time.sleep(0.1)
    else:
        proceso.terminate()
        pytest.fail("el servidor no respondió a /salud a tiempo")

    try:
        yield base
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proceso.kill()
            proceso.wait(timeout=5)


def test_la_pagina_de_inicio_se_sirve_con_cabeceras_de_seguridad(servidor):
    estado, cabeceras, cuerpo = pedir(servidor, "/")
    assert estado == 200
    assert cabeceras["content-type"] == "text/html; charset=utf-8"
    assert cabeceras["cache-control"] == "no-store"
    assert cabeceras["x-content-type-options"] == "nosniff"
    assert "<html lang=\"es\">" in cuerpo
    assert 'method="post" action="/operar"' in cuerpo
    # La política de contenido no permite nada en línea: ni estilo ni guion.
    assert "script-src 'self'" in cabeceras["content-security-policy"]
    assert "unsafe-inline" not in cabeceras["content-security-policy"]


def test_los_estaticos_se_sirven_con_su_tipo_y_su_cache(servidor):
    for ruta, tipo in (
        ("/estatico/hoja.css", "text/css; charset=utf-8"),
        ("/estatico/app.js", "text/javascript; charset=utf-8"),
        ("/estatico/icono.svg", "image/svg+xml"),
    ):
        estado, cabeceras, cuerpo = pedir(servidor, ruta)
        assert estado == 200, ruta
        assert cabeceras["content-type"] == tipo
        assert "max-age" in cabeceras["cache-control"]
        assert cuerpo.strip(), ruta


def test_el_formulario_calcula_y_redirige(servidor):
    cuerpo = "a=manzana%0Apera%0Amanzana&b=pera%0Auva"
    estado, cabeceras, _ = pedir(
        servidor,
        "/operar",
        metodo="POST",
        cuerpo=cuerpo,
        cabeceras={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert estado == 303
    assert cabeceras["location"] == "/?ok=1"

    estado, _, pagina = pedir(servidor, "/?ok=1")
    assert estado == 200
    # Los repetidos no cuentan y la intersección es sólo "pera".
    assert '<ul class="elementos" data-lista="interseccion"><li>pera</li></ul>' in pagina
    assert '<ul class="elementos" data-lista="union"><li>manzana</li><li>pera</li><li>uva</li></ul>' in pagina
    assert "Listo: el resultado está abajo." in pagina


def test_la_api_habla_json(servidor):
    cuerpo = json.dumps({"a": ["ana", "beto"], "b": ["beto", "caro"]})
    estado, cabeceras, texto = pedir(
        servidor,
        "/api/operar",
        metodo="POST",
        cuerpo=cuerpo,
        cabeceras={"Content-Type": "application/json"},
    )
    assert estado == 200
    assert cabeceras["content-type"] == "application/json; charset=utf-8"
    datos = json.loads(texto)
    assert datos["interseccion"] == ["beto"]
    assert datos["union"] == ["ana", "beto", "caro"]
    assert datos["diferencia"] == ["ana"]
    assert datos["diferencia_simetrica"] == ["ana", "caro"]
    assert isinstance(datos["operaciones"], int)

    estado, _, texto = pedir(servidor, "/api/estado")
    assert estado == 200
    guardado = json.loads(texto)
    assert guardado["ultimo"]["union"] == ["ana", "beto", "caro"]
    assert guardado["operaciones"] >= 1


def test_el_historial_guarda_diez_como_mucho(servidor):
    for indice in range(12):
        cuerpo = json.dumps({"a": [f"a{indice}"], "b": [f"b{indice}"]})
        estado, _, texto = pedir(
            servidor,
            "/api/operar",
            metodo="POST",
            cuerpo=cuerpo,
            cabeceras={"Content-Type": "application/json"},
        )
        assert estado == 200, texto
    estado, _, texto = pedir(servidor, "/api/estado")
    assert estado == 200
    assert len(json.loads(texto)["historial"]) == 10


def test_el_html_escapa_lo_que_escribe_la_persona(servidor):
    cuerpo = "a=%3Cscript%3Ealert(1)%3C%2Fscript%3E%0Anormal&b=normal"
    pedir(
        servidor,
        "/operar",
        metodo="POST",
        cuerpo=cuerpo,
        cabeceras={"Content-Type": "application/x-www-form-urlencoded"},
    )
    estado, _, pagina = pedir(servidor, "/")
    assert estado == 200
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in pagina
    assert "<script>alert(1)</script>" not in pagina


def test_cada_error_tiene_su_codigo(servidor):
    estado, cabeceras, _ = pedir(servidor, "/no-existe")
    assert estado == 404
    assert cabeceras["content-type"].startswith("text/html")

    estado, cabeceras, _ = pedir(servidor, "/operar", metodo="GET")
    assert estado == 405
    assert cabeceras["allow"] == "POST"

    estado, _, texto = pedir(servidor, "/api/operar", metodo="POST", cuerpo="a=x")
    assert estado == 415
    assert "application/json" in texto

    estado, _, texto = pedir(
        servidor,
        "/api/operar",
        metodo="POST",
        cuerpo="no-json",
        cabeceras={"Content-Type": "application/json"},
    )
    assert estado == 400
    assert "JSON" in json.loads(texto)["mensaje"]

    enorme = "a=" + "x" * 20000
    estado, _, _ = pedir(
        servidor,
        "/operar",
        metodo="POST",
        cuerpo=enorme,
        cabeceras={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert estado == 413


def test_una_peticion_ilegible_no_tumba_el_servidor(servidor):
    puerto = int(servidor.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", puerto), timeout=5) as sonda:
        sonda.sendall(b"BASURA\r\n\r\n")
        respuesta = sonda.recv(200).decode("latin-1")
    assert respuesta.startswith("HTTP/1.1 400")
    estado, _, _ = pedir(servidor, "/salud")
    assert estado == 200


def test_head_devuelve_cabeceras_sin_cuerpo(servidor):
    estado, cabeceras, cuerpo = pedir(servidor, "/", metodo="HEAD")
    assert estado == 200
    assert cabeceras["content-type"] == "text/html; charset=utf-8"
    assert cuerpo == ""
    assert int(cabeceras["content-length"]) > 0


def test_una_interrupcion_de_teclado_cierra_limpio(monkeypatch):
    """Ctrl+C no deja una traza: el servidor cierra el socket y sale con 0.

    La consola de este entorno no entrega eventos ``CTRL_C_EVENT`` (y un
    grupo de procesos nuevo los ignora por diseño de Windows), así que la
    interrupción se inyecta con ``_thread.interrupt_main()``: es el mismo
    ``KeyboardInterrupt`` en el hilo principal que produce Ctrl+C, sin la
    entrega del sistema operativo. El resto del camino --el ``except``, la
    bitácora, el ``finally`` con ``close`` y el código de salida-- es el real.
    """
    puerto = puerto_libre()
    monkeypatch.setenv("CRUTHON_PUERTO", str(puerto))
    base = f"http://127.0.0.1:{puerto}"
    salida, error = io.StringIO(), io.StringIO()
    listo = threading.Event()
    parar = threading.Event()
    limite = time.time() + 20

    def interrumpir():
        while not parar.is_set() and time.time() < limite:
            try:
                if pedir(base, "/salud")[0] == 200:
                    listo.set()
                    break
            except OSError:
                pass
            time.sleep(0.1)
        if listo.is_set() and not parar.is_set():
            _thread.interrupt_main()

    hilo = threading.Thread(target=interrumpir, daemon=True)
    hilo.start()
    try:
        with redirect_stdout(salida), redirect_stderr(error):
            codigo = main([str(SERVIDOR)])
    finally:
        parar.set()
        hilo.join(timeout=5)

    assert listo.is_set(), "el servidor no llegó a escuchar"
    assert codigo == 0
    assert "Venn detenido." in salida.getvalue()
    assert "interrumpido con el teclado" in error.getvalue()
    assert "Traceback" not in error.getvalue()
    # El socket se cerró: ya no hay nada escuchando en ese puerto.
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", puerto), timeout=1).close()


def test_un_puerto_ocupado_falla_con_codigo_sin_traza(servidor):
    """Si no puede escuchar, el proceso sale con 1 y lo dice, sin traza."""
    puerto = int(servidor.rsplit(":", 1)[1])
    entorno = dict(os.environ, CRUTHON_PUERTO=str(puerto), PYTHONIOENCODING="utf-8")
    proceso = subprocess.run(
        [sys.executable, "-m", "cruthon", str(SERVIDOR)],
        cwd=str(ROOT),
        env=entorno,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proceso.returncode == 1
    assert "No se pudo escuchar" in proceso.stderr
    assert "Traceback" not in proceso.stderr
    # El servidor que ya estaba escuchando no se enteró.
    estado, _, _ = pedir(servidor, "/salud")
    assert estado == 200
