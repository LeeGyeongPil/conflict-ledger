"""AC-10 (1)(2): one script call gives the verdict, input problems included.

An input problem (exit 3) comes back as a ledger: --json prints
{"ok": false, "exit_code": 3, "input_error": {"kind", "path", "message"}} on stdout and --findings-file writes
'input_error <kind> <path>' plus 'summary: exit=3 input_error=<kind>'. Binary files are recognised by their
content, never by their name: every file here is made inside the test with its own name and bytes.
"""
from __future__ import annotations

import hashlib
import json

import pytest

import conflict_ledger as cl
from conftest import BASE, Sandbox, make_repo, scenario


def binary_bytes(seed: str) -> bytes:
    """Synthetic bytes with NUL bytes in them, different for every seed."""
    return b"\x00\x7f" + hashlib.sha256(seed.encode()).digest() + b"\x00" + seed.encode()


def put(folder, files: dict[str, bytes | str]) -> None:
    for name, data in files.items():
        (folder / name).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def ledger(out: str) -> dict:
    data = json.loads(out)
    assert set(data) == {"ok", "exit_code", "input_error"}
    assert data["ok"] is False and data["exit_code"] == 3
    assert set(data["input_error"]) == {"kind", "path", "message"}
    return data["input_error"]


def file_args(names: list[str]) -> list[str]:
    return ["verify", "--base", names[0], "--ours", names[1], "--theirs", names[2], "--result", names[3]]


# --------------------------------------------------------------------------- binary: decided by content

@pytest.mark.parametrize("names", [
    ["q_base.txt", "q_ours.txt", "q_theirs.txt", "q.txt"],            # text extension, binary content
    ["v1.dat", "v2.dat", "v3.dat", "merged.dat"],
    ["anc", "mine", "other", "out"],                                    # no extension at all
])
def test_binary_input_gives_one_ledger_whatever_the_name(tmp_path, run_cli, names):
    put(tmp_path, {n: binary_bytes(n) for n in names})
    code, out, err = run_cli("--json", *file_args(names))
    problem = ledger(out)
    assert code == 3
    assert problem["kind"] == "binary" and problem["path"] == names[0]
    assert problem["message"] == f"binary file, line audit skipped: {names[0]}; compare it by hand or pass text files"
    assert err.strip() == problem["message"]                          # the stderr line is still there


def test_binary_findings_file_lines(tmp_path, run_cli):
    names = ["w_base.txt", "w_ours.txt", "w_theirs.txt", "w.txt"]
    put(tmp_path, {n: binary_bytes(n + "-x") for n in names})
    code, out, _ = run_cli(*file_args(names), "--findings-file", "found.txt")
    assert code == 3 and out == ""                                    # text mode: nothing on stdout
    assert (tmp_path / "found.txt").read_text().splitlines() == [
        "input_error binary w_base.txt", "summary: exit=3 input_error=binary"]


def test_only_the_binary_input_is_named(tmp_path, run_cli):
    """The first input that is binary is reported, with the path exactly as given."""
    put(tmp_path, {"b": "a\n", "o": "a\nb\n", "t": "a\n"})
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "r.txt").write_bytes(binary_bytes("result"))
    code, out, _ = run_cli("--json", *file_args(["b", "o", "t", "sub/r.txt"]))
    assert code == 3 and ledger(out)["path"] == "sub/r.txt"


def test_text_with_a_binary_looking_name_is_audited(tmp_path, run_cli):
    """The reverse: a text file named like an image is a normal line audit, not an input problem."""
    names = ["pic_base.png", "pic_ours.png", "pic_theirs.png", "pic.png"]
    put(tmp_path, {names[0]: "a\nb\n", names[1]: "a\nb\nours\n", names[2]: "a\nb\n", names[3]: "a\nb\n"})
    code, out, _ = run_cli("--json", *file_args(names))
    report = json.loads(out)
    assert code == 1 and "input_error" not in report
    assert [e["kind"] for e in report["errors"]] == ["addition_dropped"]


def test_binary_in_a_repository(tmp_path, capsys):
    sb = make_repo(tmp_path / "r", capsys, name="table.txt", base=binary_bytes("b"), ours=binary_bytes("o"),
                   theirs=binary_bytes("t"))
    code, out, _ = sb.run("--json", "verify", "table.txt")
    problem = ledger(out)
    assert code == 3 and problem["kind"] == "binary" and problem["path"] == "table.txt"
    code, out, _ = sb.run("--json", "status")
    assert json.loads(out)["unmerged_kinds"] == {"table.txt": "binary"}
    code, out, _ = sb.run("--json", "resolve-trivial", "table.txt")
    assert code == 3 and ledger(out)["kind"] == "binary"


