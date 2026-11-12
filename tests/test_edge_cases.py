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
