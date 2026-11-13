"""AC-3: resolve-trivial applies only provable hunks, never touches the index."""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

import conflict_ledger as cl
from conftest import BASE, make_repo, scenario


def test_resolve_applies_trivial_hunks_and_keeps_real_conflict(conflict):
    before = conflict.stages()
    code, out, _ = conflict.run("resolve-trivial", "f.txt")
    assert code == 0
    assert "applied 3 hunks (H2, H3, H4); 1 remaining (H1)" in out
    assert "remaining_true_conflicts: 1" in out
    text = (conflict.root / "f.txt").read_text().splitlines()
    # Q-15: inside a repository the markers keep git's own labels (HEAD, the merged branch)
    assert text == ["line 1", "<<<<<<< HEAD", "line 2 OURS", "||||||| base", "line 2", "=======",
                    "line 2 THEIRS", ">>>>>>> feature", "line 3", "line 4", "line 6", "line 7 ", "line 8", "line 9",
                    "line 10", "added by theirs", "line 11", "line 12"]
    assert conflict.stages() == before            # the index is untouched


def test_dry_run_writes_nothing(conflict):
    path = conflict.root / "f.txt"
    original = path.read_bytes()
    code, out, _ = conflict.run("resolve-trivial", "--dry-run", "f.txt")
    assert code == 0 and "dry run: nothing was written" in out
    assert path.read_bytes() == original


def test_completed_result_passes_verify(conflict):
    conflict.run("resolve-trivial", "f.txt")
    path = conflict.root / "f.txt"
    lines = path.read_text().splitlines()
    start, end = lines.index("<<<<<<< HEAD"), lines.index(">>>>>>> feature")
    lines[start:end + 1] = ["line 2 OURS + THEIRS"]
    path.write_text("".join(x + "\n" for x in lines))
    code, out, _ = conflict.run("--json", "verify", "f.txt")
    assert code == 0 and json.loads(out)["errors"] == []


def test_unresolved_markers_fail_verify(conflict):
    conflict.run("resolve-trivial", "f.txt")
    code, out, _ = conflict.run("--json", "verify", "f.txt")
    assert code == 1 and [e["kind"] for e in json.loads(out)["errors"]] == ["conflict_marker_left"]


def test_whitespace_preference(conflict):
    conflict.run("resolve-trivial", "--whitespace", "theirs", "f.txt")
    assert "line 7   " in (conflict.root / "f.txt").read_text().splitlines()


def test_json_report(conflict):
    code, out, _ = conflict.run("--json", "resolve-trivial", "--dry-run")
    assert code == 0
    assert json.loads(out) == [{"path": "f.txt", "applied": ["H2", "H3", "H4"], "remaining": ["H1"], "written": False}]


def test_file_mode_writes_out_file_and_findings(tmp_path, run_cli, conflict):
    for name, data in (("b", "".join(x + "\n" for x in BASE)), ("o", "".join(x + "\n" for x in conflict.ours)),
                       ("t", "".join(x + "\n" for x in conflict.theirs))):
        (tmp_path / name).write_text(data)
    code, out, _ = run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t", "--out", "m.txt",
                           "--findings-file", "found.txt")
    assert code == 0
    found = (tmp_path / "found.txt").read_text().splitlines()
    assert found[:2] == ["applied: H2,H3,H4", "remaining_true_conflicts: 1"]
    assert re.fullmatch(r"summary: applied=3 remaining=1 receipt=cl1-[0-9a-f]{12}", found[2])
    assert "<<<<<<< ours" in (tmp_path / "m.txt").read_text()
    # 같은 입력으로 다시 돌리면 같은 영수증, 공백 처리 방식을 바꿔 본문이 달라지면 다른 영수증
    run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t", "--out", "m2.txt", "--findings-file", "again.txt")
    assert (tmp_path / "again.txt").read_text().splitlines()[2] == found[2]
    run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t", "--out", "m3.txt", "--whitespace", "theirs",
            "--findings-file", "other.txt")
    assert (tmp_path / "other.txt").read_text().splitlines()[2] != found[2]


def test_file_mode_prints_when_no_out(tmp_path, run_cli, conflict):
    for name, data in (("b", BASE), ("o", conflict.ours), ("t", conflict.theirs)):
        (tmp_path / name).write_text("".join(x + "\n" for x in data))
    code, out, _ = run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t")
    assert code == 0 and out.rstrip().endswith("line 12")


def test_crlf_files_stay_crlf(tmp_path, run_cli):
    (tmp_path / "b").write_bytes(b"a\r\nb\r\nc\r\n")
    (tmp_path / "o").write_bytes(b"a\r\nB\r\nc\r\n")
    (tmp_path / "t").write_bytes(b"a\r\nb\r\nc\r\nd\r\n")
    code, _, _ = run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t", "--out", "m")
    assert code == 0 and (tmp_path / "m").read_bytes() == b"a\r\nB\r\nc\r\nd\r\n"


