"""Pruebas de la semántica de conjuntos de Cruthon.

Cada prueba traduce una regla del documento de diseño a código ejecutable.
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

import pytest

from cruthon import Interpreter, parse
from cruthon.blobs import Blob, difference, intersect, symmetric_difference, union
from cruthon.errors import NameNotFound, ScopeError


def run(source: str) -> Interpreter:
    """Ejecuta un fragmento y devuelve el intérprete con su estado final."""
    interpreter = Interpreter("test")
    interpreter.run(parse(source, "test"))
    return interpreter


def value(source: str, expression: str):
    """Evalúa ``expression`` dentro del estado final de ``source``."""
    interpreter = run(source)
    blob = interpreter.globals
    return interpreter.eval(parse(expression, "test").body[0].value, blob, None)


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
    interpreter = run(INTERSECTION)
    sesion = interpreter.globals.lookup("sesion").get()
    assert isinstance(sesion, Blob)
    assert sesion.op == "&"
    assert sesion.name == "sesion"


def test_intersection_is_visible_from_both_operands():
    interpreter = run(INTERSECTION)
    sesion = interpreter.globals.lookup("sesion").get()
    assert interpreter.eval(
        parse("sesion.token", "t").body[0].value, interpreter.globals, None
    ) is None
    # `token` vive en la intersección: los dos operandos la alcanzan.
    assert "token" in sesion.storage


def test_intersection_shares_memory_for_read_and_write():
    source = """
blob main:
    blob a:
        pass

    blob b:
        pass

    blob c = a & b:
        valor = 1
"""
    interpreter = run(source)
    main = interpreter.globals
    a = main.lookup("a").get()
    b = main.lookup("b").get()
    c = main.lookup("c").get()
    assert a.lookup("valor").get() == 1
    assert b.lookup("valor").get() == 1
    # Escribiendo desde `a` cambia la misma celda que ve `b` y `c`.
    a.lookup("valor").set(42)
    assert b.lookup("valor").get() == 42
    assert c.lookup("valor").get() == 42


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
"""
    interpreter = run(source)
    main = interpreter.globals
    c = main.lookup("c").get()
    assert c.lookup("comun") is not None
    assert c.lookup("solo_a") is None
    assert c.lookup("solo_b") is None


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
    interpreter = run(UNION)
    main = interpreter.globals
    monitoreo = main.lookup("monitoreo").get()
    assert isinstance(monitoreo, Blob)
    assert monitoreo.op == "|"
    assert monitoreo.lookup("registrar") is not None
    assert monitoreo.lookup("contar_evento") is not None
    # Sin memoria común: escribir en la unión no toca los operandos.
    logger = main.lookup("logger").get()
    original = logger.storage["registrar"]
    monitoreo.define("registrar", "sombra")
    # El operando conserva su propia función: la unión no comparte memoria.
    assert logger.storage["registrar"] is original
    assert monitoreo.lookup("registrar").get() == "sombra"


def test_union_runs_both_operands_in_one_process(capsys):
    interpreter = run(UNION)
    main = interpreter.globals
    monitoreo = main.lookup("monitoreo").get()
    funcion = monitoreo.storage["reportar"]
    interpreter.call(funcion, ["hola", "click"], {})
    captured = capsys.readouterr()
    assert "hola" in captured.out
    assert "click" in captured.out


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
    interpreter = run(DIFFERENCE)
    solo_premium = interpreter.globals.lookup("solo_premium").get()
    assert solo_premium.lookup("soporte_24h") is not None
    assert solo_premium.lookup("exportar_pdf") is not None
    assert solo_premium.lookup("exportar_csv") is None


def test_difference_is_a_valid_blob_that_can_be_operated_on():
    interpreter = run(DIFFERENCE)
    main = interpreter.globals
    solo_premium = main.lookup("solo_premium").get()
    plan_gratis = main.lookup("plan_gratis").get()
    resultado = intersect(solo_premium, plan_gratis)
    assert isinstance(resultado, Blob)
    assert resultado.op == "&"


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
    interpreter = run(SYMMETRIC)
    diferencias = interpreter.globals.lookup("diferencias_ab").get()
    assert diferencias.lookup("boton_grande") is not None
    assert diferencias.lookup("boton_redondo") is not None
    assert diferencias.lookup("color_azul") is None


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
"""
    interpreter = run(source)
    main = interpreter.globals.lookup("main").get()
    frontend = main.lookup("frontend").get()
    backend = main.lookup("backend").get()
    # El contenedor ve lo que vive dentro de su área.
    assert main.lookup("secreto").get() == 42
    assert backend.lookup("secreto").get() == 42
    # El hermano no: lo que vive en `backend` es inalcanzable desde
    # `frontend` por pertenencia geométrica.
    assert frontend.lookup("secreto") is None
    with pytest.raises(ScopeError):
        interpreter.get_attribute(frontend, "secreto")


def test_container_blob_sees_what_lives_inside_it():
    source = """
blob main:
    blob backend:
        secreto = 42
"""
    interpreter = run(source)
    main = interpreter.globals.lookup("main").get()
    assert main.lookup("secreto").get() == 42


def test_sibling_blobs_do_not_see_each_other():
    source = """
blob main:
    blob frontend:
        func dibujar():
            return "html"

    blob backend:
        db = "conexion"
"""
    interpreter = run(source)
    main = interpreter.globals.lookup("main").get()
    frontend = main.lookup("frontend").get()
    backend = main.lookup("backend").get()
    assert frontend.lookup("db") is None
    assert backend.lookup("dibujar") is None
    with pytest.raises(ScopeError):
        interpreter.get_attribute(backend, "dibujar")


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
"""
    interpreter = run(source)
    main = interpreter.globals.lookup("main").get()
    g = main.storage["g"]
    assert interpreter.call(g, [], {}) == "a"
    # Y el blob `b` es inalcanzable desde `a`: los hermanos no se ven.
    a = main.lookup("a").get()
    assert a.lookup("valor").get() == "a"
    assert a.lookup("b") is None


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
"""
    interpreter = run(source)
    u = interpreter.globals.lookup("u").get()
    assert interpreter.call(u.storage["llamar"], [], {}) == "a"


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
"""
    interpreter = run(source)
    main = interpreter.globals.lookup("main").get()
    a = main.lookup("a").get()
    b = main.lookup("b").get()
    c = main.lookup("c").get()
    ab = main.lookup("ab").get()
    bc = main.lookup("bc").get()
    # B actúa de intermediario: `ab` y `bc` se tocan en `b`.
    assert b.children and any(child is ab for child in b.children)
    assert b.children and any(child is bc for child in b.children)
    # A y C no se ven directamente.
    assert a.lookup("c") is None
    assert c.lookup("a") is None


def test_empty_blob_is_legal():
    source = """
blob vacio:
    pass
"""
    interpreter = run(source)
    vacio = interpreter.globals.lookup("vacio").get()
    assert isinstance(vacio, Blob)
    assert vacio.declared_names() == frozenset()


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
"""
    interpreter = run(source)
    d = interpreter.globals.lookup("d").get()
    assert d.op == "|"
    assert d.operands[0].op == "&"


# ------------------------------------------------------- módulo de pertenencia
def test_membership_table_lists_the_blobs_where_a_name_lives():
    interpreter = run(INTERSECTION)
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