# --------------------------------------------------------------------------- the other input problems

def test_not_a_repository(tmp_path, run_cli):
    empty = tmp_path / "empty"
    empty.mkdir()
    for command in ("status", "scan", "verify"):
        code, out, _ = run_cli("--json", "--repo", str(empty), command)
        problem = ledger(out)
        assert code == 3 and problem["kind"] == "not_a_repo" and problem["path"] == str(empty)
    code, _, _ = run_cli("--repo", str(empty), "verify", "--findings-file", str(tmp_path / "f.txt"))
    assert code == 3
    assert (tmp_path / "f.txt").read_text().splitlines() == [f"input_error not_a_repo {empty}",
                                                             "summary: exit=3 input_error=not_a_repo"]


# --------------------------------------------------------------------------- run from the skill's own folder

def test_skill_folder_is_not_read_as_the_repository(run_cli, monkeypatch):
    """An agent that cd's into the skill folder (a git clone when installed as a plugin) used to get the plugin's own
    repository back as 'operation none' and went on merging there. Now it is one input problem, in one call."""
    monkeypatch.chdir(cl.SKILL_FOLDER)
    for command in ("status", "scan", "verify", "resolve-trivial"):
        code, out, err = run_cli("--json", command)
        problem = ledger(out)
        assert code == 3 and problem["kind"] == "skill_folder" and problem["path"] == "."
        assert problem["message"].startswith("this is the conflict-ledger skill's own folder")
        assert err.strip() == problem["message"]
    monkeypatch.chdir(cl.SKILL_FOLDER / "scripts")                    # any folder inside it
    code, out, _ = run_cli("--json", "status")
    assert code == 3 and ledger(out)["kind"] == "skill_folder"
    code, out, _ = run_cli("--json", "--lang", "ko", "status")
    assert code == 3 and ledger(out)["message"].startswith("conflict-ledger 스킬 자체 폴더")


def test_skill_folder_through_repo_option_and_findings_file(tmp_path, run_cli):
    code, out, _ = run_cli("--json", "--repo", str(cl.SKILL_FOLDER / "references"), "status")
    assert code == 3 and ledger(out)["path"] == str(cl.SKILL_FOLDER / "references")
    code, _, _ = run_cli("--repo", str(cl.SKILL_FOLDER), "verify", "--findings-file", str(tmp_path / "f.txt"))
    assert code == 3
    assert (tmp_path / "f.txt").read_text().splitlines() == [f"input_error skill_folder {cl.SKILL_FOLDER}",
                                                             "summary: exit=3 input_error=skill_folder"]


def test_repository_that_holds_the_skill_folder_still_works(tmp_path, capsys, run_cli, monkeypatch):
    """Only the skill folder itself is refused: a project that vendors the skill (or this project's own checkout)
    is checked as usual from its root, and file mode does not look at the folder at all."""
    ours, theirs, _ = scenario()
    sb = make_repo(tmp_path / "proj", capsys, ours=ours, theirs=theirs)
    vendored = tmp_path / "proj" / ".agents" / "skills" / "conflict-ledger"
    (vendored / "scripts").mkdir(parents=True)
    monkeypatch.setattr(cl, "SKILL_FOLDER", vendored.resolve())
    code, out, _ = sb.run("--json", "status")                          # from the project root: the real conflict
    assert code == 0 and json.loads(out)["unmerged"] == ["f.txt"]
    code, out, _ = run_cli("--json", "--repo", str(vendored / "scripts"), "status")
    assert code == 3 and ledger(out)["kind"] == "skill_folder"
    # file mode from inside the skill folder: the given files are read, the folder is not a repository question
    put(tmp_path, {"b": "a\n", "o": "a\nb\n", "t": "a\n", "r": "a\nb\n"})
    monkeypatch.chdir(vendored)
    code, out, _ = run_cli("--json", *file_args([str(tmp_path / n) for n in "botr"]))
    assert code == 0 and json.loads(out)["ok"] is True


