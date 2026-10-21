"""Pruebas del documento de diseño en PDF.

El entregable incluye el PDF con una página que dice explícitamente que
Cruthon se interpreta como Python y se ejecuta conforme se lee. Estas
pruebas vigilan que esa página siga ahí.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "Cruthon un lenguaje basado en teoría de conjuntos.pdf"

pypdf = pytest.importorskip("pypdf")


def test_el_pdf_del_diseno_esta_en_el_repositorio():
    assert PDF.exists(), "falta el PDF del documento de diseño"


def test_el_pdf_ultima_con_la_pagina_de_interpretacion():
    lector = pypdf.PdfReader(str(PDF))
    assert len(lector.pages) >= 12
    texto = lector.pages[-1].extract_text() or ""
    assert "interpreta" in texto
    assert "Python" in texto
    assert "conforme se lee" in texto


def test_el_pdf_conserva_las_paginas_originales():
    lector = pypdf.PdfReader(str(PDF))
    primera = lector.pages[0].extract_text() or ""
    assert "Motivación" in primera
    assert "Conceptos base" in primera


def test_el_texto_extraido_esta_en_el_repositorio():
    assert (ROOT / "docs" / "especificacion-texto.md").exists()
