"""Enforce repository-wide docstrings for every Python definition."""

import ast
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
PYTHON_ROOTS = ("src", "tests", "src_v2", "tests_v2")
DEFINITION_TYPES = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _python_files() -> tuple[Path, ...]:
    """Return every Python module governed by the repository instructions."""
    return tuple(
        path
        for root_name in PYTHON_ROOTS
        for path in sorted((PROJECT_DIR / root_name).rglob("*.py"))
    )


def _missing_docstrings(path: Path) -> tuple[str, ...]:
    """Return locations of modules and definitions without docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing = []
    if ast.get_docstring(tree, clean=False) is None:
        missing.append(f"{path.relative_to(PROJECT_DIR)}:1 module")
    for node in ast.walk(tree):
        if isinstance(node, DEFINITION_TYPES) and ast.get_docstring(node, clean=False) is None:
            kind = type(node).__name__
            missing.append(f"{path.relative_to(PROJECT_DIR)}:{node.lineno} {kind} {node.name}")
    return tuple(missing)


def test_every_python_definition_has_a_docstring() -> None:
    """Require docstrings on modules, classes, functions, methods, and nested definitions."""
    missing = tuple(location for path in _python_files() for location in _missing_docstrings(path))

    assert not missing, "Missing docstrings:\n" + "\n".join(missing)
