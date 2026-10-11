"""Que todo el código compile (scan.py no lo importa ningún otro test: un error de sintaxis tumbaría el servicio)."""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_everything_compiles():
    files = [ROOT / "scan.py", ROOT / "clv.py", ROOT / "dashboard.py", *ROOT.glob("surebet/**/*.py")]
    for f in files:
        py_compile.compile(str(f), doraise=True)
