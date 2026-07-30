"""Guards against the bandit version drifting between pre-commit and CI.

``pre-commit`` installs a hook repo from its ``rev``, so the bandit that runs
locally is whatever ``1.9.2`` tags.  Two things can silently break that:

* re-declaring bandit in ``additional_dependencies`` — an unpinned
  ``bandit[toml]`` floats to the latest PyPI release, and a pinned one conflicts
  with the install pre-commit already made from ``rev``;
* the ``Security | Bandit`` CI job, which installs bandit itself via ``uvx`` and
  therefore has to repeat the version by hand.

Either way developers and CI end up scanning with different rule sets, so a
finding shows up in one place and not the other.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

BANDIT_REPO = "https://github.com/PyCQA/bandit"

# A repo entry starts at `  - repo: ...`; the next one at the same indent ends it.
REPO_START_RE = re.compile(r"^(?P<indent>\s*)-\s*repo:\s*(?P<url>\S+)")
REV_RE = re.compile(r"^\s*rev:\s*(?P<rev>\S+)")
ADDITIONAL_DEPS_RE = re.compile(r"^\s*additional_dependencies:\s*(?P<deps>.*)$")

# `uvx 'bandit[toml]==1.9.2' -c pyproject.toml ...`
UVX_BANDIT_RE = re.compile(r"uvx\s+'(?P<spec>bandit(?:\[[^\]]*\])?[^']*)'")

EXACT_VERSION_RE = re.compile(r"^\d+(\.\d+)*$")


def _bandit_hook_block() -> list[str]:
    """Return the lines of the PyCQA/bandit repo entry in the pre-commit config."""
    lines = PRE_COMMIT_CONFIG.read_text().splitlines()
    block: list[str] = []
    indent: str | None = None
    for line in lines:
        start = REPO_START_RE.match(line)
        if indent is not None and start and start["indent"] == indent:
            break  # next repo entry at the same level
        if indent is not None:
            block.append(line)
        elif start and start["url"] == BANDIT_REPO:
            indent = start["indent"]
            block.append(line)
    return block


def _bandit_hook_rev() -> str:
    block = _bandit_hook_block()
    assert block, f"no {BANDIT_REPO} entry in {PRE_COMMIT_CONFIG.name}"
    for line in block:
        match = REV_RE.match(line)
        if match:
            return match["rev"]
    raise AssertionError("the bandit pre-commit entry has no rev")


def test_bandit_hook_is_discoverable() -> None:
    """Fail loudly rather than passing vacuously if the config layout moves."""
    assert _bandit_hook_block(), f"no {BANDIT_REPO} entry in {PRE_COMMIT_CONFIG.name}"


def test_bandit_hook_rev_is_an_exact_version() -> None:
    rev = _bandit_hook_rev()
    assert EXACT_VERSION_RE.match(rev), (
        f"the bandit hook rev is {rev!r}; pin it to an exact release so the "
        "executed version is reproducible"
    )


def test_bandit_hook_does_not_reinstall_bandit() -> None:
    """pre-commit already installs bandit from `rev`; asking again floats or conflicts."""
    offenders = [
        match["deps"]
        for line in _bandit_hook_block()
        if (match := ADDITIONAL_DEPS_RE.match(line)) and "bandit" in match["deps"]
    ]
    assert not offenders, (
        f"the bandit hook re-declares bandit in additional_dependencies: {offenders}. "
        "pre-commit installs the hook repo at `rev`, so an unpinned spec floats to "
        "the latest PyPI release and a pinned one fails to resolve."
    )


def test_ci_bandit_pin_matches_hook_rev() -> None:
    specs = UVX_BANDIT_RE.findall(CI_WORKFLOW.read_text())
    assert specs, f"{CI_WORKFLOW.name} no longer runs bandit via uvx"
    rev = _bandit_hook_rev()
    mismatched = [spec for spec in specs if not spec.endswith(f"=={rev}")]
    assert not mismatched, (
        f"{CI_WORKFLOW.name} runs {mismatched} but the pre-commit hook is pinned to "
        f"{rev}; pin both to the same version so local and CI scans agree."
    )