def test_no_conflict_in_progress(tmp_path, capsys):
    ours, theirs, _ = scenario()
    clean = make_repo(tmp_path / "clean", capsys, ours=ours, theirs=theirs, mode="none")
    code, out, _ = clean.run("--json", "verify")
    problem = ledger(out)
    assert code == 3 and problem["kind"] == "no_conflict"
    assert problem["message"].startswith("no conflict in progress and no snapshot for ")
    # status itself is not an input problem: it answers with operation none and no unmerged paths
    code, out, _ = clean.run("--json", "status")
    st = json.loads(out)
    assert code == 0 and st["operation"] == "none" and st["unmerged"] == [] and st["snapshots"] == []
    assert st["unmerged_kinds"] == {}


def test_missing_file(tmp_path, run_cli):
    put(tmp_path, {"b": "a\n", "o": "a\n", "t": "a\n"})
    code, out, _ = run_cli("--json", *file_args(["b", "o", "t", "nowhere.txt"]), "--findings-file", "f.txt")
    problem = ledger(out)
    # 0.1.3: 메시지가 고칠 방법까지 알려 준다(kind·path·findings 줄은 그대로)
    assert code == 3 and problem == {
        "kind": "file_missing", "path": "nowhere.txt",
        "message": "file not found: nowhere.txt; check the path (relative paths are read from the current folder)"}
    assert (tmp_path / "f.txt").read_text().splitlines() == ["input_error file_missing nowhere.txt",
                                                             "summary: exit=3 input_error=file_missing"]
    # 같은 입력을 한국어로: 문구만 바뀌고 kind·path 는 같다
    code, out, err = run_cli("--json", "--lang", "ko", *file_args(["b", "o", "t", "nowhere.txt"]))
    ko_problem = ledger(out)
    assert code == 3 and (ko_problem["kind"], ko_problem["path"]) == ("file_missing", "nowhere.txt")
    assert ko_problem["message"].startswith("파일을 찾을 수 없음: nowhere.txt, 경로를 확인할 것")
    assert err.strip() == ko_problem["message"]


# --------------------------------------------------------------------------- a binary file next to a text file

def test_binary_beside_text_in_a_real_merge(tmp_path, capsys):
    """One real merge where a text file and a binary file both conflict (stages 1/2/3 for both).
    Without a PATH, verify audits the text file and only notes the binary one (file_skipped, not an input
    problem); naming the binary path explicitly is still an input problem (exit 3, kind binary)."""
    ours, theirs, good = scenario()
    sb = Sandbox(tmp_path / "mixed", capsys)
    sb.root.mkdir()
    sb.git("init", "-q", "-b", "main")
    sb.write("notes.txt", BASE)
    sb.write("pixels.dat", binary_bytes("base"))
    sb.commit("base")
    sb.git("checkout", "-q", "-b", "feature")
    sb.write("notes.txt", theirs)
    sb.write("pixels.dat", binary_bytes("theirs"))
    sb.commit("theirs")
    sb.git("checkout", "-q", "main")
    sb.write("notes.txt", ours)
    sb.write("pixels.dat", binary_bytes("ours"))
    sb.commit("ours")
    sb.git("merge", "--no-edit", "feature", check=False)          # 두 파일 모두 충돌

    # 두 경로 모두 인덱스 stage 1·2·3 이 있다
    stages: dict[str, set[str]] = {}
    for row in sb.stages().splitlines():
        meta, path = row.split("\t", 1)
        stages.setdefault(path, set()).add(meta.split()[2])
    assert stages == {"notes.txt": {"1", "2", "3"}, "pixels.dat": {"1", "2", "3"}}

    # status 와 scan 이 경로별 종류를 같게 본다
    code, out, _ = sb.run("--json", "status")
    st = json.loads(out)
    assert code == 0 and st["unmerged"] == ["notes.txt", "pixels.dat"]
    assert st["unmerged_kinds"] == {"notes.txt": "text", "pixels.dat": "binary"}
    code, out, _ = sb.run("--json", "scan")
    assert code == 0
    assert {f["path"]: f["kind"] for f in json.loads(out)["files"]} == {"notes.txt": "text", "pixels.dat": "binary"}

    # theirs 통째로 푼 텍스트 파일: 텍스트 감사가 되살아난 삭제를 잡고, 바이너리는 건너뜀 알림만
    sb.write("notes.txt", theirs)
    code, out, _ = sb.run("--json", "verify")
    report = json.loads(out)
    assert code == 1 and "input_error" not in report
    assert "deletion_reverted" in [e["kind"] for e in report["errors"]]
    assert {f["path"]: f["skipped"] for f in report["files"]} == {"notes.txt": None, "pixels.dat": "binary"}
    skipped = [i for i in report["info"] if i["kind"] == "file_skipped"]
    assert [(i["path"], i["reason"]) for i in skipped] == [("pixels.dat", "binary")]

    code, out, _ = sb.run("verify")
    assert code == 1 and "binary file: line audit skipped" in out
    assert out.strip().splitlines()[-1].startswith("FAIL:")
    code, out, _ = sb.run("--lang", "ko", "verify")
    assert code == 1 and "binary 파일, 줄 감사 건너뜀" in out

    # 양쪽을 합친 해결: 전체 판정은 텍스트 감사 결과(통과)를 따른다
    sb.write("notes.txt", good)
    found = tmp_path / "found.txt"
    code, out, _ = sb.run("--json", "verify", "--findings-file", str(found))
    assert code == 0 and json.loads(out)["ok"] is True
    rows = found.read_text(encoding="utf-8").splitlines()
    assert rows[-1].startswith("summary: ") and "exit=0" in rows[-1]
    assert not any(r.startswith("input_error") for r in rows)

    # 바이너리 경로를 직접 지정하면 지금처럼 입력 문제다
    code, out, _ = sb.run("--json", "verify", "pixels.dat")
    problem = ledger(out)
    assert code == 3 and problem["kind"] == "binary" and problem["path"] == "pixels.dat"


