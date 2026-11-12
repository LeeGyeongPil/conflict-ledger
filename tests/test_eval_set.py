"""AC-8: evaluation set composition and grader values that match the script's own answers."""
from __future__ import annotations

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
    extra = ["--findings-file", "findings.txt"] if name != "task-error-binary" else []
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
    if name == "task-error-binary":
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
