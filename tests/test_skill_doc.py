"""AC-10 (3)(4): SKILL.md structure, and no evaluation answers baked into the skill.

(3) SKILL.md opens with a "First command" section whose first command is a script call (file-mode verify with
    --json, otherwise status --json), has an early-exit rule, leaves no sentence that lets the agent conclude about
    binary files without running the script, and names only subcommands and options the parser really has.
(4) The skill files (SKILL.md, references/, the script) contain no evaluation case name, fixture file name,
    grader receipt value or grader line value, and the SKILL.md / references text matches no grader regex.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import conflict_ledger as cl

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "conflict-ledger"
SKILL = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
REFERENCES = {p.name: p.read_text(encoding="utf-8") for p in sorted((SKILL_DIR / "references").glob("*.md"))}
SCRIPT = (SKILL_DIR / "scripts" / "conflict_ledger.py").read_text(encoding="utf-8")
EVALS = ROOT / "evals"
CMD = "python3 scripts/conflict_ledger.py"


def frontmatter_and_body(text: str) -> tuple[dict[str, str], str]:
    _, head, body = text.split("---\n", 2)
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(": ")
        fields[key] = value.strip()
    return fields, body


def section(body: str, title: str) -> str:
    """Text of a '## title' section up to the next level-2 heading."""
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    assert match, f"SKILL.md has no '## {title}' section"
    return match.group(1)


def code_spans(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


FIELDS, BODY = frontmatter_and_body(SKILL)


# --------------------------------------------------------------------------- (3) structure

def test_frontmatter_description_boundaries():
    assert FIELDS["name"] == "conflict-ledger"
    description = FIELDS["description"]
    assert len(description) <= 1024
    for phrase in ("merge, rebase or cherry-pick conflict", "fixing conflicts", '"ours"',
                   "merging branches (including parallel agent branches) and confirming nothing was silently overwritten"):
        assert phrase in description, phrase
    exclusions = description.split("Not for", 1)[1]
    for phrase in ("explaining git concepts", "configuring merge drivers or conflict style", "conflict-free diffs",
                   "undoing merges", "writing CI jobs"):
        assert phrase in exclusions, phrase


def test_first_command_section_comes_first_and_starts_with_the_script():
    headings = re.findall(r"^## (.+)$", BODY, re.M)
    assert headings[0] == "First command"
    first = section(BODY, "First command")
    spans = [s for s in code_spans(first) if s.startswith("python3 ")]
    assert spans[0].startswith(f"{CMD} verify --base ") and spans[0].endswith("--json")
    assert "--result" in spans[0]
    assert spans[-1] == f"{CMD} status --json"                    # the fallback when no files are given
    assert all(s.startswith(CMD) for s in spans)
    assert "Do not open, diff or inspect the files yourself first" in first


def test_early_exit_rule():
    first = section(BODY, "First command")
    stop = first.split("### Stop early", 1)[1]
    assert "exits with code 3" in stop and "stop" in stop
    assert '"operation": "none"' in stop and "`unmerged`" in stop and "`snapshots`" in stop
    assert "Never search the file system" in stop and "never create repositories" in stop


def test_one_call_ends_a_merge_request_without_a_repository():
    """A request to merge in a folder with no repository, or with unnamed branches, must not turn into a search:
    the script runs from the user's folder by full path (never from the skill's own folder, which can be a git
    clone), exit 3 is the answer even for a merge request, and unnamed branches are asked about, not guessed."""
    intro = BODY.split("## First command", 1)[0]
    assert "`python3 <skill-folder>/scripts/conflict_ledger.py <command>`" in intro
    assert "Never `cd` into the skill's folder" in intro
    stop = section(BODY, "First command").split("### Stop early", 1)[1]
    exit3 = next(line for line in stop.splitlines() if "exits with code 3" in line)
    assert "also when the user asked you to merge, rebase or cherry-pick" in exit3 and "`skill_folder`" in exit3
    none = next(line for line in stop.splitlines() if '"operation": "none"' in line)
    assert "named the branches or commits" in none and "do not guess" in none and "and stop" in none


def test_staged_resolution_and_fast_forward_have_a_next_step():
    """Q-1: an operation with nothing unmerged goes to verify without PATH; Q-2: a fast-forward is not audited."""
    stop = section(BODY, "First command").split("### Stop early", 1)[1]
    staged = next(line for line in stop.splitlines() if "operation other than `none`" in line)
    assert "no `unmerged` paths" in staged and "`verify --json`" in staged and "no PATH" in staged
    assert "fast-forwarded" in stop and "nothing to resolve" in stop
    assert "with a merge commit, audit it with `verify --commit HEAD --json`" in stop


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.;])\s+|\n", text) if s.strip()]


def test_no_shortcut_sentence_for_binary_files():
    """0.1.0 said binary files are 'reported by kind but not line-audited; tell the user they need a human
    decision', and a strong model answered from the file extension without running the script."""
    assert "reported by kind but not line-audited" not in SKILL
    assert "need a human decision" not in SKILL
    for s in sentences(BODY):
        if re.search(r"binary", s, re.I):
            assert re.search(r"script|run", s), s
    rules = section(BODY, "Rules")
    assert "Do not draw a conclusion from a file name, an extension or the bytes" in rules


