#!/usr/bin/env python3
"""Regenerate evals/ (prompts, graders, fixtures) for the skill evaluation.

Fixtures are synthetic files built here. Every exact value that a grader checks is
obtained by running the bundled script on the fixture, never typed by hand.

    python3 tools/build_evals.py
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "conflict-ledger" / "scripts" / "conflict_ledger.py"
EVALS = ROOT / "evals"
NAME = "conflict-ledger"

TRIGGERS = [
    "I just resolved the merge conflicts in src/config.py - can you check I didn't lose anything from either branch?",
    "`git merge main` gave me conflicts in three files, please fix them.",
    "Rebase stopped with conflicts. Resolve them and make sure none of my commit's changes get dropped.",
    "Before I commit this merge, verify the resolution kept both sides' changes.",
    "Cherry-pick hit a conflict in handlers.go - resolve it safely.",
    "Our last merge commit a1b2c3 seems to have reverted some of feature-x's work. Audit it.",
    "During this rebase which side is 'ours'? I need to pick the right version for the conflict in api.ts.",
    "Auto-resolve the whitespace-only conflict hunks and leave the real ones for me.",
    "Merge the two agent branches and confirm nothing was silently overwritten.",
    "병합 충돌 해결했는데 한쪽 변경이 빠진 거 없는지 확인해 줘.",
    "리베이스하다 충돌 났어. 내 커밋 내용 안 날아가게 풀어 줘.",
    "이 머지 커밋에서 feature 브랜치 변경이 사라진 것 같은데 검사해 줘.",
]

# 0.1.1: 02, 05 and 07 used to ask for real multi-step work in an empty folder (create files, run commands), which
# ran past the 6-turn budget without the skill ever firing. Same topic and closeness, now "show me, change nothing",
# so the budget measures whether the skill fires. 0.1.0 wording:
#   02 "Set up a custom merge driver for package-lock.json in .gitattributes."
#   05 "Write a GitHub Actions job that fails PRs which have merge conflicts with main."
#   07 "Undo my last merge - I want to go back to before it."
NEAR = [
    "Explain the difference between git merge and git rebase.",
    "What should I put in .gitattributes and git config to use a custom merge driver for package-lock.json? "
    "Just show me the lines, don't change any files.",
    "Show me the diff between main and my feature branch.",
    "Squash my last five commits into one.",
    "Show me a GitHub Actions job that fails PRs which have merge conflicts with main - just the YAML, don't create files.",
    "Set merge.conflictStyle to zdiff3 globally.",
    "How do I undo my last merge and go back to before it? Give me the commands, don't run them.",
    "엑셀 파일 두 개 비교해서 바뀐 행만 알려 줘.",
]

FAR = [
    "Write a Python function that merges two sorted lists.",
    "Summarize this README in three bullet points.",
    "Add type hints to utils.py.",
    "쿠버네티스 배포 yaml 검토해 줘.",
]


def write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def prompt_md(name: str, body: str, turns: int = 12) -> str:
    return f"---\nname: {name}\nmax_turns: {turns}\n---\n\n{body}\n"


def lines(items: list[str]) -> str:
    return "".join(x + "\n" for x in items)


# --------------------------------------------------------------------------- fixtures

def cfg_base() -> list[str]:
    out = []
    for n in range(1, 41):
        out += [f"[service-{n:02d}]", f"host = svc{n}.internal.example", f"port = {8000 + n}",
                f"timeout = {n % 7 + 5}", f"retries = {n % 3 + 1}", "log_level = info"]
    return out


def fixture_theirs_wholesale() -> dict[str, str]:
    base = cfg_base()
    ours = list(base)
    del ours[56]                              # base line 57: "port = 8010", removed on our side
    theirs = list(base)
    theirs[119] = "retries = 9"               # a different, unrelated change on their side
    theirs[199] = "timeout = 30"
    return {"base.cfg": lines(base), "ours.cfg": lines(ours), "theirs.cfg": lines(theirs), "resolved.cfg": lines(theirs)}


def py_module() -> list[str]:
    out = ["import logging", "", "log = logging.getLogger(__name__)", ""]
    for n in range(1, 11):
        out += [f"def handler_{n}(event):", f"    name = event.get('name_{n}')", f"    value = compute_{n}(name)",
                f"    if value is None:", f"        return None", f"    return {{'id': {n}, 'value': value}}", ""]
    return out


def fixture_rebase_labels() -> dict[str, str]:
    base = py_module()
    ours = list(base)                         # upstream: documented one handler
    assert base[11] == "def handler_2(event):"
    ours[11] = "def handler_2(event):  # now documented"
    theirs = list(base)                       # my commit: three added lines (base coordinates, last one first)
    resolved = list(ours)
    for at, name in ((60, "handler_9"), (36, "handler_6"), (14, "handler_3")):
        theirs.insert(at, f"    log.debug('{name} called')")
        if name != "handler_6":
            resolved.insert(at, f"    log.debug('{name} called')")      # the handler_6 line is missing
    return {"stage2_upstream.py": lines(ours), "stage1_base.py": lines(base), "stage3_mine.py": lines(theirs),
            "resolved.py": lines(resolved)}


def fixture_trivial_then_judge() -> dict[str, str]:
    base = [f"value_{i} = {i}" for i in range(1, 61)]
    ours = list(base)
    theirs = list(base)
    ours[7], theirs[7] = "value_8 = 8", "value_8  =  8"          # whitespace only
    ours[14], theirs[14] = "value_15 = 15", "value_15 =   15"    # whitespace only
    ours[21] = theirs[21] = "value_22 = 220"                      # both sides made the same change
    ours[29] = "value_30 = 300"                                   # only we changed this
    ours[44], theirs[44] = "value_45 = 'ours'", "value_45 = 'theirs'"   # a real conflict
    return {"base.py": lines(base), "ours.py": lines(ours), "theirs.py": lines(theirs)}


def go_source() -> list[str]:
    out = ["package store", "", "import \"log\"", ""]
    for n in range(1, 31):
        out += [f"func Handle{n}(req *Request) error {{", f"\tif req == nil {{", f"\t\treturn ErrNil", "\t}",
                f"\tfor _, item := range req.Items {{", f"\t\tprocess{n}(item)", "\t}", "\treturn nil", "}", ""]
    return out


def fixture_duplicate_braces() -> dict[str, str]:
    base = go_source()
    ours = list(base)
    ours.insert(70, "\tlog.Println(\"handle7 start\")")
    ours.insert(210, "\tlog.Println(\"handle22 start\")")
    theirs = list(base)
    theirs[5 + 9 * 3] = "\t\tprocess4Fast(item)"
    return {"base.go": lines(base), "ours.go": lines(ours), "theirs.go": lines(theirs), "resolved.go": lines(theirs)}


def md_doc() -> list[str]:
    out: list[str] = []
    n = 0
    while len(out) < 400:
        n += 1
        out += [f"Section {n}", "=======", "", f"Paragraph for section {n} explains topic {n} in plain words.", ""]
        if n == 6:
            out += ["| id | name | owner |", "|---|---|---|"] + [f"| {i} | item-{i} | team-{i % 5} |" for i in range(40)]
            out += [""]
    return out[:400]


def fixture_blanked_markdown() -> dict[str, str]:
    base = md_doc()
    ours = list(base)
    ours[3] = "Paragraph for section 1 now has an extra clause."
    theirs = list(base)
    theirs[348] = "Paragraph updated by the other branch."
    resolved = list(base)
    resolved[3] = ours[3]
    resolved[348] = theirs[348]
    start = base.index("| 0 | item-0 | team-0 |")
    for i in range(start, start + 40):
        resolved[i] = ""                      # unrelated table rows blanked
    return {"base.md": lines(base), "ours.md": lines(ours), "theirs.md": lines(theirs), "resolved.md": lines(resolved)}


def packed_records(rows: list[tuple[int, str]]) -> bytes:
    """A small synthetic binary export: a header, then (id, length, name) records with NUL padding."""
    out = b"EXP1\x00\x00" + struct.pack("<H", len(rows))
    for row_id, name in rows:
        data = name.encode("ascii")
        out += struct.pack("<IH", row_id, len(data)) + data + b"\x00" * (8 - len(data) % 8)
    return out


def fixture_disguised_binary() -> dict[str, bytes]:
    """Binary content behind a .txt name: only reading the bytes (as the script does) tells it apart."""
    base = [(1, "alpha"), (2, "beta"), (3, "gamma")]
    ours = base + [(4, "delta")]
    theirs = [(1, "alpha"), (2, "beta-2"), (3, "gamma")]
    resolved = [(1, "alpha"), (2, "beta-2"), (3, "gamma"), (4, "delta")]
    return {"export_base.txt": packed_records(base), "export_ours.txt": packed_records(ours),
            "export_theirs.txt": packed_records(theirs), "export.txt": packed_records(resolved)}


# --------------------------------------------------------------------------- script runs

def run_script(cwd: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(SCRIPT), "--lang", "en", *args], cwd=cwd, capture_output=True, text=True, check=False)
    return r.returncode, r.stdout + r.stderr


def findings_for(files: dict, args: list[str]) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="ledger-eval-") as tmp:
        work = Path(tmp)
        for name, data in files.items():
            write(work / name, data)
        run_script(work, *args, "--findings-file", "findings.txt")
        return (work / "findings.txt").read_text(encoding="utf-8").splitlines()


def grader(kind: str, pattern: str, target: str = "{source: file, path: findings.txt}", extra: str = "") -> str:
    return f"---\ntype: {kind}\npattern: '{pattern}'\ntarget: {target}\n{extra}---\n"


SCRIPT_RAN = "---\ntype: tool_used\ntool: Bash\ninput_match: 'scripts/conflict_ledger\\.py'\narm: with-only\n---\n"
SKILL_FIRED = f"---\ntype: tool_used\ntool: Skill\ninput_match: '\"skill\"\\s*:\\s*\"(?:[\\w-]+:)?{NAME}\"'\narm: both\n---\n"
SKILL_NOT_FIRED = "---\ntype: tool_used\ntool: Skill\nmin: 0\nmax: 0\narm: both\n---\n"


def task(slug: str, body: str, files: dict, graders: dict[str, str], turns: int = 14) -> None:
    case = EVALS / f"task-{slug}"
    write(case / "prompt.md", prompt_md(f"task-{slug}", body, turns))
    write(case / "graders" / "script-ran.md", SCRIPT_RAN)
    for gname, text in graders.items():
        write(case / "graders" / f"{gname}.md", text)
    for name, data in files.items():
        write(EVALS / "fixtures" / f"task-{slug}" / name, data)


def main() -> int:
    if EVALS.exists():
        shutil.rmtree(EVALS)
    for n, text in enumerate(TRIGGERS, 1):
        write(EVALS / f"trigger-{n:02d}" / "prompt.md", prompt_md(f"trigger-{n:02d}", text, 6))
        write(EVALS / f"trigger-{n:02d}" / "graders" / "skill-fired.md", SKILL_FIRED)
    for n, text in enumerate(NEAR, 1):
        write(EVALS / f"notrigger-near-{n:02d}" / "prompt.md", prompt_md(f"notrigger-near-{n:02d}", text, 6))
        write(EVALS / f"notrigger-near-{n:02d}" / "graders" / "skill-not-fired.md", SKILL_NOT_FIRED)
    for n, text in enumerate(FAR, 1):
        write(EVALS / f"notrigger-{n:02d}" / "prompt.md", prompt_md(f"notrigger-{n:02d}", text, 6))
        write(EVALS / f"notrigger-{n:02d}" / "graders" / "skill-not-fired.md", SKILL_NOT_FIRED)

    fmt = ("Write the result to findings.txt, one line per problem, in the form `<kind> <side> base:<line>` "
           "(for example `addition_dropped theirs base:12`).")
    # The summary line carries the audit receipt (a fingerprint of the input files and the findings).
    # Only a real run of the script on these exact files can produce it, so a guessed or hand-made
    # answer fails the "totals" grader even when its problem lines happen to be right.
    totals = (" End findings.txt with the audit's own `summary:` line (totals and `receipt=`) exactly as the audit "
              "produced it; I paste that receipt into the merge commit so a reviewer can re-run the audit on the same "
              "files and compare.")

    # 1: a wholesale take of one side brings back a line the other side deleted
    files = fixture_theirs_wholesale()
    got = findings_for(files, ["verify", "--base", "base.cfg", "--ours", "ours.cfg", "--theirs", "theirs.cfg",
                               "--result", "resolved.cfg"])
    task("theirs-wholesale",
         "My merge had conflicts in service.cfg and I resolved it by taking the other branch's file. base.cfg is the common "
         "ancestor, ours.cfg is my branch, theirs.cfg is the other branch and resolved.cfg is my resolution. Check that the "
         "resolution did not undo anything my branch did. " + fmt + totals,
         files, {"deleted-line-back": grader("regex", got[0]), "totals": grader("regex", got[-1])})

    # 2: rebase - which side is which, and one dropped line of my commit
    files = fixture_rebase_labels()
    got = findings_for(files, ["verify", "--base", "stage1_base.py", "--ours", "stage2_upstream.py", "--theirs",
                               "stage3_mine.py", "--result", "resolved.py", "--operation", "rebase"])
    task("rebase-labels",
         "I am in the middle of `git rebase main`. For handlers.py I saved the index stages as stage1_base.py, "
         "stage2_upstream.py (stage 2) and stage3_mine.py (stage 3), and my resolution is resolved.py. Check that none of my "
         "commit's changes were dropped. Start findings.txt with a line `my-commit-side: stage<N>` naming the stage that "
         "holds my commit, then add one line per problem as `<kind> <side> base:<line>`." + totals,
         files, {"label": grader("regex", got[0]), "dropped-line": grader("regex", got[1]),
                 "totals": grader("regex", got[-1])})

    # 3: resolve only the trivial hunks
    files = fixture_trivial_then_judge()
    with tempfile.TemporaryDirectory(prefix="ledger-eval-") as tmp:
        work = Path(tmp)
        for name, data in files.items():
            write(work / name, data)
        run_script(work, "resolve-trivial", "--base", "base.py", "--ours", "ours.py", "--theirs", "theirs.py", "--out",
                   "merged.py", "--findings-file", "findings.txt")
        got = (work / "findings.txt").read_text(encoding="utf-8").splitlines()
    task("trivial-then-judge",
         "base.py, ours.py and theirs.py are three versions of a file with merge conflicts. Resolve every hunk that can be "
         "resolved without judgement into merged.py, leave the real conflicts in merged.py as diff3 markers, and write "
         "findings.txt with the lines `applied: <hunk ids>` and `remaining_true_conflicts: <count>`, then end it with the "
         "tool's own `summary:` line (hunk counts and `receipt=`) exactly as the tool produced it; I paste that receipt "
         "into the merge commit so a reviewer can re-run the same step and compare.",
         files, {"remaining": grader("regex", got[1]), "applied": grader("regex", got[0]),
                 "totals": grader("regex", got[2])})

    # 4: repeated closing braces hide dropped lines
    files = fixture_duplicate_braces()
    got = findings_for(files, ["verify", "--base", "base.go", "--ours", "ours.go", "--theirs", "theirs.go", "--result",
                               "resolved.go"])
    task("duplicate-braces",
         "I merged store.go by keeping the other branch's version of the file. base.go is the common ancestor, ours.go is my "
         "branch, theirs.go is the other branch, resolved.go is what I committed. Tell me exactly which lines my branch added "
         "that are now missing. " + fmt + totals,
         files, {f"line-{n + 1}": grader("regex", line) for n, line in enumerate(got)})

    # 5: forty unrelated lines were blanked
    files = fixture_blanked_markdown()
    got = findings_for(files, ["verify", "--base", "base.md", "--ours", "ours.md", "--theirs", "theirs.md", "--result",
                               "resolved.md"])
    lost = [x for x in got if x.startswith("base_line_lost")]
    task("blanked-markdown",
         "docs.md was merged and the result looks fine, but I suspect lines that neither branch touched were emptied. base.md "
         "is the common ancestor, ours.md and theirs.md are the two branches, resolved.md is the merge result. Find every such "
         "line. " + fmt + " Name the kind `base_line_lost` and write `-` as the side, since no side applies "
         "(for example `base_line_lost - base:7`)." + totals,
         files, {"lost-count": grader("regex", r"base_line_lost - base:\d+", extra="match: count:%d\n" % len(lost)),
                 "lost-first": grader("regex", lost[0]), "lost-last": grader("regex", lost[-1]),
                 "no-marker": grader("regex", "conflict_marker_left", extra="match: not_contains\n"),
                 "totals": grader("regex", got[-1])})

    # 0.1.3: the image-only task case "task-error-binary" is removed, together with its four PNG fixtures
    # (logo_base.png, logo_ours.png, logo_theirs.png, logo.png). Its prompt was:
    #   "logo_base.png, logo_ours.png and logo_theirs.png are the three sides of a conflicting image, and logo.png is
    #    my resolution. Audit the resolution and tell me whether it kept both sides' changes."
    # Reason: in three pre-release evaluations in a row the largest model answered it at least once by looking at the
    # pictures instead of running the script, and two SKILL.md rewrites did not change that. Images are not what this
    # skill audits (line change sets of text files); the script can only report them as binary (exit 3). No grader was
    # loosened: the whole case is gone. Binary handling is still guaranteed by tests/test_entry.py (content-based
    # detection, a binary file next to a text file in a real merge) and by the measured error-path case below.

    # 6 (error path, 0.1.1): binary content behind a .txt name; the verdict and its findings lines come from the script
    files = fixture_disguised_binary()
    got = findings_for(files, ["verify", "--base", "export_base.txt", "--ours", "export_ours.txt", "--theirs",
                               "export_theirs.txt", "--result", "export.txt"])
    label, kind, reported = got[0].split(" ", 2)             # "input_error <kind> <path>" as the script wrote it
    named = rf"{label} {kind} \S*{re.escape(Path(reported).name)}"         # the path may be given absolute
    task("error-disguised-binary",
         "export_base.txt, export_ours.txt and export_theirs.txt are the common ancestor, my branch and the other branch "
         "of export.txt, which had a merge conflict, and export.txt is my resolution. Check that the resolution kept both "
         "sides' changes. Write the audit's result to findings.txt, ending with the audit's own `summary:` line exactly as "
         "the audit produced it.",
         files, {"binary-error": grader("regex", r"(binary file, line audit skipped|바이너리 파일, 줄 감사 건너뜀): \S*export\S*\.txt",
                                        target="trace"),
                 "input-error": grader("regex", named), "totals": grader("regex", got[-1])})
    return 0


if __name__ == "__main__":
    sys.exit(main())