def test_single_parent_commit(tmp_path, capsys):
    ours, theirs, _ = scenario()
    sb = make_repo(tmp_path / "r", capsys, ours=ours, theirs=theirs, mode="none")
    found = tmp_path / "found.txt"
    code, out, _ = sb.run("--json", "verify", "--commit", "HEAD", "--findings-file", str(found))
    problem = ledger(out)
    assert code == 3 and problem["kind"] == "not_two_parent" and problem["path"] is None
    assert "is not a two-parent merge" in problem["message"]
    assert found.read_text().splitlines() == ["input_error not_two_parent -", "summary: exit=3 input_error=not_two_parent"]


def test_options_given_partly(run_cli):
    code, out, _ = run_cli("--json", "verify", "--base", "b")
    assert code == 3 and ledger(out) == {"kind": "triple_together", "path": None,
                                         "message": "--base/--ours/--theirs must be given together"}


def test_hand_edited_file_refusal_is_a_ledger_too(conflict, tmp_path):
    conflict.write("f.txt", [*conflict.theirs, "user-note"])
    code, out, err = conflict.run("--json", "resolve-trivial", "f.txt", "--findings-file", str(tmp_path / "f.txt"))
    problem = ledger(out)
    assert code == 3 and problem["kind"] == "hand_edited" and problem["path"].endswith("f.txt")
    assert "refusing to overwrite" in problem["message"] and err.strip() == problem["message"]
    assert (tmp_path / "f.txt").read_text().splitlines()[1] == "summary: exit=3 input_error=hand_edited"


def test_ledger_kind_and_path_do_not_follow_the_language(tmp_path, run_cli):
    names = ["k1", "k2", "k3", "k4"]
    put(tmp_path, {n: binary_bytes(n) for n in names})
    _, en, _ = run_cli("--json", "--lang", "en", *file_args(names))
    _, ko, _ = run_cli("--json", "--lang", "ko", *file_args(names))
    en_problem, ko_problem = ledger(en), ledger(ko)
    assert (en_problem["kind"], en_problem["path"]) == (ko_problem["kind"], ko_problem["path"]) == ("binary", "k1")
    assert ko_problem["message"].startswith("바이너리 파일, 줄 감사 건너뜀: k1")


def test_unwritable_findings_file_is_an_input_problem_not_a_lost_change(tmp_path, run_cli):
    """A crash would exit 1, which means 'a change was lost'; a file that cannot be written is exit 3 instead."""
    put(tmp_path, {"b": "a\n", "o": "a\nb\n", "t": "a\n", "r": "a\nb\n"})
    target = tmp_path / "no-such-folder" / "found.txt"
    code, out, err = run_cli("--json", *file_args(["b", "o", "t", "r"]), "--findings-file", str(target))
    problem = ledger(out)
    assert code == 3 and problem["kind"] == "os_error" and problem["path"] == str(target)
    assert err.strip().startswith(f"cannot read or write {target}: ")


def test_text_mode_is_unchanged(tmp_path, run_cli):
    """Without --json the input problem is still exactly one stderr line and nothing on stdout."""
    names = ["m1", "m2", "m3", "m4"]
    put(tmp_path, {n: binary_bytes(n) for n in names})
    code, out, err = run_cli(*file_args(names))
    assert code == 3 and out == "" and err.count("\n") == 1
