"""AC-4: boundaries and error paths."""
from __future__ import annotations

import json
import time

import pytest

import conflict_ledger as cl
from conftest import BASE, Sandbox, make_repo, scenario


def put(tmp_path, **files):
    for name, data in files.items():
        p = tmp_path / name
        if isinstance(data, list):
            data = "".join(x + "\n" for x in data)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def file_verify(run_cli, *extra):
    return run_cli("--json", "verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r", *extra)


def test_add_add_and_modify_delete_are_reported_by_kind(tmp_path, capsys):
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("keep.txt", ["x"])
    sb.write("gone.txt", ["1", "2", "3"])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("new.txt", ["theirs"])
    (sb.root / "gone.txt").unlink()
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("new.txt", ["ours"])
    sb.write("gone.txt", ["1", "2 changed", "3"])
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    _, out, _ = sb.run("--json", "scan")
    kinds = {f["path"]: f["kind"] for f in json.loads(out)["files"]}
    assert kinds == {"gone.txt": "modify_delete", "new.txt": "add_add"}
    sb.write("new.txt", ["both"])
    code, out, _ = sb.run("--json", "verify", "new.txt")
    rep = json.loads(out)
    assert code == 0 and rep["files"][0]["skipped"] == "add_add"
    assert rep["info"][0]["kind"] == "file_skipped"


def test_binary_verify_is_an_input_problem(tmp_path, capsys):
    sb = make_repo(tmp_path / "r", capsys, name="logo.bin", base=b"\0base", ours=b"\0ours", theirs=b"\0theirs")
    code, out, err = sb.run("verify", "logo.bin")
    assert code == 3 and out == "" and "binary file, line audit skipped: logo.bin" in err
    _, out, _ = sb.run("--json", "scan")
    assert json.loads(out)["files"][0]["kind"] == "binary"
    _, text, _ = sb.run("scan")
    assert "line classification skipped for binary files" in text


def test_non_utf8_counts_as_binary():
    assert cl.is_binary(b"caf\xe9") and cl.is_binary(b"a\0b") and not cl.is_binary("café".encode())


def test_crlf_and_bom_are_normalized(tmp_path, run_cli):
    put(tmp_path, b=b"a\nb\nc\n", o=b"a\nB\nc\n", t=b"a\nb\nc\nd\n", r=b"\xef\xbb\xbfa\r\nB\r\nc\r\nd\r\n")
    code, out, _ = file_verify(run_cli)
    assert code == 0 and json.loads(out)["errors"] == []
    code, out, _ = file_verify(run_cli, "--eol", "keep")
    assert code == 1                              # exact comparison sees every line as different


def test_setext_underline_is_not_a_conflict_marker(tmp_path, run_cli):
    doc = ["Title", "=======", "", "text", "", "Other", "=======", "more"]
    put(tmp_path, b=doc, o=doc + ["ours tail"], t=["intro"] + doc, r=["intro"] + doc + ["ours tail"])
    code, out, _ = file_verify(run_cli)
    assert code == 0, out
    assert json.loads(out)["errors"] == []


def test_real_marker_pair_is_found_even_with_setext(tmp_path, run_cli):
    doc = ["Title", "=======", "text"]
    put(tmp_path, b=doc, o=doc, t=doc, r=["<<<<<<< HEAD", "x", "=======", "y", ">>>>>>> main"] + doc)
    code, out, _ = file_verify(run_cli)
    assert code == 1 and [e["kind"] for e in json.loads(out)["errors"]] == ["conflict_marker_left"]


def braces_file():
    out = []
    for n in range(1, 41):
        out += [f"func f{n}() {{", f"\tif x{n} {{", f"\t\tdo{n}()", "\t}", "}", ""]
    return out


def test_repeated_braces_audited_by_position(tmp_path, run_cli):
    base = braces_file()                           # 240 lines, 80 closing braces
    assert len(base) >= 240
    assert base[196] == "}"
    ours = list(base)
    del ours[196]                                  # ours removes the closing brace of f33 (base line 197)
    ours.insert(30, "\tlogOurs()")
    ours.insert(121, "\tlogOurs2()")
    theirs = list(base)
    theirs[2] = "\t\tdoOne()"
    put(tmp_path, b=base, o=ours, t=theirs)
    # correct merge = ours changes plus theirs change
    correct = list(ours)
    correct[2] = "\t\tdoOne()"
    put(tmp_path, r=correct)
    code, out, _ = file_verify(run_cli)
    assert code == 0, out
    # taking theirs wholesale drops two additions and brings back the deleted brace
    put(tmp_path, r=theirs)
    code, out, _ = file_verify(run_cli)
    errs = json.loads(out)["errors"]
    assert code == 1
    assert sorted((e["kind"], e["side"], e["text"]) for e in errs if e["kind"] == "addition_dropped") == [
        ("addition_dropped", "ours", "\tlogOurs()"), ("addition_dropped", "ours", "\tlogOurs2()")]
    reverted = [e for e in errs if e["kind"] == "deletion_reverted"]
    assert len(reverted) == 1 and reverted[0]["text"] == "}" and reverted[0]["base_line"] == 197


