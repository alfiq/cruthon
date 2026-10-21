#!/usr/bin/env python3
"""Añade al PDF de diseño la página que documenta cómo se interpreta Cruthon.

El PDF original exportado desde el editor no tiene fuente ``.tex``/``.md`` en
el repositorio, así que la página se genera con ``reportlab`` (la biblioteca de
PDF disponible en el repo) y se une al documento original con ``pypdf``,
respetando la geometría y el estilo de las páginas existentes: A4, márgenes de
45.5 pt, título de 24 pt, cuerpo de 12 pt y pie de página de 6 pt.

Uso::

    python tools/pdf_interpreted_page.py            # actualiza el PDF del repo
    python tools/pdf_interpreted_page.py --salida X.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

#: Geometría de las páginas del PDF original (595.92 x 842.88 pt).
PAGE_SIZE = (595.92, 842.88)
MARGIN_X = 45.5
MARGIN_TOP = 54.0
MARGIN_BOTTOM = 54.0

TITULO = "Interpretación: Cruthon se ejecuta como Python"

PARRAFOS = [
    (
        "h",
        "Cruthon se interpreta igual que Python: el código no se compila por "
        "adelantado, sino que se analiza y se ejecuta conforme se lee, "
        "instrucción por instrucción, tal como ocurre en la consola de Python.",
    ),
    (
        "p",
        "En la práctica, esto significa que el estado de los blobs es visible "
        "en el momento: cada instrucción se evalúa y muestra su resultado "
        "mientras se teclea, y las tablas de pertenencia a conjuntos se "
        "recalculan con cada línea escrita. No hay paso de compilación ni "
        "archivos intermedios que mantener sincronizados.",
    ),
    (
        "h",
        "Cómo se ejecuta",
    ),
    (
        "li",
        "En la REPL, cada instrucción se ejecuta al teclearla y muestra su "
        "resultado, como el intérprete de CPython.",
    ),
    (
        "li",
        "Un bloque (una declaración <b>blob</b> o una <b>func</b>) se acumula "
        "hasta que la línea siguiente empieza en la primera columna, o hasta "
        "que se escribe una línea en blanco: es la misma regla que usa "
        "<b>codeop</b> en CPython.",
    ),
    (
        "li",
        "Un error de alcance por conjuntos no termina la sesión: la REPL lo "
        "reporta y sigue leyendo, como hace la consola de Python.",
    ),
    (
        "h",
        "El ejecutable",
    ),
    (
        "code",
        "cruthon                  # abre la REPL<br/>"
        "cruthon ejemplo.cruthon  # ejecuta un archivo<br/>"
        "cruthon -c \"blob main: ...\"  # ejecuta un fragmento",
    ),
    (
        "p",
        "El intérprete está escrito en Python siguiendo la estructura del "
        "repositorio de CPython: el tokenizador usa el mismo modelo de "
        "sangría, el analizador desciende recursivamente sobre el árbol de "
        "sintaxis y el evaluador lo recorre sustituyendo la resolución por MRO "
        "por la resolución por pertenencia a conjuntos.",
    ),
    (
        "p",
        "Los ejemplos de este documento se ejecutan tal cual (con las "
        "modificaciones menores anotadas en cada archivo) y sus resultados "
        "quedan guardados en el repositorio, junto a las pruebas que verifican "
        "que el intérprete sigue esas reglas.",
    ),
]


def _estilos() -> dict:
    """Estilos de la página, alineados con el diseño original."""
    comun = dict(
        fontName="Helvetica",
        textColor="#1f1e1d",
        spaceAfter=10,
        alignment=TA_JUSTIFY,
    )
    return {
        "titulo": ParagraphStyle(
            "titulo",
            fontName="Times-Bold",
            fontSize=24,
            leading=28,
            spaceAfter=18,
            textColor="#1f1e1d",
        ),
        "h": ParagraphStyle(
            "encabezado",
            fontName="Times-Bold",
            fontSize=12,
            leading=18,
            spaceBefore=6,
            spaceAfter=4,
            textColor="#1f1e1d",
        ),
        "p": ParagraphStyle("parrafo", fontSize=12, leading=18, **comun),
        "li": ParagraphStyle(
            "vineta",
            fontSize=12,
            leading=18,
            leftIndent=16,
            bulletIndent=2,
            spaceAfter=6,
            alignment=TA_JUSTIFY,
        ),
        "code": ParagraphStyle(
            "codigo",
            fontName="Courier",
            fontSize=10,
            leading=14,
            spaceBefore=4,
            spaceAfter=12,
            textColor="#1f1e1d",
        ),
    }


def _pie(canvas, pagina) -> None:
    """Pie de página con el mismo texto que las páginas originales."""
    canvas.saveState()
    canvas.setFont("Helvetica", 6)
    canvas.setFillColor("#1f1e1d")
    canvas.drawString(MARGIN_X, 30, "Cruthon: un lenguaje basado en teoría de conjuntos")
    canvas.drawRightString(
        PAGE_SIZE[0] - MARGIN_X, 30, f"Page {pagina.page} of {pagina.total_pages}"
    )
    canvas.restoreState()


def construir_pagina(destino: Path, total_paginas: int) -> None:
    """Genera la página nueva en un PDF de una sola página."""
    estilos = _estilos()
    historia = [Paragraph(TITULO, estilos["titulo"])]
    for tipo, texto in PARRAFOS:
        if tipo == "h":
            historia.append(Paragraph(texto, estilos["h"]))
        elif tipo == "p":
            historia.append(Paragraph(texto, estilos["p"]))
        elif tipo == "li":
            historia.append(Paragraph(texto, estilos["li"], bulletText="•"))
        elif tipo == "code":
            historia.append(Paragraph(texto, estilos["code"]))
    historia.append(Spacer(1, 6))

    documento = SimpleDocTemplate(
        str(destino),
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title="Cruthon: un lenguaje basado en teoría de conjuntos",
        author="Luis Andrés Aguilar Luhrs",
    )

    # `total_pages` se fija de antemano para que el pie diga el total real
    # del documento (la página nueva es la última).
    class _Pagina:
        page = total_paginas
        total_pages = total_paginas

    total = _Pagina()

    def _pie_final(canvas, doc):
        _pie(canvas, total)

    documento.build(historia, onFirstPage=_pie_final, onLaterPages=_pie_final)


def actualizar(origen: Path, salida: Path) -> None:
    """Une la página nueva al PDF original."""
    lector = PdfReader(str(origen))
    total = len(lector.pages) + 1
    pagina_nueva = salida.with_name(salida.stem + "_pagina.pdf")
    construir_pagina(pagina_nueva, total)

    escritor = PdfWriter()
    for pagina in lector.pages:
        escritor.add_page(pagina)
    for pagina in PdfReader(str(pagina_nueva)).pages:
        escritor.add_page(pagina)
    with open(salida, "wb") as archivo:
        escritor.write(archivo)
    pagina_nueva.unlink()
    print(f"[ok] {salida} ({total} páginas)")


def main() -> int:
    raiz = Path(__file__).resolve().parent.parent
    por_defecto = raiz / "Cruthon un lenguaje basado en teoría de conjuntos.pdf"
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument(
        "--origen", type=Path, default=por_defecto, help="PDF de diseño original"
    )
    analizador.add_argument(
        "--salida", type=Path, default=None, help="PDF de salida (por defecto, el mismo)"
    )
    argumentos = analizador.parse_args()
    salida = argumentos.salida or argumentos.origen
    actualizar(argumentos.origen, salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
