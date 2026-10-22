"""Pruebas de la semántica de conjuntos de Cruthon.

Cada prueba traduce una regla del documento de diseño a código ejecutable y
comprueba **lo que se ve desde el lenguaje**: el programa se ejecuta y se
compara lo que imprime. Las pruebas no tocan la representación interna (ni
celdas, ni tablas, ni marcos): si una reescritura del modelo cambia el
comportamiento, estas pruebas lo detectan; si no lo cambia, no deben fallar.

Las reglas comprobadas son:

* Intersección: memoria nueva, compartida por los dos blobs de origen.
* Unión: espacio de ejecución común, sin memoria común.
* Diferencia: el contenido del primero menos lo que el segundo también tiene.
* Diferencia simétrica: lo que ninguno de los dos comparte con el otro.
* Visibilidad geométrica: lo que vive en un sub-blob no se ve desde fuera.
* Scope: una función se ejecuta con el ámbito del blob donde está definida.
* El resultado de una operación de conjuntos es un blob válido y encadena.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from cruthon import Interpreter, parse
from cruthon.blobs import Blob
from cruthon.errors import AmbiguousName, DuplicateName, NameNotFound, ScopeError

ROOT = Path(__file__).resolve().parent.parent


def ejecutar(source: str) -> str:
    """Ejecuta un programa de Cruthon y devuelve lo que imprime."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        Interpreter("test").run(parse(source, "test"))
    return buffer.getvalue()


def falla(source: str) -> Any:
    """Ejecuta un programa que debe fallar y devuelve la excepción."""
    with pytest.raises(Exception) as info:
        ejecutar(source)
    return info.value


# --------------------------------------------------------------- intersección
INTERSECTION = """
blob main:
    blob frontend:
        tema = "oscuro"

    blob backend:
        usuario_activo = "ana"

    blob sesion = frontend & backend:
        token = None
"""


def test_intersection_creates_its_own_blob():
    salida = ejecutar(INTERSECTION + 'print(sesion)\n')
    assert "<blob sesion = frontend backend>" in salida


def test_intersection_is_visible_from_both_operands():
    """Lo que vive en la intersección se ve desde los dos operandos."""
    salida = ejecutar(
        INTERSECTION
        + """
sesion.token = "abc"
print(sesion.token, frontend.token, backend.token)
"""
    )
    assert "abc abc abc" in salida


def test_intersection_shares_memory_for_read_and_write():
    """La memoria de la intersección la leen y la escriben los dos operandos.

    Es la afirmación central del documento: "se crea un blob nuevo con memoria
    propia, compartida por los blobs que lo originaron (lectura y escritura por
    ambos)".
    """
    source = """
blob main:
    blob a:
        comun = 1

    blob b:
        comun = 1

    blob c = a & b:
        pass

print(c.comun)
c.comun = 9
print(a.comun, b.comun, c.comun)
a.comun = 7
print(a.comun, b.comun, c.comun)
"""
    salida = ejecutar(source)
    assert salida.split() == ["1", "9", "9", "9", "7", "7", "7"]


def test_intersection_only_keeps_shared_names():
    source = """
blob main:
    blob a:
        comun = 1
        solo_a = 2

    blob b:
        comun = 1
        solo_b = 3

    blob c = a & b:
        pass

print(c.comun)
try:
    print(c.solo_a)
except ScopeError as error:
    print("ScopeError")
"""
    salida = ejecutar(source)
    assert "1" in salida
    assert "ScopeError" in salida


def test_chained_intersections_share_the_same_memory():
    """``A & B`` y ``B & C`` comparten la memoria a través de ``B``."""
    source = """
blob main:
    blob a:
        valor = 1

    blob b:
        valor = 1

    blob c:
        valor = 1

    blob ab = a & b:
        pass

    blob bc = b & c:
        pass

ab.valor = 5
print(a.valor, b.valor, c.valor, ab.valor, bc.valor)
"""
    assert ejecutar(source).split() == ["5", "5", "5", "5", "5"]


def test_the_body_of_the_intersection_writes_in_the_shared_zone():
    source = """
blob main:
    blob a:
        comun = 1

    blob b:
        comun = 1

    blob c = a & b:
        comun = 3

print(a.comun, b.comun, c.comun)
"""
    assert ejecutar(source).split() == ["3", "3", "3"]