def test_large_file_is_fast(tmp_path, run_cli):
    base = [f"row {i}" for i in range(5000)]
    ours = list(base)
    ours[100] = "row 100 ours"
    theirs = list(base)
    theirs.insert(4000, "row new theirs")
    put(tmp_path, b=base, o=ours, t=theirs, r=ours[:4000] + ["row new theirs"] + ours[4000:])
    started = time.monotonic()
    code, out, _ = file_verify(run_cli)
    assert code == 0 and time.monotonic() - started < 5, out


def test_verify_after_git_add_uses_snapshot(conflict):
    conflict.run("scan")
    conflict.write("f.txt", conflict.good)
    conflict.git("add", "f.txt")
    assert conflict.stages() == ""                 # stages are gone after git add
    code, out, _ = conflict.run("--json", "verify", "f.txt")
    rep = json.loads(out)
    assert code == 0 and rep["files"][0]["source"] == "snapshot"
    conflict.write("f.txt", conflict.theirs)
    conflict.git("add", "f.txt")
    code, out, _ = conflict.run("--json", "verify")          # default paths come from the snapshots
    assert code == 1 and json.loads(out)["errors"][0]["kind"] == "deletion_reverted"


def test_merge_head_fallback_without_snapshot(conflict):
    conflict.write("f.txt", conflict.ours)
    conflict.git("add", "f.txt")
    code, out, _ = conflict.run("--json", "verify", "f.txt")
    rep = json.loads(out)
    assert code == 1 and rep["files"][0]["source"] == "heads"
    assert rep["errors"][0]["kind"] == "addition_dropped" and rep["errors"][0]["text"] == "added by theirs"


def test_rebase_and_cherry_pick_fallbacks(tmp_path, capsys):
    ours, theirs, good = scenario()
    for mode, label in (("rebase", "my-commit"), ("cherry-pick", "CHERRY_PICK_HEAD")):
        sb = make_repo(tmp_path / mode, capsys, ours=ours, theirs=theirs, mode=mode)
        sb.write("f.txt", theirs)                   # keep only the replayed commit's version
        sb.git("add", "f.txt")
        code, out, _ = sb.run("--json", "verify", "f.txt")
        rep = json.loads(out)
        assert code == 1 and rep["files"][0]["source"] == "heads"
        assert rep["errors"][0]["kind"] == "deletion_reverted" and rep["errors"][0]["side_label"] == "HEAD" \
            or rep["errors"][0]["side_label"] == "upstream"
        assert rep["files"][0]["labels"]["theirs"] == label


def test_stale_snapshot_is_ignored_after_commit(conflict):
    conflict.run("scan")
    conflict.write("f.txt", conflict.good)
    conflict.git("add", "f.txt")
    conflict.git("commit", "-q", "--no-edit")
    code, _, err = conflict.run("verify", "f.txt")
    assert code == 3 and "no conflict in progress and no snapshot for f.txt" in err


def test_verify_commit_audits_two_parent_merge(conflict):
    conflict.write("f.txt", conflict.theirs)
    conflict.git("add", "f.txt")
    conflict.git("commit", "-q", "--no-edit")
    code, out, _ = conflict.run("--json", "verify", "--commit", "HEAD")
    rep = json.loads(out)
    assert code == 1
    assert [(e["kind"], e["side_label"], e["base_line"]) for e in rep["errors"]] == [("deletion_reverted", "parent1", 5)]
    _, text, _ = conflict.run("verify", "--commit", "HEAD")
    assert "ours = first parent" in text


def test_verify_commit_passes_for_good_merge(conflict):
    conflict.write("f.txt", conflict.good)
    conflict.git("add", "f.txt")
    conflict.git("commit", "-q", "--no-edit")
    code, out, _ = conflict.run("--json", "verify", "--commit", "HEAD", "f.txt")
    assert code == 0 and json.loads(out)["errors"] == []


def test_verify_commit_skips_binary_and_single_parent(tmp_path, capsys):
    ours, theirs, good = scenario()
    sb = make_repo(tmp_path / "r", capsys, ours=ours, theirs=theirs)
    sb.write("f.txt", good)
    sb.write("img.bin", b"\0\1\2")
    sb.git("add", "-A")
    sb.git("commit", "-q", "--no-edit")
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    assert code == 0                                  # the binary file added by the merge is skipped, not an error
    code, _, err = sb.run("verify", "--commit", "HEAD~1")
    assert code == 3 and "is not a two-parent merge" in err
    code, _, err = sb.run("verify", "--commit", "nonexistent")
    assert code == 3 and "cannot resolve commit nonexistent" in err


def test_not_a_git_repository(tmp_path, run_cli):
    empty = tmp_path / "empty"
    empty.mkdir()
    for command in ("status", "scan", "verify"):
        code, _, err = run_cli("--repo", str(empty), command)
        assert code == 3 and f"not a git repository: {empty}" in err


