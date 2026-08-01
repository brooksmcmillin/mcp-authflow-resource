"""Guard the documented log levels against the levels the code actually emits.

Operators filter their log stack on the levels the README, the configuration
guide and the friction guide advertise, so a logger documented as DEBUG-only
that also emits INFO events silently hides those events from dashboards. Every
place that names a friction logger next to a level must therefore name the full
set of levels that logger emits.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import mcp_authflow_resource

_ROOT = Path(__file__).resolve().parent.parent
_LOGGING_MODULE = Path(mcp_authflow_resource.__file__).parent / "friction" / "logging.py"

# Longest logger name first so `...friction` does not swallow its children.
_LOGGER_NAME = re.compile(r"mcp_authflow_resource\.friction(?:\.block|\.registry)?")
_LEVEL_NAME = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")

# Files that document logger names alongside their levels.
_DOC_SOURCES = ("README.md", "docs/configuration.md", "docs/friction.md")


def _emitted_levels(tree: ast.Module) -> dict[str, set[str]]:
    """Map logger name -> level names passed to ``_emit()`` for that logger."""
    loggers: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        target, argument = node.targets[0], node.value.args[0]
        if (
            isinstance(target, ast.Name)
            and ast.unparse(node.value.func) == "logging.getLogger"
            and isinstance(argument, ast.Constant)
            and isinstance(argument.value, str)
        ):
            loggers[target.id] = argument.value

    levels: dict[str, set[str]] = {name: set() for name in loggers.values()}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "_emit"):
            continue
        logger_arg, level_arg = ast.unparse(node.args[0]), ast.unparse(node.args[1])
        levels[loggers[logger_arg]].add(level_arg.removeprefix("logging."))

    return levels


def _documented_levels(text: str) -> dict[str, set[str]]:
    """Map logger name -> level names named on the same line as that logger.

    One line per logger keeps the table rows and the comment blocks apart, and
    keeps unrelated level words elsewhere in a document out of the comparison.
    Lines that name a logger without any level (an API-reference anchor, say)
    are not claims about levels and are skipped.
    """
    documented: dict[str, set[str]] = {}
    for line in text.splitlines():
        names = set(_LOGGER_NAME.findall(line))
        levels = set(_LEVEL_NAME.findall(line))
        if len(names) != 1 or not levels:
            continue
        documented.setdefault(names.pop(), set()).update(levels)
    return documented


def test_module_docstring_lists_the_levels_it_emits() -> None:
    tree = ast.parse(_LOGGING_MODULE.read_text())
    docstring = ast.get_docstring(tree)
    assert docstring is not None

    assert _documented_levels(docstring) == _emitted_levels(tree)


def test_docs_list_the_levels_the_loggers_emit() -> None:
    emitted = _emitted_levels(ast.parse(_LOGGING_MODULE.read_text()))

    for source in _DOC_SOURCES:
        documented = _documented_levels((_ROOT / source).read_text())
        assert documented, f"{source} documents no logger levels"
        for logger_name, levels in documented.items():
            assert levels == emitted[logger_name], (
                f"{source} documents {logger_name} as {sorted(levels)}, "
                f"but it emits {sorted(emitted[logger_name])}"
            )