# ---------------------------------------------------------------------- unión
UNION = """
blob main:
    blob logger:
        func registrar(msg):
            print(msg)

    blob metricas:
        func contar_evento(nombre):
            print(nombre)

    blob monitoreo = logger | metricas:
        func reportar(msg, evento):
            logger.registrar(msg)
            metricas.contar_evento(evento)
"""


def test_union_exposes_both_operands_without_shared_memory():
    source = """
blob main:
    blob a:
        x = 1

    blob b:
        pass

    blob u = a | b:
        pass

print(u.x)
u.x = 5
print(a.x)
"""
    # La unión no crea memoria común: escribir en ella no toca el operando.
    assert ejecutar(source).split() == ["1", "1"]


def test_union_runs_both_operands_in_one_process():
    source = UNION + 'monitoreo.reportar("hola", "click")\n'
    salida = ejecutar(source)
    assert "hola" in salida
    assert "click" in salida


# ------------------------------------------------------------------ diferencia
DIFFERENCE = """
blob main:
    blob plan_premium:
        soporte_24h = True
        exportar_pdf = True
        exportar_csv = True

    blob plan_gratis:
        exportar_csv = True

    blob solo_premium = plan_premium - plan_gratis:
        pass
"""


def test_difference_excludes_names_shared_with_the_second():
    source = DIFFERENCE + """
print(solo_premium.soporte_24h, solo_premium.exportar_pdf)
try:
    print(solo_premium.exportar_csv)
except ScopeError:
    print("ScopeError")
"""
    salida = ejecutar(source)
    assert "True True" in salida
    assert "ScopeError" in salida


def test_difference_is_a_valid_blob_that_can_be_operated_on():
    source = DIFFERENCE + 'print(solo_premium)\n'
    assert "<blob solo_premium = plan_premium plan_gratis>" in ejecutar(source)


# ------------------------------------------------------- diferencia simétrica
SYMMETRIC = """
blob main:
    blob version_a:
        boton_grande = True
        color_azul = True

    blob version_b:
        color_azul = True
        boton_redondo = True

    blob diferencias_ab = version_a ^ version_b:
        pass
"""


def test_symmetric_difference_keeps_only_the_unshared_names():
    source = SYMMETRIC + """
print(diferencias_ab.boton_grande, diferencias_ab.boton_redondo)
try:
    print(diferencias_ab.color_azul)
except ScopeError:
    print("ScopeError")
"""
    salida = ejecutar(source)
    assert "True True" in salida
    assert "ScopeError" in salida


# ------------------------------------------------------ anidamiento y alcance
def test_un_blob_anidado_solo_lo_ve_su_contenedor():
    """El contenedor ve su área; desde fuera, el subconjunto es inalcanzable.

    El documento dice que un subconjunto que no es producto de una
    intersección "no lo ven los blobs de nivel superior": es decir, los
    blobs hermanos, no el propio contenedor (que sí ve "todo lo que está
    dentro de su área").
    """
    source = """
blob main:
    blob backend:
        secreto = 42

    blob frontend:
        pass

print(main.secreto, backend.secreto)
try:
    print(frontend.secreto)
except ScopeError:
    print("ScopeError")
"""
    salida = ejecutar(source)
    assert "42 42" in salida
    assert "ScopeError" in salida


def test_container_blob_sees_what_lives_inside_it():
    source = """
blob main:
    blob backend:
        secreto = 42

print(main.secreto)
"""
    assert "42" in ejecutar(source)


def test_sibling_blobs_do_not_see_each_other():
    source = """
blob main:
    blob frontend:
        func dibujar():
            return "html"

    blob backend:
        db = "conexion"

print(frontend.dibujar(), backend.db)
try:
    print(backend.dibujar())
except ScopeError:
    print("ScopeError")
try:
    print(frontend.db)
except ScopeError:
    print("ScopeError")
"""
    salida = ejecutar(source)
    assert "html conexion" in salida
    assert salida.count("ScopeError") == 2


def test_scope_is_the_blob_where_the_function_is_defined():
    """Quien llama entra por la puerta de su blob; adentro manda el del autor.

    ``g`` (en ``main``) llama a ``f``, que vive en ``a``: ``f`` se ejecuta con
    el ámbito de ``a`` y ve ``a.valor``, no el ``valor`` de ``b``.
    """
    source = """
blob main:
    blob a:
        valor = "a"

        func f():
            return valor

    blob b:
        valor = "b"

    func g():
        return a.f()

print(g())
"""
    assert "a" in ejecutar(source)