def test_binary_cannot_be_resolved(tmp_path, run_cli):
    for name in ("b", "o", "t"):
        (tmp_path / name).write_bytes(b"\0\1" + name.encode())
    code, _, err = run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t")
    assert code == 3 and "only text files can be resolved (binary)" in err


def test_resolve_refuses_hand_edited_working_file(conflict):
    """A file without conflict markers is a hand resolution; it must not be overwritten (exit 3)."""
    conflict.write("f.txt", [*conflict.theirs, "user-note"])
    before = (conflict.root / "f.txt").read_text(encoding="utf-8")
    code, _, err = conflict.run("resolve-trivial", "f.txt")
    assert code == 3 and "no conflict markers left" in err and "--force" in err and "--out" in err
    assert (conflict.root / "f.txt").read_text(encoding="utf-8") == before
    code, _, _ = conflict.run("resolve-trivial", "f.txt", "--force")
    assert code == 0 and "<<<<<<<" in (conflict.root / "f.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- Q-10: partly resolved files

TWO_BLOCK_BASE = list("abcdefgh")


def two_block_conflict(tmp_path, capsys, style: str | None = None, files: tuple[str, ...] = ("f.txt",)):
    """base a..h; main changes b and g and appends a line, feature changes b and g differently: two conflict blocks
    plus one hunk only main made. With style, merge.conflictStyle is set in the repository before merging."""
    ours = ["a", "B-main", "c", "d", "e", "f", "G-main", "h", "main-add"]
    theirs = ["a", "B-feat", "c", "d", "e", "f", "G-feat", "h"]
    sb = make_repo(tmp_path / "repo", capsys, name=files[0], base=TWO_BLOCK_BASE, ours=ours, theirs=theirs, mode="none")
    for extra in files[1:]:                        # more files with the same conflict, one commit per side
        sb.git("checkout", "-q", "feature")
        sb.write(extra, theirs)
        sb.commit(f"theirs {extra}")
        sb.git("checkout", "-q", "main")
        sb.write(extra, ours)
        sb.commit(f"ours {extra}")
    if style:
        sb.git("config", "merge.conflictStyle", style)
    sb.git("merge", "--no-edit", "feature", check=False)
    return sb


def resolve_first_block(lines: list[str], replacement: str) -> list[str]:
    start = next(i for i, x in enumerate(lines) if x.startswith("<<<<<<<"))
    end = next(i for i, x in enumerate(lines) if x.startswith(">>>>>>>"))
    return lines[:start] + [replacement] + lines[end + 1:]


@pytest.mark.parametrize("style", [None, "merge", "diff3", "zdiff3"])
def test_untouched_conflict_is_resolved_in_every_conflict_style(tmp_path, capsys, style):
    """The file exactly as git wrote it (any merge.conflictStyle, any labels) is safe to rewrite."""
    sb = two_block_conflict(tmp_path, capsys, style)
    before = sb.stages()
    assert (sb.root / "f.txt").read_text().count("<<<<<<<") == 2
    code, out, err = sb.run("resolve-trivial", "f.txt")
    assert code == 0, err
    assert "applied 1 hunk (H3); 2 remaining (H1, H2)" in out           # Q-15: singular
    text = (sb.root / "f.txt").read_text().splitlines()
    assert text.count("<<<<<<< HEAD") == 2 and text.count(">>>>>>> feature") == 2 and text[-1] == "main-add"
    assert sb.stages() == before


def merge_file_knows_histogram() -> bool:
    r = subprocess.run(["git", "merge-file", "-p", "--diff-algorithm=histogram", os.devnull, os.devnull, os.devnull],
                       capture_output=True, check=False)
    return r.returncode == 0


def test_untouched_conflict_aligned_by_histogram_is_accepted(tmp_path, capsys):
    """git merge (merge-ort) aligns with the histogram diff, merge-file defaults to Myers. On this input (found by a
    random probe, 16 of 177 conflicts) the two place the conflict differently, so only the histogram retry matches."""
    if not merge_file_knows_histogram():
        pytest.skip("this git has no merge-file --diff-algorithm (added in newer git)")
    base, ours, theirs = list("b}{b}}"), ["b", "b", "}", "a", "c1", "b", "c2", "}"], ["z", "b", "}", "y2", "{", "b", "}", "}"]
    sb = make_repo(tmp_path / "repo", capsys, base=base, ours=ours, theirs=theirs)
    path = sb.root / "f.txt"
    item = {k: "".join(x + "\n" for x in v).encode() for k, v in (("base", base), ("ours", ours), ("theirs", theirs))}
    lines = cl.split_lines(path.read_bytes())
    myers = [cl.split_lines(cl.run_merge_file(item["base"], item["ours"], item["theirs"], f"--{s}").stdout) for s in cl.SIDES]
    assert myers != [cl.pick_side(lines, s) for s in cl.SIDES]      # Myers alone would refuse this untouched file
    assert cl.untouched_conflict(path.read_bytes(), item, "normalize")
    code, _, err = sb.run("resolve-trivial", "f.txt")
    assert code == 0, err


def test_resolve_refuses_a_partly_resolved_file(tmp_path, capsys):
    """QA Q-10 repro: one block resolved by hand plus an extra line; the rest still has markers.
    Rewriting it would drop 'B-main-and-feat' and 'user-note', which belong to neither side, so verify could
    never notice. resolve-trivial must refuse (exit 3) and write nothing."""
    sb = two_block_conflict(tmp_path, capsys)
    path = sb.root / "f.txt"
    sb.write("f.txt", resolve_first_block(path.read_text().splitlines(), "B-main-and-feat") + ["user-note"])
    before_bytes, before_stages = path.read_bytes(), sb.stages()
    code, out, err = sb.run("resolve-trivial", "f.txt")
    assert code == 3 and out == ""
    assert "differs from the unresolved conflict" in err and "nothing was written" in err
    assert "--force" in err and "--out" in err
    assert path.read_bytes() == before_bytes and sb.stages() == before_stages
    code, _, err = sb.run("--lang", "ko", "resolve-trivial", "f.txt")
    assert code == 3 and "세 버전의 미해결 충돌 상태와 다름" in err and path.read_bytes() == before_bytes
    # --out writes the machine resolution elsewhere and leaves the hand work alone
    code, out, _ = sb.run("resolve-trivial", "f.txt", "--out", str(tmp_path / "machine.txt"))
    assert code == 0 and f"written: {tmp_path / 'machine.txt'}" in out
    assert path.read_bytes() == before_bytes
    assert (tmp_path / "machine.txt").read_text().count("<<<<<<< HEAD") == 2
    # --force is the explicit way to discard the hand edits
    code, _, _ = sb.run("resolve-trivial", "f.txt", "--force")
    rewritten = path.read_text()
    assert code == 0 and "user-note" not in rewritten and rewritten.count("<<<<<<< HEAD") == 2


@pytest.mark.parametrize("edit", ["extra_line", "one_block_resolved", "outside_line_changed", "inside_block_changed",
                                  "line_removed"])
def test_any_hand_edit_blocks_the_rewrite(tmp_path, capsys, edit):
    sb = two_block_conflict(tmp_path, capsys, "diff3")
    path = sb.root / "f.txt"
    lines = path.read_text().splitlines()
    if edit == "extra_line":
        lines.append("user-note")
    elif edit == "one_block_resolved":
        lines = resolve_first_block(lines, "B-main")
    elif edit == "outside_line_changed":
        lines[lines.index("d")] = "d edited"
    elif edit == "inside_block_changed":
        lines[lines.index("G-main")] = "G-main tweaked"
    else:
        lines.remove("main-add")
    sb.write("f.txt", lines)
    before = path.read_bytes()
    code, _, err = sb.run("resolve-trivial", "f.txt")
    assert code == 3 and "refusing to overwrite" in err
    assert path.read_bytes() == before


def test_running_twice_is_allowed(conflict):
    """The second run finds exactly what it would write, so nothing can be lost."""
    assert conflict.run("resolve-trivial", "f.txt")[0] == 0
    first = (conflict.root / "f.txt").read_bytes()
    code, _, err = conflict.run("resolve-trivial", "f.txt")
    assert code == 0, err
    assert (conflict.root / "f.txt").read_bytes() == first


def test_refusal_writes_no_file_at_all(tmp_path, capsys):
    """With several files, one hand-edited file stops the whole run before anything is written."""
    sb = two_block_conflict(tmp_path, capsys, files=("f.txt", "g.txt"))
    untouched = (sb.root / "f.txt").read_bytes()
    sb.write("g.txt", (sb.root / "g.txt").read_text().splitlines() + ["user-note"])
    code, _, err = sb.run("resolve-trivial")
    assert code == 3 and "g.txt" in err and "f.txt" not in err
    assert (sb.root / "f.txt").read_bytes() == untouched
    code, _, err = sb.run("resolve-trivial", "--out", str(tmp_path / "x.txt"), "f.txt", "g.txt")
    assert code == 3 and "--out takes exactly one PATH" in err and not (tmp_path / "x.txt").exists()


def test_diff3_style_base_label_is_reused(tmp_path, capsys):
    """Q-15: with merge.conflictStyle=diff3 git also labels the base part; the rewrite keeps all three labels."""
    sb = two_block_conflict(tmp_path, capsys, "diff3")
    git_lines = (sb.root / "f.txt").read_text().splitlines()
    git_base = next(x for x in git_lines if x.startswith("|||||||"))
    assert git_base != "||||||| base"                    # git's own base label (not our default)
    assert sb.run("resolve-trivial", "f.txt")[0] == 0
    text = (sb.root / "f.txt").read_text().splitlines()
    assert text.count("<<<<<<< HEAD") == 2 and text.count(git_base) == 2 and text.count(">>>>>>> feature") == 2


def test_marker_labels_fall_back_when_git_wrote_none():
    assert cl.marker_labels(None) == {"ours": "ours", "base": "base", "theirs": "theirs"}
    assert cl.marker_labels(b"no markers\n") == {"ours": "ours", "base": "base", "theirs": "theirs"}
    assert cl.marker_labels(b"\0binary") == {"ours": "ours", "base": "base", "theirs": "theirs"}
    got = cl.marker_labels(b"x\n<<<<<<<<< HEAD\na\n=========\nb\n>>>>>>>>> topic/one\n<<<<<<< other\n")
    assert got == {"ours": "HEAD", "base": "base", "theirs": "topic/one"}      # first block only, longer markers too


def test_file_mode_markers_and_plural_unchanged(tmp_path, run_cli):
    """File mode keeps the 0.1.0 marker names; the English message says 'hunk' for exactly one."""
    base, ours, theirs = ["a", "b", "c"], ["a", "B1", "c", "o"], ["a", "B2", "c"]
    for name, lines in (("b", base), ("o", ours), ("t", theirs)):
        (tmp_path / name).write_text("".join(x + "\n" for x in lines))
    code, out, _ = run_cli("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t", "--out", "m.txt")
    assert code == 0 and "m.txt: applied 1 hunk (H2); 1 remaining (H1)" in out      # F-2: named after --out
    assert (tmp_path / "m.txt").read_text().splitlines() == ["a", "<<<<<<< ours", "B1", "||||||| base", "b", "=======",
                                                             "B2", ">>>>>>> theirs", "c", "o"]


def test_file_mode_out_file_with_hand_edits_is_kept(tmp_path, run_cli, conflict):
    """The same rule protects an existing --out file: a re-run must not wipe the block you already merged."""
    for name, data in (("b", BASE), ("o", conflict.ours), ("t", conflict.theirs)):
        (tmp_path / name).write_text("".join(x + "\n" for x in data))
    args = ("resolve-trivial", "--base", "b", "--ours", "o", "--theirs", "t", "--out", "m.txt")
    assert run_cli(*args)[0] == 0
    assert run_cli(*args)[0] == 0                                   # same content again: allowed
    merged = tmp_path / "m.txt"
    merged.write_text("".join(x + "\n" for x in resolve_first_block(merged.read_text().splitlines(), "line 2 both")))
    before = merged.read_bytes()
    code, _, err = run_cli(*args)
    assert code == 3 and "no conflict markers left" in err and merged.read_bytes() == before
    code, _, _ = run_cli(*args, "--force")
    assert code == 0 and "<<<<<<< ours" in merged.read_text()


def test_file_mode_report_names_the_out_file_without_name(tmp_path, run_cli):
    """QA fresh F-2: without --name the report names the --out file, not the ours input; --name still wins and the
    receipt does not depend on the name."""
    ours, theirs, _good = scenario()
    for name, lines in (("base.txt", BASE), ("ours.txt", ours), ("theirs.txt", theirs)):
        (tmp_path / name).write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    common = ("resolve-trivial", "--base", "base.txt", "--ours", "ours.txt", "--theirs", "theirs.txt")
    code, out, _ = run_cli("--lang", "en", *common, "--out", "merged.txt", "--findings-file", "a.txt")
    assert code == 0 and out.startswith("merged.txt: applied ") and "ours.txt" not in out
    code, out, _ = run_cli("--lang", "en", *common, "--out", "merged.txt", "--force", "--name", "app.txt",
                           "--findings-file", "b.txt")
    assert code == 0 and out.startswith("app.txt: applied ")
    summary = [(tmp_path / f).read_text(encoding="utf-8").splitlines()[-1] for f in ("a.txt", "b.txt")]
    assert summary[0] == summary[1] and "receipt=cl1-" in summary[0]
    code, out, _ = run_cli("--lang", "en", *common)                           # no --out: the ours file names it
    assert code == 0 and out.startswith("ours.txt: applied ")
