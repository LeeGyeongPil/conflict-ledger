"""AC-7 (release part): every place that names the version says the same thing, and the changelog matches 0.1.3."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import conflict_ledger as cl

ROOT = Path(__file__).resolve().parent.parent
VERSION = "0.1.3"
PREVIOUS = "0.1.2"                       # already tagged and pushed: its changelog entry and SKILL.md stay as they were
RELEASED = ("0.1.0", "0.1.1", "0.1.2")   # pushed tags, never moved


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def show_at_tag(tag: str, path: str) -> str:
    """The file as it is in a release tag (skips the test where git or the tag is not there, e.g. shallow CI)."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{tag}:{path}"], capture_output=True, text=True,
                       encoding="utf-8", check=False)
    if r.returncode != 0:
        pytest.skip(f"tag {tag} is not present in this checkout")
    return r.stdout


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


def changelog_sections(text: str | None = None) -> dict[str, str]:
    text = read("CHANGELOG.md") if text is None else text
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    return {p.split("\n", 1)[0]: p for p in parts}


def test_changelog_top_entry():
    """0.1.3: the image-only evaluation case is removed (with the reason) and file_missing says how to fix it."""
    sections = changelog_sections()
    first = next(iter(sections))
    assert re.fullmatch(rf"\[{re.escape(VERSION)}\] - \d{{4}}-\d{{2}}-\d{{2}}", first)
    entry = sections[first]
    changed = entry.split("### Changed", 1)[1].split("###", 1)[0]
    for needle in ("task-error-binary", "task-error-disguised-binary", "looking at the images", "6 task cases"):
        assert needle in changed, needle
    fixed = entry.split("### Fixed", 1)[1].split("###", 1)[0]
    assert "file_missing" in fixed and "check the path" in fixed
    assert "receipts do not change" in entry                                 # the audit's output did not change
    headings = re.findall(r"^### .*$", entry, re.M)
    assert len(headings) == len(set(headings)), headings                     # no repeated ### heading in the entry
    text = read("CHANGELOG.md")
    for version, before in ((VERSION, PREVIOUS), (PREVIOUS, "0.1.1"), ("0.1.1", "0.1.0")):
        link = f"[{version}]: https://github.com/LeeGyeongPil/conflict-ledger/compare/v{before}...v{version}"
        assert link in text, link


def test_changelog_previous_entries_are_kept():
    sections = changelog_sections()
    title = next(t for t in sections if t.startswith("[0.1.1] - "))
    entry = sections[title]
    fixed = entry.split("### Fixed", 1)[1].split("###", 1)[0]
    for needle in ("First command", "early-exit", "input_error", "by content",       # soak fixes
                   "(1 hunk needs judgement)",                                       # F-1
                   "applied 1 hunk", "labels git wrote",                             # Q-15
                   "step 6"):                                                        # F-3
        assert needle in fixed, needle
    assert "### Changed" in entry and "unmerged_kinds" in entry and "task-error-disguised-binary" in entry
    # the published 0.1.2 and 0.1.1 entries stay byte for byte as in tag v0.1.2 (the 0.1.0 entry carries the
    # link list, which grows with every release, so it is not compared)
    tagged = changelog_sections(show_at_tag(f"v{PREVIOUS}", "CHANGELOG.md"))
    for prefix in (f"[{PREVIOUS}] - ", "[0.1.1] - "):
        now = next(t for t in sections if t.startswith(prefix))
        before = next(t for t in tagged if t.startswith(prefix))
        assert sections[now] == tagged[before], prefix


def test_known_issues_hold_only_what_is_left():
    text = read("CHANGELOG.md")
    assert text.count("### Known issues") == 1                               # only the 0.1.1 one; later lists are bold
    entry = next(iter(changelog_sections().values()))
    known = entry.split("**Known issues (still open):**", 1)[1]
    items = [line for line in known.splitlines() if line.startswith("- ")]
    assert len(items) == 3
    assert "receipt" in items[0] and "ours: (deleted)" in items[1] and "image" in items[2]         # Q-12, F-2, images
    assert "file_missing" not in known                                       # fixed in 0.1.3


def section(text: str, start: str, end: str = "\n## ") -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def test_readmes_show_the_version_and_the_scope_note():
    en, ko = read("README.md"), read("README.ko.md")
    assert en.count(f"Current version: {VERSION}") == 2 and ko.count(f"현재 버전: {VERSION}") == 2
    assert "PASS: no lost changes found (1 hunk needs judgement)" in en
    assert "통과: 유실된 변경 없음 (판단이 필요한 덩어리 1개)" in ko
    assert "applied 1 hunk (H3); 2 remaining (H1, H2)" in en
    assert "덩어리 1개를 적용했고 2개가 남았습니다. 적용: H3 / 남음: H1, H2" in ko
    for text in (en, ko):
        assert "<<<<<<< HEAD" in text and ">>>>>>> feature" in text          # Q-15: git's own labels
        assert '"input_error": {' in text and '"kind": "binary"' in text
        assert "(2 hunks need judgement)" not in text and "덩어리 2개)" not in text and "1 hunks" not in text
        for old in RELEASED:
            assert old not in text, old                                      # no stale version mention
        # the scope note sits right after the skill-eval markers (outside them, so the table refresh keeps it)
        note = section(text, "<!-- skill-eval:end -->")
        assert "task-error-binary" in note and "task-error-disguised-binary" in note
        assert "task-error-binary" not in section(text, "<!-- skill-eval:start -->", "<!-- skill-eval:end -->")
    assert "6 task cases" in en and "7 task cases" not in en
    assert "작업 6개" in ko and "작업 7개" not in ko
    assert "image" in section(en, "## Limitations")
    assert "이미지" in section(ko, "## Limitations")


def test_readmes_have_no_fixed_test_count():
    """The number of tests changes with the checkout (tag tests skip without tags), so the README never states it."""
    assert not re.search(r"\b\d+ (tests|passed)\b", read("README.md"))
    assert not re.search(r"테스트 \d+개|\d+개 테스트|\d+ passed", read("README.ko.md"))


@pytest.mark.parametrize("version", RELEASED)
def test_release_tags_are_untouched(version):
    """Published tags keep pointing at their own files: a fix is a new version, never a moved tag."""
    assert json.loads(show_at_tag(f"v{version}", ".claude-plugin/plugin.json"))["version"] == version


def test_skill_md_body_is_the_measured_one():
    """0.1.3 re-measures the SKILL.md that 0.1.2 measured: only the version line may differ."""
    tagged = show_at_tag(f"v{PREVIOUS}", "skills/conflict-ledger/SKILL.md")
    assert f'version: "{PREVIOUS}"' in tagged
    expected = tagged.replace(f'version: "{PREVIOUS}"', f'version: "{VERSION}"', 1)
    assert read("skills/conflict-ledger/SKILL.md") == expected
