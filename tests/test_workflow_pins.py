"""Guards against GitHub Actions steps drifting back onto mutable refs.

A branch pin (``@release/v1``, ``@main``, ...) lets whoever controls the upstream
branch change what we execute after review.  Version tags are only marginally
better: ``@v7`` is a pointer the upstream owner can force-push.  So every step
is pinned to a full commit SHA with a ``# vX.Y.Z`` comment naming the release it
came from, and Dependabot (``.github/dependabot.yml``) bumps the pins.

This matters most in ``publish.yml``, which runs with ``id-token: write`` in the
``pypi`` environment, so a compromised action could push a release of this
library.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"

# `uses: owner/repo@ref` — optionally followed by a `# vX.Y.Z` version comment.
USES_RE = re.compile(
    r"^\s*-?\s*uses:\s*(?P<action>[^\s@]+)@(?P<ref>[^\s#]+)\s*(?:#\s*(?P<comment>\S+))?"
)

MUTABLE_REF_RE = re.compile(r"^(main|master|HEAD|dev|develop|release/.*)$")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

VERSION_COMMENT_RE = re.compile(r"^v\d")


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.y*ml"))


def _uses_steps(path: Path) -> list[tuple[str, str]]:
    return [(action, ref) for action, ref, _ in _uses_steps_with_comments(path)]


def _uses_steps_with_comments(path: Path) -> list[tuple[str, str, str | None]]:
    steps = []
    for line in path.read_text().splitlines():
        match = USES_RE.match(line)
        if match:
            steps.append((match["action"], match["ref"], match["comment"]))
    return steps


def test_workflow_dir_is_discoverable() -> None:
    """Fail loudly rather than passing vacuously if the layout moves."""
    assert _workflow_files(), f"no workflows found under {WORKFLOW_DIR}"


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_no_action_pinned_to_mutable_ref(workflow: Path) -> None:
    offenders = [
        f"{action}@{ref}" for action, ref in _uses_steps(workflow) if MUTABLE_REF_RE.match(ref)
    ]
    assert not offenders, (
        f"{workflow.name} pins actions to mutable refs: {offenders}. "
        "Pin to a tag, or to a full commit SHA for privileged workflows."
    )


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_every_action_is_sha_pinned(workflow: Path) -> None:
    offenders = [
        f"{action}@{ref}" for action, ref in _uses_steps(workflow) if not SHA_RE.match(ref)
    ]
    assert not offenders, (
        f"{workflow.name} pins actions to mutable refs: {offenders}. "
        "Tags can be force-pushed upstream; pin to a full 40-char commit SHA "
        "with a `# vX.Y.Z` comment instead."
    )


@pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: p.name)
def test_sha_pins_carry_a_version_comment(workflow: Path) -> None:
    """A bare SHA is unreadable; the comment says which release it is."""
    offenders = [
        f"{action}@{ref}"
        for action, ref, comment in _uses_steps_with_comments(workflow)
        if SHA_RE.match(ref) and not (comment and VERSION_COMMENT_RE.match(comment))
    ]
    assert not offenders, (
        f"{workflow.name} has SHA pins without a version comment: {offenders}. "
        "Append `# vX.Y.Z` so the pin is reviewable."
    )


def test_pypi_publish_action_is_sha_pinned() -> None:
    """The trusted-publishing step is the highest-value target, so require a SHA."""
    refs = [
        ref
        for action, ref in _uses_steps(WORKFLOW_DIR / "publish.yml")
        if action == "pypa/gh-action-pypi-publish"
    ]
    assert refs, "publish.yml no longer invokes pypa/gh-action-pypi-publish"
    unpinned = [ref for ref in refs if not SHA_RE.match(ref)]
    assert not unpinned, (
        "gh-action-pypi-publish runs with id-token: write; pin it to a full "
        f"40-char commit SHA with a version comment, not {unpinned}"
    )