def test_step_six_names_where_the_receipt_is():
    """F-3: the default text output of verify has no summary line, so step 6 points at --findings-file / --json."""
    workflow = section(BODY, "Workflow")
    step6 = next(line for line in workflow.splitlines() if line.startswith("6. "))
    assert "`--findings-file`" in step6 and "`receipt`" in step6 and "`--json`" in step6


def help_text(*args: str, capsys) -> str:
    assert cl.main([*args, "--help"]) == 0
    return capsys.readouterr().out


def test_every_command_and_option_named_exists(capsys):
    helps = {name: help_text(name, capsys=capsys) for name in cl.COMMANDS}
    top = help_text(capsys=capsys)

    def has_option(text: str, option: str) -> bool:
        return re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", text) is not None

    for name, text in [("SKILL.md", SKILL), *REFERENCES.items()]:
        for span in code_spans(text):
            words = span.split()
            command = None
            script_at = [i for i, w in enumerate(words) if w.endswith("conflict_ledger.py")]
            if script_at:                                     # "python3 scripts/conflict_ledger.py <command> ..."
                after = words[script_at[0] + 1:]
                command = after[0] if after and not after[0].startswith(("<", "-")) else None
            elif words and words[0] in cl.COMMANDS:          # "verify --commit SHA"
                command = words[0]
            if command is not None:
                assert command in cl.COMMANDS, (name, span)
            for option in re.findall(r"(?<![\w-])--[a-z][\w-]*", span):
                if command is not None:
                    assert has_option(helps[command], option) or has_option(top, option), (name, span, option)
                else:
                    assert any(has_option(h, option) for h in [top, *helps.values()]), (name, span, option)


# --------------------------------------------------------------------------- (4) no hardcoded evaluation answers

def eval_case_names() -> list[str]:
    return sorted(d.name for d in EVALS.iterdir() if d.is_dir() and d.name != "fixtures")


def fixture_file_names() -> list[str]:
    return sorted({f.name for f in (EVALS / "fixtures").rglob("*") if f.is_file()})


def grader_patterns() -> list[dict[str, str]]:
    out = []
    for grader in EVALS.glob("*/graders/*.md"):
        fields, _ = frontmatter_and_body(grader.read_text(encoding="utf-8"))
        if fields.get("type") == "regex":
            pattern = fields["pattern"]
            if pattern.startswith("'") and pattern.endswith("'"):
                pattern = pattern[1:-1].replace("''", "'")
            out.append({"pattern": pattern, "match": fields.get("match", "contains"), "file": str(grader)})
    return out


SKILL_FILES = {"SKILL.md": SKILL, **{f"references/{k}": v for k, v in REFERENCES.items()}, "conflict_ledger.py": SCRIPT}


def test_the_evaluation_set_is_there():
    assert len(eval_case_names()) >= 30 and len(fixture_file_names()) >= 20 and len(grader_patterns()) >= 15


@pytest.mark.parametrize("where", sorted(SKILL_FILES))
def test_no_case_or_fixture_names(where):
    text = SKILL_FILES[where]
    for name in eval_case_names() + fixture_file_names():
        assert not re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", text), (where, name)


@pytest.mark.parametrize("where", sorted(SKILL_FILES))
def test_no_receipt_or_line_values_from_the_graders(where):
    text = SKILL_FILES[where]
    assert not re.search(r"cl1-[0-9a-f]{12}", text), where           # no concrete receipt at all
    for grader in grader_patterns():
        for value in re.findall(r"base:\d+", grader["pattern"]):
            assert not re.search(rf"{value}(?!\d)", text), (where, value, grader["file"])


@pytest.mark.parametrize("where", ["SKILL.md", *[f"references/{k}" for k in REFERENCES]])
def test_skill_text_matches_no_grader(where):
    """Echoing the skill text must never satisfy a grader; only running the script can."""
    text = SKILL_FILES[where]
    for grader in grader_patterns():
        if grader["match"] == "contains":
            assert not re.search(grader["pattern"], text, re.M), (where, grader["file"])


def test_script_does_not_decide_by_file_name():
    assert ".suffix" not in SCRIPT and "endswith(" not in SCRIPT and "mimetypes" not in SCRIPT
    assert not re.search(r"""["']\.(png|jpe?g|gif|pdf|zip|bin|dat)["']""", SCRIPT, re.I)
