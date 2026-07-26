"""Guards against workflows inheriting the repository's default token scopes.

Without a top-level ``permissions:`` block, every job in a workflow gets whatever
the repository default is — which can be read/write on ``contents``.  Declaring
the least-privilege set at the top and letting individual jobs opt into more
keeps a compromised action from using ``GITHUB_TOKEN`` to push commits, publish
releases, or edit issues.
"""

from __future__ import annotations

import re
from itertools import takewhile
from pathlib import Path

import pytest

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# A top-level key is unindented, so `^permissions:` never matches a job-level block.
TOP_LEVEL_PERMISSIONS_RE = re.compile(r"^permissions:\s*(?P<inline>\S.*)?$")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def _top_level_permissions(path: Path) -> str | None:
    """Return the workflow's top-level permissions value, or None if absent."""
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        match = TOP_LEVEL_PERMISSIONS_RE.match(line)
        if not match:
            continue
        if match["inline"]:
            return match["inline"]
        block = takewhile(lambda item: item.startswith((" ", "\t")), lines[index + 1 :])
        return "\n".join(block)
    return None


def test_workflow_dir_is_discoverable() -> None:
    """Fail loudly rather than passing vacuously if the layout moves."""
    assert _workflow_files(), f"no workflows found under {WORKFLOW_DIR}"


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_workflow_declares_top_level_permissions(workflow: Path) -> None:
    permissions = _top_level_permissions(workflow)

    assert permissions is not None, (
        f"{workflow.name} has no top-level `permissions:` block, so its jobs run with "
        "the repository default GITHUB_TOKEN scopes. Declare the least-privilege set "
        "(usually `contents: read`, or `{}`) and let individual jobs opt into more."
    )
    assert permissions.strip(), (
        f"{workflow.name} declares an empty `permissions:` key, which YAML reads as null "
        "rather than a scope set; write `permissions: {}` to grant nothing."
    )
