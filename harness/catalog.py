"""Discover exactly 100 single-mechanism experiment definitions."""
import importlib
from pathlib import Path
from collections import Counter
import re


def load(require_complete=True):
    result = []
    for path in sorted((Path(__file__).parent / "directions").glob("d[0-9][0-9]_*.py")):
        module = importlib.import_module(f"harness.directions.{path.stem}")
        result.extend(module.experiments())
    ids = [e.id for e in result]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate experiment IDs")
    if any(not re.fullmatch(r"D(?:0[1-9]|10)\.(?:0[1-9]|10)", i) for i in ids):
        raise ValueError("Expected D01.01 through D10.10")
    if require_complete and Counter(i[:3] for i in ids) != Counter({f"D{i:02}": 10 for i in range(1, 11)}):
        raise ValueError(f"Incomplete catalog: {len(ids)} experiments")
    return sorted(result, key=lambda e: e.id)
