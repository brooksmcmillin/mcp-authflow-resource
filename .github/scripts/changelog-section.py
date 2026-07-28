#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md, for use as GitHub release notes.

``publish.yml`` runs this on tag push so the release body is the text the
authors already wrote and reviewed, rather than something regenerated from
commit subjects.

Usage:
    changelog-section.py 0.6.0 [--changelog CHANGELOG.md]

Exits non-zero when the section is missing or has no content. That is
deliberate: a release whose notes silently came out empty is worse than a
failed workflow step, because nobody looks at a green run.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Matches this project's `## 0.6.0` headings, and tolerates the Keep a Changelog
# variants (`## [0.6.0]`, `## [0.6.0] - 2026-07-15`) in case the format shifts.
_H2 = re.compile(r"^##\s+")


def _heading_version(line: str) -> str | None:
    """The version named by a level-2 heading, or None if it isn't one."""
    if not _H2.match(line):
        return None
    text = _H2.sub("", line).strip()
    # Drop a trailing `- DATE`, then the optional surrounding brackets.
    text = text.split(" - ")[0].strip()
    return text.strip("[]").strip()


def extract(changelog: str, version: str) -> str:
    """Return the body of ``version``'s section, without its heading.

    Raises ``LookupError`` if the version has no section, or its section is
    empty (subsection headings only, no content).
    """
    if version.strip().lower() == "unreleased":
        # Only reachable by hand — the workflow derives the version from a tag.
        # Still worth refusing: publishing the unreleased block as release notes
        # would announce work that has not shipped.
        raise LookupError("refusing to extract the [Unreleased] section")

    lines = changelog.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if _heading_version(line) == version:
            start = index
            break
    if start is None:
        raise LookupError(f"no `## {version}` section in the changelog")

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _H2.match(lines[index]):
            end = index
            break

    body = "\n".join(lines[start + 1 : end]).strip()
    # A section of bare `### Added` / `### Fixed` headings with no content is
    # empty for release-notes purposes even though it is not an empty string.
    # Content need not be bullets: 0.1.0's section is a prose paragraph.
    content = [
        line for line in body.splitlines() if line.strip() and not line.lstrip().startswith("#")
    ]
    if not content:
        raise LookupError(f"`## {version}` section has no entries")
    return body


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="version to extract, without a leading v")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="path to the changelog (default: CHANGELOG.md)",
    )
    args = parser.parse_args(argv)

    try:
        text = args.changelog.read_text()
    except OSError as exc:
        print(f"ERROR: cannot read {args.changelog}: {exc}", file=sys.stderr)
        return 2

    try:
        print(extract(text, args.version))
    except LookupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
