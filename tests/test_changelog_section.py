"""Tests for the release-notes extractor used by ``publish.yml``.

The script lives under ``.github/scripts/`` and is hyphenated, so it is not
importable as a module; it is loaded via importlib.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PATH = _ROOT / ".github" / "scripts" / "changelog-section.py"
_SPEC = importlib.util.spec_from_file_location("changelog_section", _PATH)
assert _SPEC and _SPEC.loader
cs = importlib.util.module_from_spec(_SPEC)
sys.modules["changelog_section"] = cs
_SPEC.loader.exec_module(cs)


_CHANGELOG = """\
# Changelog

Some preamble that must never end up in release notes.

## [Unreleased]

### Added

### Security

- an entry that has not shipped yet

## 0.6.0

### Added

- **A new thing.** With a second line of prose that belongs to the entry.

### Fixed

- A bug fix.

## 0.5.2

### Security

- Rejected a bad auth combination.

## 0.5.1
"""


def test_extracts_only_the_requested_section() -> None:
    body = cs.extract(_CHANGELOG, "0.6.0")
    assert "A new thing" in body
    assert "A bug fix" in body
    # Neither the neighbouring releases nor the unreleased block leak in.
    assert "not shipped yet" not in body
    assert "Rejected a bad auth combination" not in body
    assert "preamble" not in body


def test_version_heading_is_not_included() -> None:
    # The GitHub release already displays the version as its title; repeating it
    # in the body renders as a redundant H2. Subsection headings are kept.
    body = cs.extract(_CHANGELOG, "0.6.0")
    assert not body.startswith("## 0.6.0")
    assert body.startswith("### Added")


def test_prose_only_section_is_not_treated_as_empty() -> None:
    # 0.1.0 in the real changelog is a paragraph with no bullets. Requiring
    # bullets would fail the workflow on a section that has perfectly good notes.
    changelog = "## 0.1.0\n\nInitial release with a prose summary.\n"
    assert "prose summary" in cs.extract(changelog, "0.1.0")


def test_last_section_runs_to_end_of_file() -> None:
    body = cs.extract(_CHANGELOG, "0.5.2")
    assert "Rejected a bad auth combination" in body


def test_unknown_version_raises() -> None:
    with pytest.raises(LookupError, match="no `## 9.9.9` section"):
        cs.extract(_CHANGELOG, "9.9.9")


def test_section_without_entries_raises() -> None:
    # `## 0.5.1` exists but has no bullets. Shipping that as release notes would
    # produce an empty release body, so it must fail the workflow instead.
    with pytest.raises(LookupError, match="no entries"):
        cs.extract(_CHANGELOG, "0.5.1")


def test_unreleased_is_refused() -> None:
    # Publishing the unreleased block as release notes would announce work that
    # has not shipped.
    with pytest.raises(LookupError, match="refusing"):
        cs.extract(_CHANGELOG, "Unreleased")


@pytest.mark.parametrize(
    "heading",
    ["## 0.6.0", "## [0.6.0]", "## [0.6.0] - 2026-07-15", "##   0.6.0  "],
)
def test_heading_variants_are_recognized(heading: str) -> None:
    changelog = f"{heading}\n\n### Added\n\n- something\n"
    assert "something" in cs.extract(changelog, "0.6.0")


def test_cli_prints_section(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_CHANGELOG)
    assert cs.main(["0.6.0", "--changelog", str(path)]) == 0
    assert "A new thing" in capsys.readouterr().out


def test_cli_fails_on_missing_version(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_CHANGELOG)
    assert cs.main(["9.9.9", "--changelog", str(path)]) == 1


def test_cli_fails_on_unreadable_changelog(tmp_path: Path) -> None:
    assert cs.main(["0.6.0", "--changelog", str(tmp_path / "nope.md")]) == 2


def test_every_released_version_has_extractable_notes() -> None:
    """The real CHANGELOG.md must work for every version it documents.

    Guards the workflow against a heading style or empty section that would only
    surface at tag-push time, when the artifact is already on PyPI.
    """
    text = (_ROOT / "CHANGELOG.md").read_text()
    versions = [
        version
        for line in text.splitlines()
        if (version := cs._heading_version(line)) and version != "Unreleased"
    ]
    assert versions, "no released versions found in CHANGELOG.md"
    for version in versions:
        assert cs.extract(text, version).strip(), f"{version} yielded empty notes"