def test_function_called_from_a_union_still_uses_its_own_blob():
    source = """
blob main:
    blob a:
        valor = "a"

        func f():
            return valor

    blob b:
        valor = "b"

    blob u = a | b:
        func llamar():
            return a.f()

print(u.llamar())
"""
    assert "a" in ejecutar(source)


# --------------------------------------------------------------- intersecciones
def test_chained_intersections_bridge_without_direct_visibility():
    source = """
blob main:
    blob a:
        pass

    blob b:
        pass

    blob c:
        pass

    blob ab = a & b:
        pass

    blob bc = b & c:
        pass

try:
    print(a.c)
except ScopeError:
    print("ScopeError")
try:
    print(c.a)
except ScopeError:
    print("ScopeError")
"""
    salida = ejecutar(source)
    assert salida.count("ScopeError") == 2


def test_empty_blob_is_legal():
    source = """
blob vacio:
    pass

print(vacio)
"""
    assert "<blob vacio>" in ejecutar(source)


def test_set_operations_nest_without_limit():
    source = """
blob main:
    blob a:
        x = 1

    blob b:
        x = 1
        y = 2

    blob c:
        z = 3

    blob d = (a & b) | c:
        pass

print(d)
"""
    assert "<blob d = " in ejecutar(source)


# ------------------------------------------------------- módulo de pertenencia
def test_membership_table_lists_the_blobs_where_a_name_lives():
    interpreter = Interpreter("test")
    interpreter.run(parse(INTERSECTION, "test"))
    tabla = interpreter.globals.membership_table()
    assert "token" in tabla
    assert "sesion" in tabla["token"]


def test_name_outside_the_area_raises_scope_error():
    interpreter = Interpreter("test")
    interpreter.run(parse("x = 1", "test"))
    with pytest.raises(NameNotFound):
        interpreter.eval(
            parse("y", "test").body[0].value, interpreter.globals, None
        )


def test_the_blob_model_is_the_only_owner_of_the_resolution():
    """La resolución de nombres vive en el modelo de blobs, no en el evaluador."""
    interpreter = Interpreter("test")
    interpreter.run(parse("blob a:\n    x = 1\n", "test"))
    main = interpreter.globals.lookup("a").get()
    assert isinstance(main, Blob)
    assert main.lookup("x").get() == 1
    assert main.lookup("inexistente") is None


# ------------------------------------------------------------- excepciones

OSERROR_DE_ARCHIVO = '''
blob main:
    func abrir():
        try:
            open("/no-existe-de-verdad/cruthon.txt")
        except OSError as error:
            print("archivo:", isinstance(error, OSError))

abrir()
'''

OSERROR_DE_SOCKET = '''
blob main:
    import socket

    func ocupar():
        primero = socket.socket()
        primero.bind(("127.0.0.1", 0))
        puerto = primero.getsockname()[1]
        segundo = socket.socket()
        try:
            segundo.bind(("127.0.0.1", puerto))
        except OSError as error:
            print("socket:", isinstance(error, OSError))
        finally:
            segundo.close()
            primero.close()

ocupar()
'''

CONNECTION_ERROR = '''
blob main:
    # Los dos nombres existen y son lo que dicen ser: subclases de OSError,
    # que es lo que hace que ``except OSError`` los capture a los dos.
    print("conexión:", issubclass(ConnectionError, OSError))
    print("plazo:", issubclass(TimeoutError, OSError))
'''

INTERRUPCION = '''
blob main:
    func despedirse():
        try:
            raise KeyboardInterrupt()
        except KeyboardInterrupt as error:
            print("interrumpido:", type(error).__name__)

despedirse()
'''


def test_oserror_captura_un_fallo_real_de_archivo():
    """``open`` de un archivo que no existe lanza un ``OSError`` de verdad."""
    assert ejecutar(OSERROR_DE_ARCHIVO).strip() == "archivo: True"


def test_oserror_captura_un_fallo_real_de_socket():
    """Dos sockets no pueden escuchar en el mismo puerto: eso es un OSError."""
    assert ejecutar(OSERROR_DE_SOCKET).strip() == "socket: True"


def test_connection_error_y_timeout_error_son_oserror():
    """Los dos nombres nuevos son las clases de Python, no copias."""
    assert ejecutar(CONNECTION_ERROR).split() == ["conexión:", "True", "plazo:", "True"]


