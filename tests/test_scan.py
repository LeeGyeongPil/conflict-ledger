"""AC-1: scan reads the index stages, classifies hunks, stores snapshots; status labels."""
from __future__ import annotations

import json
from pathlib import Path

from conftest import make_repo, scenario


def test_scan_classifies_probe_scenario(conflict):
    code, out, _ = conflict.run("--json", "scan")
    ledger = json.loads(out)
    assert code == 0
    assert ledger["operation"] == "merge"
    (entry,) = ledger["files"]
    got = [(h["id"], h["class"], h["base"]) for h in entry["hunks"]]
    assert got == [("H1", "true_conflict", [1, 2]), ("H2", "ours_only", [4, 5]),
                   ("H3", "whitespace_only", [6, 7]), ("H4", "theirs_only", [10, 10])]
    assert [h["auto_resolvable"] for h in entry["hunks"]] == [False, True, True, True]
    assert entry["kind"] == "text"
    assert entry["hunks"][0]["ours_excerpt"] == ["line 2 OURS"]
    assert entry["hunks"][0]["theirs_excerpt"] == ["line 2 THEIRS"]
    assert entry["git_merge_file_conflicts"] == 2     # git also leaves the whitespace hunk as a conflict


def test_scan_saves_snapshot_under_git_path(conflict):
    conflict.run("scan")
    snap_root = Path(conflict.git("rev-parse", "--git-path", "conflict-ledger").stdout.strip())
    snap_root = snap_root if snap_root.is_absolute() else conflict.root / snap_root
    metas = list(snap_root.glob("*/meta.json"))
    assert len(metas) == 1
    meta = json.loads(metas[0].read_text())
    assert meta["path"] == "f.txt" and meta["operation"] == "merge"
    assert (metas[0].parent / "base").read_text().splitlines()[4] == "line 5"
    assert (metas[0].parent / "theirs").read_text().splitlines()[1] == "line 2 THEIRS"


def test_scan_text_output_lists_hunks(conflict):
    code, out, _ = conflict.run("scan")
    assert code == 0
    assert "H1 true_conflict base L2 [judge]" in out
    assert "H4 theirs_only base after L10 [auto]" in out
    assert 'ours: "line 2 OURS"' in out


def test_status_merge_labels(conflict):
    code, out, _ = conflict.run("--json", "status")
    st = json.loads(out)
    assert code == 0
    assert st["operation"] == "merge"
    assert st["labels"] == {"ours": "HEAD", "theirs": "MERGE_HEAD"}
    assert st["unmerged"] == ["f.txt"]
    assert st["unmerged_kinds"] == {"f.txt": "text"}          # 0.1.1: added next to the unchanged unmerged list
    assert st["my_commit_side"] is None
    _, text, _ = conflict.run("status")
    assert "stage 2 (ours) = HEAD (current branch)" in text
    assert "stage 3 (theirs) = MERGE_HEAD (branch being merged in)" in text


def test_status_rebase_labels_are_swapped(tmp_path, capsys):
    ours, theirs, _ = scenario()
    sb = make_repo(tmp_path / "rb", capsys, ours=ours, theirs=theirs, mode="rebase")
    _, out, _ = sb.run("--json", "status")
    st = json.loads(out)
    assert st["operation"] == "rebase"
    assert st["labels"] == {"ours": "upstream", "theirs": "my-commit"}
    assert st["my_commit_side"] == "stage3"
    # the index agrees: stage 2 is the upstream (main) file, stage 3 is the commit being replayed
    _, scan_out, _ = sb.run("--json", "scan")
    hunk = json.loads(scan_out)["files"][0]["hunks"][0]
    assert hunk["ours_excerpt"] == ["line 2 OURS"] and hunk["theirs_excerpt"] == ["line 2 THEIRS"]
    _, text, _ = sb.run("status")
    assert "my-commit-side: stage3" in text


def test_status_cherry_pick_and_clean_repo(tmp_path, capsys):
    ours, theirs, _ = scenario()
    sb = make_repo(tmp_path / "cp", capsys, ours=ours, theirs=theirs, mode="cherry-pick")
    _, out, _ = sb.run("--json", "status")
    assert json.loads(out)["labels"] == {"ours": "HEAD", "theirs": "CHERRY_PICK_HEAD"}
    clean = make_repo(tmp_path / "clean", capsys, ours=ours, theirs=theirs, mode="none")
    code, out, _ = clean.run("status")
    assert code == 0 and "no unmerged files" in out and "operation: none" in out


def test_scan_without_conflicts_is_an_input_problem(tmp_path, capsys):
    ours, theirs, _ = scenario()
    clean = make_repo(tmp_path / "clean", capsys, ours=ours, theirs=theirs, mode="none")
    code, _, err = clean.run("scan")
    assert code == 3 and "no unmerged files" in err


def test_scan_file_mode(tmp_path, run_cli):
    ours, theirs, _ = scenario()
    from conftest import BASE
    for name, lines in (("b", BASE), ("o", ours), ("t", theirs)):
        (tmp_path / name).write_text("".join(x + "\n" for x in lines))
    code, out, _ = run_cli("--json", "scan", "--base", "b", "--ours", "o", "--theirs", "t", "--name", "f.txt")
    ledger = json.loads(out)
    assert code == 0 and ledger["files"][0]["path"] == "f.txt"
    assert len(ledger["files"][0]["hunks"]) == 4


def test_status_reports_the_kind_of_every_unmerged_path(tmp_path, capsys):
    """0.1.1: status --json names each conflict's kind from the staged contents, so binary, add/add,
    modify/delete and symlink conflicts are known from the first command (the unmerged list is unchanged)."""
    from conftest import Sandbox
    sb = Sandbox(tmp_path / "r", capsys)
    sb.root.mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("text.txt", ["1", "2", "3"])
    sb.write("data.txt", b"\0\1base")              # binary content behind a .txt name
    sb.write("gone.txt", ["x", "y"])
    (sb.root / "link").symlink_to("text.txt")
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("text.txt", ["1", "2 theirs", "3"])
    sb.write("data.txt", b"\0\1theirs")
    sb.write("new.txt", ["theirs"])
    (sb.root / "gone.txt").unlink()
    (sb.root / "link").unlink()
    (sb.root / "link").symlink_to("gone.txt")
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("text.txt", ["1", "2 ours", "3"])
    sb.write("data.txt", b"\0\1ours")
    sb.write("new.txt", ["ours"])
    sb.write("gone.txt", ["x", "y changed"])
    (sb.root / "link").unlink()
    (sb.root / "link").symlink_to("new.txt")
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)
    code, out, _ = sb.run("--json", "status")
    st = json.loads(out)
    assert code == 0
    assert st["unmerged"] == ["data.txt", "gone.txt", "link", "new.txt", "text.txt"]
    assert st["unmerged_kinds"] == {"data.txt": "binary", "gone.txt": "modify_delete", "link": "symlink",
                                    "new.txt": "add_add", "text.txt": "text"}
