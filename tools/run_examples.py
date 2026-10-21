#!/usr/bin/env python3
"""Ejecuta los ejemplos del documento de diseño y verifica sus resultados.

Uso::

    python tools/run_examples.py            # ejecuta y compara
    python tools/run_examples.py --update   # regenera los resultados guardados

Cada ejemplo vive en ``examples/`` y su salida esperada en
``examples/expected/<nombre>.out``. La comparación es literal, así que los
resultados guardados en el repositorio son exactamente los que produce el
intérprete.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
EXPECTED = EXAMPLES / "expected"


def run_example(path: Path) -> tuple:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONIOPATH"] = str(ROOT)
    process = subprocess.run(
        [sys.executable, "-m", "cruthon", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(ROOT),
    )
    return process.returncode, process.stdout, process.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="regenera los resultados")
    args = parser.parse_args()

    EXPECTED.mkdir(exist_ok=True)
    failures = 0
    files = sorted(EXAMPLES.glob("*.cruthon"))
    for path in files:
        target = EXPECTED / (path.stem + ".out")
        code, stdout, stderr = run_example(path)
        if code != 0:
            failures += 1
            print(f"[FALLO] {path.name}: terminó con código {code}")
            if stderr:
                print(stderr.rstrip())
            continue
        if args.update:
            target.write_text(stdout, encoding="utf-8")
            print(f"[guardado] {target.relative_to(ROOT)}")
            continue
        if not target.exists():
            failures += 1
            print(f"[FALLO] {path.name}: no existe {target.relative_to(ROOT)}")
            continue
        expected = target.read_text(encoding="utf-8")
        if expected != stdout:
            failures += 1
            print(f"[FALLO] {path.name}: la salida no coincide con {target.name}")
            _diff(expected, stdout)
        else:
            print(f"[ok] {path.name}")
    if failures:
        print(f"\n{failures} ejemplo(s) con diferencias")
        return 1
    print(f"\nTodos los ejemplos ({len(files)}) coinciden con sus resultados guardados.")
    return 0


def _diff(expected: str, actual: str) -> None:
    import difflib

    diff = difflib.unified_diff(
        expected.splitlines(keepends=True),
        actual.splitlines(keepends=True),
        fromfile="esperado",
        tofile="obtenido",
    )
    for line in diff:
        print("   " + line.rstrip("\n"))


if __name__ == "__main__":
    raise SystemExit(main())
