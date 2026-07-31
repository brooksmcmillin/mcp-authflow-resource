"""Guard against docstrings that advertise decorators the package does not ship.

Module and API docstrings are rendered verbatim in the mkdocstrings API
reference, so a bare ``@something()`` in an example is read as an import an
integrator can copy.  Any such name must therefore resolve either in the
module it is documented in or in the top-level package namespace.  Dotted
names (``@app.tool()``, ``@functools.wraps``) are placeholders for objects
that come from elsewhere and are skipped.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import mcp_authflow_resource

_PACKAGE_ROOT = Path(mcp_authflow_resource.__file__).parent
_DECORATOR = re.compile(r"^\s*@([A-Za-z_][A-Za-z0-9_]*)\s*[(\n]", re.MULTILINE)


def _docstrings(tree: ast.AST) -> list[str]:
    nodes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    return [
        doc
        for node in ast.walk(tree)
        if isinstance(node, nodes) and (doc := ast.get_docstring(node)) is not None
    ]


def test_docstring_decorator_examples_are_importable() -> None:
    unresolved: list[str] = []

    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        relative = path.relative_to(_PACKAGE_ROOT).with_suffix("")
        parts = [p for p in relative.parts if p != "__init__"]
        module_name = ".".join(["mcp_authflow_resource", *parts])
        module = importlib.import_module(module_name)

        for doc in _docstrings(ast.parse(path.read_text())):
            for name in _DECORATOR.findall(doc):
                if hasattr(module, name) or hasattr(mcp_authflow_resource, name):
                    continue
                unresolved.append(f"{module_name}: @{name}")

    assert not unresolved, "docstrings reference decorators that do not exist: " + ", ".join(
        unresolved
    )