def test_input_errors_in_file_mode(tmp_path, run_cli):
    put(tmp_path, b="a\n", o="a\n", t="a\n")
    code, _, err = run_cli("verify", "--base", "b", "--ours", "o")
    assert code == 3 and "--base/--ours/--theirs must be given together" in err
    code, _, err = run_cli("verify", "--base", "b", "--ours", "o", "--theirs", "t")
    assert code == 3 and "needs --result FILE" in err
    code, _, err = run_cli("verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "missing.txt")
    assert code == 3 and "file not found: missing.txt" in err
    code, _, err = run_cli("scan", "--base", "b", "--ours", "o")
    assert code == 3 and "must be given together" in err


def test_path_outside_repository(conflict, tmp_path):
    code, _, err = conflict.run("verify", "/etc/hosts")
    assert code == 3 and "path is outside the repository" in err


def test_relative_path_is_found_from_repo_root(conflict, tmp_path):
    code, out, _ = conflict.run("--json", "scan", "f.txt")        # cwd is not the repository
    assert code == 0 and json.loads(out)["files"][0]["path"] == "f.txt"


def test_usage_errors_exit_with_input_code(run_cli):
    code, _, err = run_cli("verify", "--no-such-flag")
    assert code == 3 and "unrecognized arguments" in err


def test_file_mode_operation_labels(tmp_path, run_cli):
    put(tmp_path, b=["a", "b"], o=["a", "b", "ours"], t=["a", "b", "theirs"], r=["a", "b", "ours"])
    code, out, _ = run_cli("verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r",
                           "--operation", "rebase", "--findings-file", "f.txt")
    assert code == 2 and "my-commit-side: stage3" in out
    assert (tmp_path / "f.txt").read_text().splitlines()[0] == "my-commit-side: stage3"


def test_multiple_files_findings_prefix_path():
    def item(path):
        return {"path": path, "base": b"a\nb\n", "ours": b"a\nb\nours\n", "theirs": b"a\nb\n", "result": b"a\nb\n",
                "op": "none", "source": "files", "notes": [], "labels": cl.labels_for("none")}
    report = cl.build_verify_report([item("one.txt"), item("two.txt")], "normalize", False)
    assert cl.findings_lines(report) == ["one.txt addition_dropped ours base:3", "two.txt addition_dropped ours base:3",
                                         f"summary: errors=2 warnings=0 exit=1 receipt={report['receipt']}"]
    assert report["receipt"].startswith("cl1-")
    assert [e["id"] for e in report["errors"]] == ["E1", "E2"]


@pytest.mark.parametrize("data,expected", [(b"a\nb", ["a", "b"]), (b"a\n\n", ["a", ""]), (b"", [])])
def test_split_lines(data, expected):
    assert cl.split_lines(data) == expected


# --- resolved and staged, not committed yet, scan never ran (the "check before I commit" case) ---

SIX = ["a", "b", "c", "d", "e", "f"]
MAIN_SIX = ["a", "B-main", "c", "d", "E-main", "f", "main-only"]
FEAT_SIX = ["a", "B-feat", "c", "d", "E-feat", "f"]


def staged_without_scan(tmp_path, capsys, mode="merge"):
    """Both sides change lines b and e, ours also adds a line; the conflict is resolved by taking the
    other side's whole file and staged with git add, without ever running scan (no snapshot)."""
    sb = make_repo(tmp_path / mode, capsys, base=SIX, ours=MAIN_SIX, theirs=FEAT_SIX, mode=mode)
    assert sb.stages() != ""                                   # git really stopped on a conflict
    sb.write("f.txt", FEAT_SIX)
    sb.git("add", "f.txt")
    assert sb.stages() == ""                                   # no unmerged stages left, nothing committed
    return sb


def test_verify_without_path_audits_a_staged_merge(tmp_path, capsys):
    sb = staged_without_scan(tmp_path, capsys)
    code, out, err = sb.run("--json", "verify")
    rep = json.loads(out)
    assert code == 1, err
    assert [f["path"] for f in rep["files"]] == ["f.txt"] and rep["files"][0]["source"] == "heads"
    assert [(e["kind"], e["side"], e["base_line"], e["text"]) for e in rep["errors"]] == \
        [("addition_dropped", "ours", 7, "main-only")]
    # same verdict as naming the path by hand, and as the text output
    assert sb.run("--json", "verify", "f.txt")[1] == out
    code, text, _ = sb.run("verify")
    assert code == 1 and "verify f.txt (source: heads)" in text and 'addition_dropped ours base:7 "main-only"' in text
    assert "no conflict in progress" not in text


def test_scan_and_status_without_path_find_a_staged_merge(tmp_path, capsys):
    sb = staged_without_scan(tmp_path, capsys)
    code, out, err = sb.run("--json", "scan")
    assert code == 0, err
    assert [f["path"] for f in json.loads(out)["files"]] == ["f.txt"]
    code, out, _ = sb.run("--json", "status")
    st = json.loads(out)
    assert code == 0 and st["operation"] == "merge" and st["unmerged"] == []
    assert set(st) == {"operation", "labels", "my_commit_side", "unmerged", "unmerged_kinds", "snapshots"}  # no new keys
    code, text, _ = sb.run("status")
    assert "no unmerged files left; verify without PATH audits the files both sides changed: f.txt" in text
    _, text, _ = sb.run("--lang", "ko", "status")
    assert "병합되지 않은 파일이 남지 않음" in text and "f.txt" in text


def test_staged_rebase_and_cherry_pick_are_found_without_path(tmp_path, capsys):
    for mode in ("rebase", "cherry-pick"):
        sb = staged_without_scan(tmp_path, capsys, mode=mode)
        code, out, err = sb.run("--json", "verify")
        rep = json.loads(out)
        assert code == 1, (mode, err)
        assert [f["path"] for f in rep["files"]] == ["f.txt"] and rep["files"][0]["source"] == "heads"
        assert "addition_dropped" in {e["kind"] for e in rep["errors"]}
        code, out, _ = sb.run("--json", "scan")
        assert code == 0 and [f["path"] for f in json.loads(out)["files"]] == ["f.txt"]


def test_verify_without_path_covers_staged_and_unmerged_files_together(tmp_path, capsys):
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("one.txt", SIX)
    sb.write("two.txt", SIX)
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("one.txt", FEAT_SIX)
    sb.write("two.txt", FEAT_SIX)
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("one.txt", MAIN_SIX)
    sb.write("two.txt", MAIN_SIX)
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    sb.write("one.txt", FEAT_SIX)                              # one.txt staged without scan, two.txt still open
    sb.git("add", "one.txt")
    code, out, _ = sb.run("--json", "verify")
    rep = json.loads(out)
    assert code == 1 and [f["path"] for f in rep["files"]] == ["one.txt", "two.txt"]
    kinds = {(e["path"], e["kind"]) for e in rep["errors"]}
    assert ("one.txt", "addition_dropped") in kinds and ("two.txt", "conflict_marker_left") in kinds


def test_merge_in_progress_with_nothing_changed_on_both_sides(tmp_path, capsys):
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("ours.txt", ["x"])
    sb.write("theirs.txt", ["y"])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("theirs.txt", ["y", "theirs"])
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("ours.txt", ["x", "ours"])
    sb.commit("ours")
    sb.git("merge", "--no-commit", "--no-ff", "feature")      # clean merge, stopped before the commit
    for command in ("verify", "scan"):
        code, out, err = sb.run("--json", command)
        problem = json.loads(out)["input_error"]
        assert code == 3 and problem["kind"] == "nothing_to_audit", command
        assert "no conflict in progress" not in err and "merge in progress, but nothing to audit" in err
    _, _, err = sb.run("--lang", "ko", "verify")
    assert "merge 작업이 진행 중" in err
    code, out, _ = sb.run("--json", "verify", "theirs.txt")    # a named path is still audited from the heads
    assert code == 0 and json.loads(out)["files"][0]["source"] == "heads"


def test_modify_delete_resolved_by_git_rm_does_not_stop_the_default_audit(tmp_path, capsys):
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("gone.txt", SIX)
    sb.write("f.txt", SIX)
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    (sb.root / "gone.txt").unlink()
    sb.write("f.txt", FEAT_SIX)
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("gone.txt", ["a", "b changed", "c", "d", "e", "f"])
    sb.write("f.txt", MAIN_SIX)
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    sb.git("rm", "-q", "gone.txt")                             # take the deletion
    sb.write("f.txt", FEAT_SIX)
    sb.git("add", "f.txt")
    code, out, err = sb.run("--json", "verify")
    rep = json.loads(out)
    assert code == 1, err
    assert {f["path"]: f["skipped"] for f in rep["files"]} == {"f.txt": None, "gone.txt": "modify_delete"}
    code, out, err = sb.run("--json", "verify", "gone.txt")   # naming the deleted path: same verdict, not "missing"
    rep = json.loads(out)
    assert code == 0, err
    assert rep["files"][0]["skipped"] == "modify_delete" and rep["info"][0]["kind"] == "file_skipped"
    code, _, err = sb.run("verify", "never-existed.txt")      # a path no version has is still an input problem
    assert code == 3 and "file not found" in err
    sb.git("commit", "-q", "--no-edit")
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    assert code == 1 and {f["path"]: f["skipped"] for f in json.loads(out)["files"]}["gone.txt"] == "modify_delete"


def test_scan_cross_check_and_large_file_counts(tmp_path, run_cli):
    put(tmp_path, b=SIX, o=["a", "B-main", "c", "d", "e", "f"], t=["a", "B-feat", "c", "d", "e", "f"])
    code, out, _ = run_cli("scan", "--base", "b", "--ours", "o", "--theirs", "t")
    assert code == 0 and "git merge-file reports 1 conflict for comparison" in out
    assert any(x.endswith(": text, 1 hunk") for x in out.splitlines()) and "1 hunks" not in out
    _, out, _ = run_cli("scan", "--base", "b", "--ours", "o", "--theirs", "t", "--lang", "ko")
    assert "비교용 git merge-file 충돌 수: 1" in out
    put(tmp_path, o=MAIN_SIX[:6], t=FEAT_SIX)
    _, out, _ = run_cli("scan", "--base", "b", "--ours", "o", "--theirs", "t")
    assert "git merge-file reports 2 conflicts for comparison" in out
    rows = [f"{i}" for i in range(1, cl.LARGE_FILE_LINES + 1)]
    put(tmp_path, b=rows, o=rows, t=rows, r=rows)
    code, out, _ = file_verify(run_cli)
    assert code == 0 and json.loads(out)["info"] == []        # exactly the limit is not "large"
    put(tmp_path, b=rows, o=rows, t=rows + ["x"], r=rows + ["x"])
    code, out, _ = file_verify(run_cli)
    assert [i["lines"] for i in json.loads(out)["info"] if i["kind"] == "large_file"] == [cl.LARGE_FILE_LINES + 1]


# --- a resolution that deletes a file no side deleted (QA round 2, Q-5) ---

def both_changed_repo(tmp_path, capsys):
    """f.txt and g.txt are changed by both sides (both conflict); one.txt is a single line both sides rewrote."""
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("f.txt", ["a", "b", "c"])
    sb.write("g.txt", ["x", "y", "z"])
    sb.write("one.txt", ["only"])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("f.txt", ["a", "B-f", "c"])
    sb.write("g.txt", ["x", "Y-f", "z"])
    sb.write("one.txt", ["only-f"])
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("f.txt", ["a", "B-m", "c"])
    sb.write("g.txt", ["x", "Y-m", "z"])
    sb.write("one.txt", ["only-m"])
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    assert sorted(set(sb.stages().split()[3::4])) == ["f.txt", "g.txt", "one.txt"]
    return sb


def dropped(rep: dict, path: str) -> list[tuple]:
    return sorted((e["kind"], e["side"], e["text"]) for e in rep["errors"] if e["path"] == path)


F_DROPPED = [("addition_dropped", "ours", "B-m"), ("addition_dropped", "theirs", "B-f"),
             ("base_line_lost", None, "a"), ("base_line_lost", None, "c")]
ONE_DROPPED = [("addition_dropped", "ours", "only-m"), ("addition_dropped", "theirs", "only-f")]


def test_git_rm_of_a_both_changed_file_fails_before_and_after_the_commit(tmp_path, capsys):
    sb = both_changed_repo(tmp_path, capsys)
    sb.git("rm", "-q", "f.txt", "one.txt")                     # "resolve" by deleting files both sides changed
    sb.write("g.txt", ["x", "Y-f", "z"])
    sb.git("add", "g.txt")
    assert sb.stages() == ""
    code, out, err = sb.run("--json", "verify")                # staged, not committed, no PATH
    rep = json.loads(out)
    assert code == 1, err
    assert {f["path"]: f["skipped"] for f in rep["files"]} == {"f.txt": None, "g.txt": None, "one.txt": None}
    assert dropped(rep, "f.txt") == F_DROPPED
    assert dropped(rep, "one.txt") == ONE_DROPPED              # a file that is one big conflict hunk still fails
    assert not [i for i in rep["info"] if i["kind"] == "file_skipped"]
    code, text, _ = sb.run("--lang", "en", "verify")
    assert code == 1 and "modify_delete" not in text and "PASS" not in text and text.splitlines()[-1].startswith("FAIL")
    code, out, _ = sb.run("--json", "verify", "f.txt")         # naming the deleted path gives the same verdict
    assert code == 1 and dropped(json.loads(out), "f.txt") == F_DROPPED
    sb.git("commit", "-q", "--no-edit")
    code, out, err = sb.run("--json", "verify", "--commit", "HEAD")
    rep = json.loads(out)
    assert code == 1, err
    assert dropped(rep, "f.txt") == F_DROPPED and dropped(rep, "one.txt") == ONE_DROPPED
    assert {f["path"]: f["skipped"] for f in rep["files"]}["f.txt"] is None
    code, text, _ = sb.run("--lang", "en", "verify", "--commit", "HEAD")
    assert code == 1 and "modify_delete" not in text


def test_rm_of_an_unmerged_file_before_verify_fails(tmp_path, capsys):
    sb = both_changed_repo(tmp_path, capsys)
    (sb.root / "f.txt").unlink()                               # plain rm, the stages are still there
    code, out, err = sb.run("--json", "verify")
    rep = json.loads(out)
    assert code == 1, err
    assert {f["path"]: f["source"] for f in rep["files"]}["f.txt"] == "stages"
    assert dropped(rep, "f.txt") == F_DROPPED


def test_deleting_a_file_only_one_side_changed_fails_in_every_mode(tmp_path, capsys):
    """theirs adds a line to keep.txt, ours never touches it; the resolution deletes keep.txt.
    gone.txt is a clean deletion by theirs (ours left it alone): taking it is not a lost change."""
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("keep.txt", ["k1", "k2"])
    sb.write("gone.txt", ["g1"])
    sb.write("c.txt", ["c"])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("keep.txt", ["k1", "k2", "theirs-only"])
    (sb.root / "gone.txt").unlink()
    sb.write("c.txt", ["C-f"])
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("c.txt", ["C-m"])
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    sb.git("rm", "-q", "-f", "keep.txt")                       # keep.txt has the merged-in change staged
    sb.write("c.txt", ["C-m", "C-f"])
    sb.git("add", "c.txt")
    _, text, _ = sb.run("--lang", "en", "status")
    assert "keep.txt" in text and "gone.txt" not in text
    _, ko, _ = sb.run("--lang", "ko", "status")
    assert "어느 쪽도 지우지 않았는데" in ko and "keep.txt" in ko
    expected = [("addition_dropped", "theirs", "theirs-only"), ("base_line_lost", None, "k1"), ("base_line_lost", None, "k2")]
    for args in (("verify",), ("verify", "keep.txt")):
        code, out, err = sb.run("--json", *args)
        rep = json.loads(out)
        assert code == 1, (args, err)
        assert dropped(rep, "keep.txt") == expected, args
        assert "gone.txt" not in [f["path"] for f in rep["files"]], args
    sb.git("commit", "-q", "--no-edit")
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    rep = json.loads(out)
    assert code == 1 and dropped(rep, "keep.txt") == expected
    assert {f["path"]: f["skipped"] for f in rep["files"]}["gone.txt"] == "modify_delete"   # a side deleted it


def test_side_less_findings_name_the_file_or_result_in_text(tmp_path, run_cli):
    put(tmp_path, b=["a", "b"], o=["a", "b", "o"], t=["a", "b"], r=["b", "o", "new"])
    code, en, _ = run_cli("--lang", "en", "verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r")
    assert code == 1
    assert "ERROR r base_line_lost file base:1" in en
    assert "WARN  r new_line_introduced result result:3" in en
    assert " - --" not in en and " - base:" not in en
    _, ko, _ = run_cli("--lang", "ko", "verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r")
    assert "ERROR r base_line_lost 파일 base:1" in ko and "new_line_introduced 결과 result:3" in ko
    run_cli("verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r", "--findings-file", "out.txt")
    assert "base_line_lost - base:1" in (tmp_path / "out.txt").read_text(encoding="utf-8")   # file format unchanged


@pytest.mark.parametrize("base,ours,theirs,result,skipped,errors", [
    # add/add deleted by the resolution: both sides' new lines are lost
    (None, b"o\n", b"t\n", None, None, [("addition_dropped", "ours", "o"), ("addition_dropped", "theirs", "t")]),
    # a file only theirs added, dropped by the resolution
    (None, None, b"new\n", None, None, [("addition_dropped", "theirs", "new")]),
    # a real modify/delete whose deletion was taken: skipped, not an error
    (b"a\n", b"a\nB\n", None, None, "modify_delete", []),
    # modify/delete where the resolution keeps the file but loses the kept side's edit: audited, not skipped
    (b"a\n", b"a\nB\n", None, b"a\n", None, [("addition_dropped", "ours", "B")]),
    # ... and the same when the other side is the one that deleted
    (b"a\n", None, b"a\nB\n", b"a\n", None, [("addition_dropped", "theirs", "B")]),
    # keeping the edited content is clean
    (b"a\n", b"a\nB\n", None, b"a\nB\n", None, []),
    # add/add still present in the result keeps the 0.1.0 behaviour (skipped)
    (None, b"o\n", b"t\n", b"o\n", "add_add", []),
])
def test_deleted_result_verdicts(base, ours, theirs, result, skipped, errors):
    found = cl.verify_item({"path": "x", "base": base, "ours": ours, "theirs": theirs, "result": result,
                            "explicit": False}, "normalize", False)
    assert found["skipped"] == skipped
    assert sorted((e["kind"], e["side"], e["text"]) for e in found["errors"]) == errors


def add_add_merge(tmp_path, capsys) -> Sandbox:
    """QA fresh F-1 repro: a real merge with an add/add conflict (aa.txt) and a modify/delete conflict (md.txt)."""
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("keep.txt", ["x"])
    sb.write("md.txt", ["1", "2", "3"])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("aa.txt", ["shared", "theirs line"])
    (sb.root / "md.txt").unlink()
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("aa.txt", ["shared", "ours line"])
    sb.write("md.txt", ["1", "2 changed", "3"])
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    return sb


def marker_errors(rep: dict, path: str) -> list[str]:
    return [e["text"] for e in rep["errors"] if e["path"] == path and e["kind"] == "conflict_marker_left"]


def test_add_add_file_with_markers_left_fails_in_every_mode(tmp_path, capsys):
    """F-1: a file that still holds conflict markers never passes, even when its line audit is skipped (add/add)."""
    sb = add_add_merge(tmp_path, capsys)
    assert "<<<<<<< HEAD" in (sb.root / "aa.txt").read_text(encoding="utf-8")      # left exactly as git wrote it
    for args in (("verify",), ("verify", "aa.txt")):                                  # unmerged (source: stages)
        code, out, _ = sb.run("--json", *args)
        rep = json.loads(out)
        assert code == 1, args
        assert marker_errors(rep, "aa.txt") == ["<<<<<<< HEAD"], args
        assert {f["path"]: f["skipped"] for f in rep["files"]}["aa.txt"] == "add_add", args
    code, text, _ = sb.run("--lang", "en", "verify")
    assert code == 1 and "PASS" not in text
    assert 'ERROR aa.txt conflict_marker_left result result:2 "<<<<<<< HEAD"' in text
    assert text.rstrip().splitlines()[-1].startswith("FAIL: 1 error(s)")
    code, ko, _ = sb.run("--lang", "ko", "verify")
    assert code == 1 and "conflict_marker_left 결과 result:2" in ko and "통과" not in ko
    # staged with the markers still in it (git add), then committed: still a failure
    sb.git("add", "aa.txt")
    sb.git("rm", "-q", "md.txt")
    code, out, _ = sb.run("--json", "verify")
    assert code == 1 and marker_errors(json.loads(out), "aa.txt") == ["<<<<<<< HEAD"]
    sb.git("commit", "-q", "--no-edit")
    for args in (("verify", "--commit", "HEAD"), ("verify", "--commit", "HEAD", "aa.txt")):
        code, out, _ = sb.run("--json", *args)
        rep = json.loads(out)
        assert code == 1, args
        assert marker_errors(rep, "aa.txt") == ["<<<<<<< HEAD"], args


def test_add_add_file_resolved_by_hand_still_passes(tmp_path, capsys):
    """The neighbour of F-1: an add/add file resolved without markers keeps the 0.1.0 verdict (skipped, exit 0)."""
    sb = add_add_merge(tmp_path, capsys)
    sb.write("aa.txt", ["shared", "ours line", "theirs line"])
    for args in (("verify",), ("verify", "aa.txt")):
        code, out, _ = sb.run("--json", *args)
        rep = json.loads(out)
        assert code == 0, args
        assert rep["errors"] == [] and {f["path"]: f["skipped"] for f in rep["files"]}["aa.txt"] == "add_add"
    sb.git("add", "aa.txt")
    sb.git("rm", "-q", "md.txt")
    sb.git("commit", "-q", "--no-edit")
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    assert code == 0 and json.loads(out)["errors"] == []


MARKED = b"<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\n"


@pytest.mark.parametrize("base,ours,theirs,skipped", [
    (None, b"ours\n", b"theirs\n", "add_add"),
    (b"a\n", b"a\nB\n", None, None),                       # kept after a modify/delete: line-audited now, markers still found
    (b"\0bin", b"\0ours", b"\0theirs", "binary"),          # binary versions, but the result is text with markers
])
def test_markers_are_checked_for_every_skipped_kind(base, ours, theirs, skipped):
    found = cl.verify_item({"path": "x", "base": base, "ours": ours, "theirs": theirs, "result": MARKED,
                            "explicit": False}, "normalize", False)
    assert found["skipped"] == skipped
    assert ("conflict_marker_left", 1) in [(e["kind"], e.get("result_line")) for e in found["errors"]]
    if skipped is not None:
        assert [i["kind"] for i in found["info"]] == ["file_skipped"]
        # without markers the same inputs stay a plain skip
        clean = cl.verify_item({"path": "x", "base": base, "ours": ours, "theirs": theirs, "result": b"ours\ntheirs\n",
                                "explicit": False}, "normalize", False)
        assert clean["skipped"] == skipped and clean["errors"] == []


def test_skipped_kind_keeps_marker_like_lines_a_side_already_had():
    """A file whose sides already contain a marker-like pair (a test fixture, say) is not flagged for keeping it."""
    fixture = b"<<<<<<< HEAD\nx\n>>>>>>> feature\n"
    found = cl.verify_item({"path": "x", "base": None, "ours": fixture, "theirs": b"other\n", "result": fixture,
                            "explicit": False}, "normalize", False)
    assert found["skipped"] == "add_add" and found["errors"] == []


def test_file_mode_binary_inputs_with_markers_in_result_fail(tmp_path, run_cli):
    """File mode: binary inputs are an input problem (exit 3), unless the result itself still holds markers (exit 1)."""
    put(tmp_path, b=b"\0b", o=b"\0o", t=b"\0t", r=MARKED)
    code, out, _ = file_verify(run_cli)
    rep = json.loads(out)
    assert code == 1 and [e["kind"] for e in rep["errors"]] == ["conflict_marker_left"]
    put(tmp_path, r=b"\0r")
    code, _, _ = file_verify(run_cli)
    assert code == 3


def rename_merge(tmp_path, capsys, renamer: str, keep_edit: bool = True) -> Sandbox:
    """One side renames ren.txt -> renamed.txt, the other edits r3; git merges it cleanly by rename detection."""
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("ren.txt", [f"r{i}" for i in range(1, 7)])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    edited = ["r1", "r2", "r3-edit", "r4", "r5", "r6"]
    if renamer == "theirs":
        sb.git("mv", "ren.txt", "renamed.txt")
    else:
        sb.write("ren.txt", edited)
    sb.commit("feature")
    sb.git("checkout", "-q", "main")
    if renamer == "theirs":
        sb.write("ren.txt", edited)
    else:
        sb.git("mv", "ren.txt", "renamed.txt")
    sb.commit("main")
    sb.git("merge", "--no-edit", "--no-commit", "feature")
    assert (sb.root / "renamed.txt").read_text(encoding="utf-8").splitlines()[2] == "r3-edit"
    if not keep_edit:
        sb.write("renamed.txt", [f"r{i}" for i in range(1, 7)])      # the merge commit drops the edit
        sb.git("add", "renamed.txt")
    sb.git("commit", "-q", "--no-edit")
    return sb


@pytest.mark.parametrize("renamer", ["theirs", "ours"])
def test_verify_commit_follows_a_rename_on_one_side(tmp_path, capsys, renamer):
    """QA Q-7: a file renamed by one side and edited by the other is audited as one file, not as a new file."""
    sb = rename_merge(tmp_path, capsys, renamer)
    for args in (("verify", "--commit", "HEAD"), ("verify", "--commit", "HEAD", "renamed.txt")):
        code, out, _ = sb.run("--json", *args)
        rep = json.loads(out)
        assert code == 0, (args, rep["errors"], rep["warnings"])
        assert [f["path"] for f in rep["files"]] == ["renamed.txt"]
        follow = [i for i in rep["info"] if i["kind"] == "rename_followed"]
        assert [(i["side"], i["old"]) for i in follow] == [(renamer, "ren.txt")]
    _, text, _ = sb.run("--lang", "en", "verify", "--commit", "HEAD")
    assert "rename_followed" in text and "renamed from ren.txt by" in text
    _, ko, _ = sb.run("--lang", "ko", "verify", "--commit", "HEAD")
    assert "ren.txt 에서 이름을 바꿈" in ko


def test_verify_commit_still_catches_an_edit_lost_across_a_rename(tmp_path, capsys):
    sb = rename_merge(tmp_path, capsys, "theirs", keep_edit=False)
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    rep = json.loads(out)
    assert code == 1
    assert ("addition_dropped", "ours", "r3-edit") in [(e["kind"], e["side"], e["text"]) for e in rep["errors"]]


def test_renamed_paths_reads_git_rename_detection(tmp_path, capsys):
    sb = rename_merge(tmp_path, capsys, "theirs")
    repo = cl.open_repo(sb.root)
    assert cl.renamed_paths(repo, "HEAD^1~1", "HEAD^1") == {}                 # main only edited ren.txt
    assert cl.renamed_paths(repo, "HEAD^1~1", "HEAD^2") == {"renamed.txt": "ren.txt"}


def modify_delete_merge(tmp_path, capsys, deleter: str = "feature") -> Sandbox:
    """One side deletes g.txt, the other edits it (changes b, adds main-add); the merge stops on modify/delete."""
    sb = Sandbox(tmp_path / "r", capsys)
    (tmp_path / "r").mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("g.txt", ["a", "b", "c"])
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    if deleter == "feature":
        sb.git("rm", "-q", "g.txt")
    else:
        sb.write("g.txt", ["a", "B-feat", "c", "feat-add"])
    sb.commit("feature")
    sb.git("checkout", "-q", "main")
    if deleter == "feature":
        sb.write("g.txt", ["a", "B-main", "c", "main-add"])
    else:
        sb.git("rm", "-q", "g.txt")
    sb.commit("main")
    sb.git("merge", "--no-edit", "feature", check=False)
    return sb


def test_modify_delete_kept_file_that_loses_the_edit_fails_on_every_path(tmp_path, capsys):
    sb = modify_delete_merge(tmp_path, capsys)
    sb.write("g.txt", ["a", "b", "c"])                         # keep the file, revert main's edit to the base
    for args in (("--json", "verify"), ("--json", "verify", "g.txt")):
        code, out, err = sb.run(*args)
        rep = json.loads(out)
        assert code == 1, (args, err)
        assert ("addition_dropped", "ours", "main-add") in [(e["kind"], e["side"], e["text"]) for e in rep["errors"]]
        assert rep["files"][0]["skipped"] is None
    code, out, _ = sb.run("verify")
    assert code == 1 and "PASS" not in out and "main-add" in out
    sb.git("add", "g.txt")
    sb.git("commit", "-q", "--no-edit")
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    errors = json.loads(out)["errors"]
    assert code == 1 and ("addition_dropped", "ours", "main-add") in [(e["kind"], e["side"], e["text"]) for e in errors]


def test_modify_delete_kept_file_that_loses_the_edit_fails_when_the_other_side_deleted(tmp_path, capsys):
    sb = modify_delete_merge(tmp_path, capsys, deleter="main")
    sb.write("g.txt", ["a", "b", "c"])
    code, out, err = sb.run("--json", "verify", "g.txt")
    errors = json.loads(out)["errors"]
    assert code == 1, err
    assert ("addition_dropped", "theirs", "feat-add") in [(e["kind"], e["side"], e["text"]) for e in errors]


def test_modify_delete_kept_file_with_the_edit_passes(tmp_path, capsys):
    sb = modify_delete_merge(tmp_path, capsys)
    sb.write("g.txt", ["a", "B-main", "c", "main-add"])
    code, out, err = sb.run("--json", "verify", "g.txt")
    assert code == 0, err
    assert json.loads(out)["errors"] == []
    sb.git("add", "g.txt")
    sb.git("commit", "-q", "--no-edit")
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD")
    assert code == 0 and json.loads(out)["errors"] == []


def test_modify_delete_taking_the_deletion_stays_skipped(tmp_path, capsys):
    sb = modify_delete_merge(tmp_path, capsys)
    sb.git("rm", "-q", "g.txt")
    code, out, err = sb.run("--json", "verify")
    rep = json.loads(out)
    assert code == 0, err
    assert rep["errors"] == [] and rep["files"][0]["skipped"] == "modify_delete"
    assert [i["kind"] for i in rep["info"]] == ["file_skipped"]


def test_modify_delete_kept_file_with_markers_still_fails(tmp_path, capsys):
    sb = modify_delete_merge(tmp_path, capsys)
    sb.write("g.txt", ["<<<<<<< HEAD", "a", "B-main", "c", "main-add", "=======", "a", "b", "c", ">>>>>>> feature"])
    code, out, err = sb.run("--json", "verify", "g.txt")
    assert code == 1, err
    assert "conflict_marker_left" in [e["kind"] for e in json.loads(out)["errors"]]