def test_keyboard_interrupt_se_puede_capturar_dentro_de_un_blob():
    assert ejecutar(INTERRUPCION).strip() == "interrumpido: KeyboardInterrupt"


def test_except_exception_no_captura_una_interrupcion(tmp_path):
    """``KeyboardInterrupt`` no hereda de ``Exception``, como en Python.

    El programa no la captura, así que sale del intérprete y termina con
    error: lo comprobamos en un proceso aparte para no interrumpir las demás
    pruebas.
    """
    archivo = tmp_path / "interrupcion.cruthon"
    archivo.write_text(
        "blob main:\n"
        "    func correr():\n"
        "        try:\n"
        "            raise KeyboardInterrupt()\n"
        "        except Exception:\n"
        "            print('capturada')\n"
        "\n"
        "correr()\n",
        encoding="utf-8",
    )
    proceso = subprocess.run(
        [sys.executable, "-m", "cruthon", str(archivo)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
    )
    assert proceso.returncode != 0
    assert "capturada" not in proceso.stdout
    assert "KeyboardInterrupt" in proceso.stderr


# ------------------------------------------------------- nombres y ruta de inclusión
DUPLICADO = '''
blob main:
    blob A:
        x = 1
    blob A:
        y = 2
'''

AMBIGUO = '''
blob main:
    blob A:
        y = "y de A"
    blob B:
        y = "y de B"

print(y)
'''

CERCANO = '''
blob main:
    y = "el de main"
    blob A:
        y = "el de A"

print(y)
'''

CALIFICADO = '''
blob main:
    blob A:
        blob API:
            x = 1
    blob B:
        blob API:
            x = 2

print(A.API.x, B.API.x)
'''

HERMANO_UNICO = '''
blob main:
    blob A:
        z = "sólo en A"
    blob B:
        w = "sólo en B"

print(z, w)
'''

TABLA = '''
blob main:
    blob A:
        y = 1
    blob B:
        y = 2
'''

COMPARTIDO = '''
blob main:
    blob frontend:
        total = 0
    blob backend:
        total = 0
    blob carrito = frontend & backend:
        items = []

print(items, total)
'''


def test_dos_blobs_del_mismo_blob_no_pueden_compartir_nombre():
    """La segunda declaración falla en su línea, no en el uso posterior."""
    with pytest.raises(DuplicateName) as info:
        ejecutar(DUPLICADO)
    assert DUPLICADO.splitlines()[info.value.lineno - 1].strip() == "blob A:"
    assert "'A'" in str(info.value)
    assert "'main'" in str(info.value)


def test_dos_regiones_a_la_misma_distancia_son_ambiguas():
    """No gana la primera declarada: el mensaje nombra las dos rutas."""
    with pytest.raises(AmbiguousName) as info:
        ejecutar(AMBIGUO)
    mensaje = str(info.value)
    assert "main.A.y" in mensaje
    assert "main.B.y" in mensaje
    assert "ruta de inclusión" in mensaje


def test_el_nombre_mas_cercano_gana_cuando_la_distancia_difiere():
    assert ejecutar(CERCANO).strip() == "el de main"


def test_dos_regiones_con_el_mismo_nombre_se_leen_calificadas():
    assert ejecutar(CALIFICADO).split() == ["1", "2"]


def test_un_nombre_que_solo_vive_en_un_blob_hermano_se_lee_desnudo():
    assert ejecutar(HERMANO_UNICO).strip() == "sólo en A sólo en B"


def test_la_memoria_compartida_no_se_confunde_con_ambiguedad():
    """Dos rutas a la misma celda son el mismo nombre; no hay nada que elegir."""
    assert ejecutar(COMPARTIDO).strip() == "[] 0"


def test_la_tabla_de_pertenencia_muestra_rutas_de_inclusion():
    interpreter = Interpreter("test")
    interpreter.run(parse(TABLA, "test"))
    tabla = interpreter.globals.inclusion_table()
    assert tabla["y"] == ["main.A.y", "main.B.y"]
    assert tabla["main"] == ["main"]


def test_una_region_no_se_reasigna_a_un_valor():
    """El nombre ya está tomado por la región: se falla en la asignación."""
    fuente = "\n".join(
        [
            "blob main:",
            "    blob A:",
            "        x = 1",
            "    A = 5",
            "",
        ]
    )
    with pytest.raises(DuplicateName) as info:
        ejecutar(fuente)
    assert info.value.lineno == 4
    assert "'A'" in str(info.value)
    assert "región" in str(info.value)
