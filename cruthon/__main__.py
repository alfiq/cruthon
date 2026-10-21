"""Permite ejecutar el lenguaje con ``python -m cruthon``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
