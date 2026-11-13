"""AC-8: evaluation set composition and grader values that match the script's own answers."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
SCRIPT = ROOT / "skills" / "conflict-ledger" / "scripts" / "conflict_ledger.py"

# how the grader values of each task case are produced (the same command the case asks for)
COMMANDS = {
    "task-theirs-wholesale": ["verify", "--base", "base.cfg", "--ours", "ours.cfg", "--theirs", "theirs.cfg", "--result", "resolved.cfg"],
    "task-rebase-labels": ["verify", "--base", "stage1_base.py", "--ours", "stage2_upstream.py", "--theirs", "stage3_mine.py",
                           "--result", "resolved.py", "--operation", "rebase"],
    "task-trivial-then-judge": ["resolve-trivial", "--base", "base.py", "--ours", "ours.py", "--theirs", "theirs.py", "--out", "merged.py"],
    "task-duplicate-braces": ["verify", "--base", "base.go", "--ours", "ours.go", "--theirs", "theirs.go", "--result", "resolved.go"],
    "task-blanked-markdown": ["verify", "--base", "base.md", "--ours", "ours.md", "--theirs", "theirs.md", "--result", "resolved.md"],
    "task-error-binary": ["verify", "--base", "logo_base.png", "--ours", "logo_ours.png", "--theirs", "logo_theirs.png",
                          "--result", "logo.png"],
    "task-error-disguised-binary": ["verify", "--base", "export_base.txt", "--ours", "export_ours.txt", "--theirs",
                                    "export_theirs.txt", "--result", "export.txt"],
}

# sha256 (first 16 hex) of prompt.md + graders/*.md of the six 0.1.0 task cases: 0.1.1 must not change them
FROZEN_TASKS = {
    "task-blanked-markdown": "f59289c3835c328b",
    "task-duplicate-braces": "cff225034384709a",
    "task-error-binary": "e4fd13a959b4c46f",
    "task-rebase-labels": "5adf5d07ee096999",
    "task-theirs-wholesale": "b9f42f2b6c06be66",
    "task-trivial-then-judge": "4533ac154d234f65",
}

# near-miss queries rewritten in 0.1.1 (plan section 4): same topic, "show me, change nothing"
NEAR_REWORDED = {
    "notrigger-near-02": "What should I put in .gitattributes and git config to use a custom merge driver for "
                         "package-lock.json? Just show me the lines, don't change any files.",
    "notrigger-near-05": "Show me a GitHub Actions job that fails PRs which have merge conflicts with main - just the YAML, "
                         "don't create files.",
    "notrigger-near-07": "How do I undo my last merge and go back to before it? Give me the commands, don't run them.",
}


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    block = text.split("---\n")[1]
    out = {}
    for line in block.splitlines():
        key, _, value = line.partition(": ")
        value = value.strip()
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        out[key] = value
    return out


def cases(prefix: str) -> list[Path]:
    return sorted(d for d in EVALS.iterdir() if d.is_dir() and d.name.startswith(prefix))


def test_case_counts():
    triggers = cases("trigger-")
    assert len(triggers) >= 10
    assert sum(1 for c in triggers if re.search(r"[가-힣]", (c / "prompt.md").read_text(encoding="utf-8"))) >= 2
    assert len(cases("notrigger-")) >= 10 and len(cases("notrigger-near-")) >= 5
    tasks = cases("task-")
    assert len(tasks) >= 5 and len([t for t in tasks if t.name.startswith("task-error-")]) >= 1


def test_composition_of_the_0_1_1_set():
    assert len(cases("trigger-")) == 12
    assert len(cases("notrigger-")) == 12 and len(cases("notrigger-near-")) == 8
    tasks = [c.name for c in cases("task-")]
    assert len(tasks) == 7
    assert [t for t in tasks if t.startswith("task-error-")] == ["task-error-binary", "task-error-disguised-binary"]


def test_existing_task_cases_are_unchanged():
    """The six 0.1.0 task prompts and graders stay byte for byte the same (only a new case is added)."""
    for name, expected in FROZEN_TASKS.items():
        case = EVALS / name
        digest = hashlib.sha256()
        for f in sorted([case / "prompt.md", *(case / "graders").glob("*.md")]):
            digest.update(f.relative_to(case).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
        assert digest.hexdigest()[:16] == expected, name


def test_trigger_and_non_trigger_budget_stays_six_turns():
    for case in cases("trigger-") + cases("notrigger-"):
        assert frontmatter(case / "prompt.md")["max_turns"] == "6", case.name


def test_reworded_near_miss_queries():
    for name, text in NEAR_REWORDED.items():
        body = (EVALS / name / "prompt.md").read_text(encoding="utf-8").split("---\n", 2)[2].strip()
        assert body == text, name
        assert re.search(r"don't (change|create|run)", body)      # asks to show, not to do the work in the folder


def test_disguised_binary_fixture_is_binary_by_content_only():
    fixture = EVALS / "fixtures" / "task-error-disguised-binary"
    files = sorted(fixture.iterdir())
    assert len(files) == 4 and all(f.suffix == ".txt" for f in files)
    assert all(b"\0" in f.read_bytes() for f in files)


def test_trigger_prompts_do_not_name_the_skill():
    for case in cases("trigger-"):
        body = (case / "prompt.md").read_text(encoding="utf-8").split("---\n", 2)[2]
        assert not re.search(r"conflict-ledger|\bskill\b|스킬", body, re.I), case.name


def test_every_task_has_fixtures_and_a_value_grader():
    for case in cases("task-"):
        fixture = EVALS / "fixtures" / case.name
        assert fixture.is_dir() and any(fixture.iterdir()), case.name
        graders = {g.stem: frontmatter(g) for g in (case / "graders").glob("*.md")}
        assert "script-ran" in graders
        values = [g for k, g in graders.items() if g["type"] == "regex"]
        assert values, case.name
        assert case.name in COMMANDS, f"add the command for {case.name}"
        assert all(len(g["pattern"]) >= 6 for g in values)


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_grader_values_match_script_output(name, tmp_path):
    work = tmp_path / "w"
    shutil.copytree(EVALS / "fixtures" / name, work)
    extra = ["--findings-file", "findings.txt"] if name != "task-error-binary" else []      # the 0.1.0 case asks for no file
    run = subprocess.run([sys.executable, str(SCRIPT), "--lang", "en", *COMMANDS[name], *extra], cwd=work,
                         capture_output=True, text=True, check=False)
    trace = run.stdout + run.stderr
    findings = work / "findings.txt"
    for grader in (EVALS / name / "graders").glob("*.md"):
        fm = frontmatter(grader)
        if fm["type"] != "regex":
            continue
        text = trace if fm["target"] == "trace" else findings.read_text(encoding="utf-8")
        hits = len(re.findall(fm["pattern"], text, re.M))
        match = fm.get("match", "contains")
        if match == "not_contains":
            assert hits == 0, (name, grader.name)
        elif match.startswith("count:"):
            assert hits == int(match.split(":")[1]), (name, grader.name, hits)
        else:
            assert hits > 0, (name, grader.name, text)
    if name.startswith("task-error-"):
        assert run.returncode == 3
    if name == "task-trivial-then-judge":
        assert (work / "merged.py").read_text().count("<<<<<<< ours") == 1


def test_every_line_task_checks_the_receipt():
    """Each task that audits lines must check the receipt, a value only a real script run can produce
    (a stubbed script or a hand-written answer cannot guess it)."""
    for case in cases("task-"):
        if case.name.startswith("task-error-"):
            continue
        patterns = [frontmatter(g)["pattern"] for g in (case / "graders").glob("*.md") if frontmatter(g)["type"] == "regex"]
        receipts = [p for p in patterns if re.search(r"receipt=cl1-[0-9a-f]{12}$", p)]
        assert len(receipts) == 1 and receipts[0].startswith("summary: "), case.name
        body = (case / "prompt.md").read_text(encoding="utf-8")
        assert receipts[0].split("receipt=")[1] not in body, case.name
        assert "receipt" in body, case.name


def test_baseline_values_are_not_in_the_prompts():
    """The exact values the graders look for must not be spelled out in the prompts."""
    for case in cases("task-"):
        body = (case / "prompt.md").read_text(encoding="utf-8")
        for grader in (case / "graders").glob("*.md"):
            fm = frontmatter(grader)
            if fm["type"] == "regex" and fm.get("match", "contains") == "contains" and "base:" in fm["pattern"]:
                assert not re.search(fm["pattern"], body), (case.name, grader.name)
