"""AC-7 (release part): every place that names the version says the same thing, and the changelog matches 0.1.2."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import conflict_ledger as cl

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.2"
PREVIOUS = "0.1.1"                       # already tagged and pushed: its changelog entry stays as it was


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_package_plugin_script_and_lock_agree():
    assert re.search(rf'^version = "{re.escape(VERSION)}"$', read("pyproject.toml"), re.M)
    assert json.loads(read(".claude-plugin/plugin.json"))["version"] == VERSION
    market = json.loads(read(".claude-plugin/marketplace.json"))
    versions = [market.get("version"), *[p.get("version") for p in market["plugins"]]]
    assert all(v in (None, VERSION) for v in versions)           # only checked where a version field exists
    assert cl.__version__ == VERSION
    lock = read("uv.lock")
    assert re.search(rf'name = "conflict-ledger"\nversion = "{re.escape(VERSION)}"', lock)
    assert f'version: "{VERSION}"' in read("skills/conflict-ledger/SKILL.md")


def test_version_option(run_cli):
    code, out, _ = run_cli("--version")
    assert code == 0 and out.strip() == f"conflict_ledger.py {VERSION}"


def changelog_sections() -> dict[str, str]:
    text = read("CHANGELOG.md")
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0]: p for p in parts}


def test_changelog_top_entry():
    """0.1.2: SKILL.md says the first command holds for every file type (an image is not audited by viewing it)."""
    sections = changelog_sections()
    first = next(iter(sections))
    assert re.fullmatch(rf"\[{re.escape(VERSION)}\] - \d{{4}}-\d{{2}}-\d{{2}}", first)
    entry = sections[first]
    fixed = entry.split("### Fixed", 1)[1].split("###", 1)[0]
    assert "every file type" in fixed and "viewing an image" in fixed and "First command" in fixed
    assert "receipts are the same" in entry                                  # the script's output did not change
    text = read("CHANGELOG.md")
    for version, before in ((VERSION, PREVIOUS), (PREVIOUS, "0.1.0")):
        link = f"[{version}]: https://github.com/LeeGyeongPil/conflict-ledger/compare/v{before}...v{version}"
        assert link in text, link


def test_changelog_previous_entry_is_kept():
    sections = changelog_sections()
    title = next(t for t in sections if t.startswith(f"[{PREVIOUS}] - "))
    entry = sections[title]
    fixed = entry.split("### Fixed", 1)[1].split("###", 1)[0]
    for needle in ("First command", "early-exit", "input_error", "by content",       # soak fixes
                   "(1 hunk needs judgement)",                                       # F-1
                   "applied 1 hunk", "labels git wrote",                             # Q-15
                   "step 6"):                                                        # F-3
        assert needle in fixed, needle
    assert "### Changed" in entry and "unmerged_kinds" in entry and "task-error-disguised-binary" in entry


def test_known_issues_hold_only_what_is_left():
    text = read("CHANGELOG.md")
    assert text.count("### Known issues") == 1
    known = text.split("### Known issues", 1)[1].split("\n## ", 1)[0]
    items = [line for line in known.splitlines() if line.startswith("- ")]
    assert len(items) == 3
    assert "receipt" in items[0] and "file_missing" in items[1] and "ours: (deleted)" in items[2]               # Q-12, F-2
    for fixed in ("hunks need judgement", "1 hunks", "summary:", "marker label"):
        assert fixed not in known, fixed


def test_readmes_show_the_version_and_the_new_output():
    en, ko = read("README.md"), read("README.ko.md")
    assert f"Current version: {VERSION}" in en and f"현재 버전: {VERSION}" in ko
    assert "PASS: no lost changes found (1 hunk needs judgement)" in en
    assert "통과: 유실된 변경 없음 (판단이 필요한 덩어리 1개)" in ko
    assert "applied 1 hunk (H3); 2 remaining (H1, H2)" in en
    assert "덩어리 1개를 적용했고 2개가 남았습니다. 적용: H3 / 남음: H1, H2" in ko
    for text in (en, ko):
        assert "<<<<<<< HEAD" in text and ">>>>>>> feature" in text          # Q-15: git's own labels
        assert '"input_error": {' in text and '"kind": "binary"' in text
        assert "(2 hunks need judgement)" not in text and "덩어리 2개)" not in text and "1 hunks" not in text
        assert "0.1.0" not in text and PREVIOUS not in text                    # no stale version mention


def test_tag_v0_1_0_is_untouched():
    """The 0.1.0 release tag still points at the 0.1.0 files (skipped where tags are not fetched, e.g. shallow CI)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    r = subprocess.run(["git", "-C", str(ROOT), "show", "v0.1.0:.claude-plugin/plugin.json"], capture_output=True, text=True,
                       check=False)
    if r.returncode != 0:
        pytest.skip("tag v0.1.0 is not present in this checkout")
    assert json.loads(r.stdout)["version"] == "0.1.0"


def test_tag_of_the_previous_release_is_untouched():
    """v0.1.1 was already published, so 0.1.2 is a new version instead of a moved tag."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"v{PREVIOUS}:.claude-plugin/plugin.json"], capture_output=True,
                       text=True, check=False)
    if r.returncode != 0:
        pytest.skip(f"tag v{PREVIOUS} is not present in this checkout")
    assert json.loads(r.stdout)["version"] == PREVIOUS
