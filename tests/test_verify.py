"""AC-2: verify findings and exit codes on the probe scenario."""
from __future__ import annotations

import json
import re

import conflict_ledger as cl
from conftest import BASE


def verify(sb, lines, *extra):
    sb.write("f.txt", lines)
    code, out, _ = sb.run("--json", "verify", "f.txt", *extra)
    return code, json.loads(out)


def kinds(items):
    return [(f["kind"], f["side"], f.get("base_line")) for f in items]


def test_correct_resolution_passes_with_two_judgement_infos(conflict):
    code, rep = verify(conflict, conflict.good)
    assert code == 0 and rep["ok"] and rep["exit_code"] == 0
    assert rep["errors"] == [] and rep["warnings"] == []
    assert [(i["kind"], i["side"]) for i in rep["info"]] == [("judgement_hunk", "ours"), ("judgement_hunk", "theirs")]


def test_take_ours_wholesale_drops_theirs_addition(conflict):
    code, rep = verify(conflict, conflict.ours)
    assert code == 1
    (err,) = rep["errors"]
    assert (err["kind"], err["side"], err["text"]) == ("addition_dropped", "theirs", "added by theirs")
    assert err["side_label"] == "MERGE_HEAD" and err["base_line"] == 11


def test_take_theirs_wholesale_revives_deleted_line(conflict):
    code, rep = verify(conflict, conflict.theirs)
    assert code == 1
    (err,) = rep["errors"]
    assert kinds([err]) == [("deletion_reverted", "ours", 5)]
    assert err["text"] == "line 5" and err["side_label"] == "HEAD"
    _, text, _ = conflict.run("verify", "f.txt")
    assert 'deletion_reverted ours base:5 "line 5"' in text
    assert "removed by HEAD (current branch)" in text


def test_marker_left_is_an_error(conflict):
    code, rep = verify(conflict, ["<<<<<<< HEAD"] + conflict.good + ["======= ", ">>>>>>> feature"])
    assert code == 1
    assert [f["kind"] for f in rep["errors"]] == ["conflict_marker_left"]
    assert rep["errors"][0]["result_line"] == 1


def test_untouched_base_lines_blanked_are_reported_per_line(conflict):
    blanked = list(conflict.good)
    for i in (6, 7, 8):                       # "line 8", "line 9", "line 10" in the result
        blanked[i] = ""
    code, rep = verify(conflict, blanked)
    assert code == 1
    lost = [(f["base_line"], f["text"]) for f in rep["errors"] if f["kind"] == "base_line_lost"]
    assert lost == [(8, "line 8"), (9, "line 9"), (10, "line 10")]


def test_side_discarded_warning_when_one_side_leaves_no_trace(tmp_path, capsys):
    from conftest import make_repo
    ours = list(BASE)
    ours[1] = "alpha beta gamma"
    theirs = list(BASE)
    theirs[1] = "zzz 123 qqq"
    sb = make_repo(tmp_path / "r", capsys, ours=ours, theirs=theirs)
    code, rep = verify(sb, ours)
    assert code == 2 and rep["errors"] == []
    assert kinds(rep["warnings"]) == [("side_discarded", "theirs", 2)]
    code, rep = verify(sb, ours[:1] + ["alpha beta gamma", "zzz 123 qqq"] + ours[2:])
    assert code == 0                           # both kept: nothing discarded


def test_adapted_rename_is_only_a_warning(tmp_path, capsys):
    from conftest import make_repo
    base = [f"x{i} = 1" for i in range(1, 31)]
    base[0] = "def old_name(a):"
    base[1] = "    return a"
    ours = list(base)
    ours[14] = "x15 = ours"                    # a real conflict keeps the merge open
    ours.insert(25, "y = old_name(2)")         # ours adds a call far from the rename
    theirs = list(base)
    theirs[14] = "x15 = theirs"
    theirs[0] = "def new_name(a):"             # theirs renames the function
    sb = make_repo(tmp_path / "r", capsys, base=base, ours=ours, theirs=theirs)
    adapted = list(theirs)
    adapted[14] = "x15 = ours theirs"
    adapted.insert(25, "y = new_name(2)")
    code, rep = verify(sb, adapted)
    assert code == 2 and rep["errors"] == []
    (warn,) = rep["warnings"]
    assert warn["kind"] == "addition_modified" and warn["text"] == "y = old_name(2)"
    assert warn["found"] == "y = new_name(2)" and warn["result_line"] == 26
    # without the adapted line the same addition is an error
    del adapted[25]
    code, rep = verify(sb, adapted)
    assert code == 1 and rep["errors"][0]["kind"] == "addition_dropped"


def test_new_line_outside_judged_hunks_is_flagged(conflict):
    invented = list(conflict.good)
    invented.insert(8, "a line nobody wrote")
    code, rep = verify(conflict, invented)
    assert code == 2
    assert [(w["kind"], w["text"]) for w in rep["warnings"]] == [("new_line_introduced", "a line nobody wrote")]


def test_ignore_whitespace_option(conflict):
    spaced = [x + "  " for x in conflict.good]
    code, rep = verify(conflict, spaced)
    assert code == 1
    code, rep = verify(conflict, spaced, "--ignore-whitespace")
    assert code == 0


def test_findings_file_lines(conflict, tmp_path):
    conflict.write("f.txt", conflict.theirs)
    out = tmp_path / "found.txt"
    code, _, _ = conflict.run("verify", "f.txt", "--findings-file", str(out))
    assert code == 1
    lines = out.read_text().splitlines()
    assert lines[0] == "deletion_reverted ours base:5"
    assert re.fullmatch(r"summary: errors=1 warnings=0 exit=1 receipt=cl1-[0-9a-f]{12}", lines[1])
    assert len(lines) == 2


def test_receipt_is_stable_and_tied_to_the_files(conflict, tmp_path):
    conflict.write("f.txt", conflict.theirs)
    _, out, _ = conflict.run("--json", "verify", "f.txt", "--findings-file", str(tmp_path / "a.txt"))
    receipt = json.loads(out)["receipt"]
    assert (tmp_path / "a.txt").read_text().splitlines()[-1].endswith(f" receipt={receipt}")
    # 같은 입력이면 언어가 달라도 같은 영수증
    _, out_ko, _ = conflict.run("--json", "--lang", "ko", "verify", "f.txt")
    assert json.loads(out_ko)["receipt"] == receipt
    # 결과 파일이 한 줄이라도 바뀌면 영수증도 바뀐다
    conflict.write("f.txt", conflict.good)
    _, out_good, _ = conflict.run("--json", "verify", "f.txt")
    assert json.loads(out_good)["receipt"] != receipt


def test_audit_receipt_format_and_inputs():
    one = cl.audit_receipt("verify", [b"a\n", None], ["summary"])
    assert re.fullmatch(r"cl1-[0-9a-f]{12}", one)
    assert one == cl.audit_receipt("verify", [b"a\n", None], ["summary"])
    assert one != cl.audit_receipt("verify", [b"a\n", b""], ["summary"])
    assert one != cl.audit_receipt("resolve-trivial", [b"a\n", None], ["summary"])
    assert one != cl.audit_receipt("verify", [b"a\n", None], ["summary", "x"])


def test_audit_function_is_pure():
    base = ["a", "b", "c"]
    found = cl.audit(base, ["a", "B", "c"], ["a", "b", "c", "d"], ["a", "B", "c", "d"])
    assert found == {"errors": [], "warnings": [], "info": []}
    lost = cl.audit(base, ["a", "B", "c"], ["a", "b", "c", "d"], ["a", "B", "c"])
    assert [f["kind"] for f in lost["errors"]] == ["addition_dropped"]


def test_line_moved_a_little_is_not_lost():
    base = list("abcdefgh")
    ours = base[:4] + ["NEW"] + base[4:]
    theirs = base[:7] + ["H"]
    moved = ["a", "b", "c", "d", "e", "NEW", "f", "g", "H"]
    assert cl.audit(base, ours, theirs, moved) == {"errors": [], "warnings": [], "info": []}
    far = ["a", "b", "c", "d", "e", "f", "g", "H", "x", "y", "z", "NEW"]
    assert [f["kind"] for f in cl.audit(base, ours, theirs, far)["errors"]] == ["addition_dropped"]


# --------------------------------------------------------------------------- F-1: the PASS line counts hunks, not notes

def test_pass_line_counts_one_judged_hunk_once(conflict):
    """One true conflict gives one INFO per side; the PASS line must still say 1 hunk (0.1.0 said 2)."""
    conflict.write("f.txt", conflict.good)
    code, out, _ = conflict.run("verify", "f.txt")
    assert code == 0
    assert out.count("judgement_hunk") == 2                       # two notes (ours, theirs) ...
    assert out.strip().splitlines()[-1] == "PASS: no lost changes found (1 hunk needs judgement)"   # ... one hunk
    _, ko, _ = conflict.run("--lang", "ko", "verify", "f.txt")
    assert ko.strip().splitlines()[-1] == "통과: 유실된 변경 없음 (판단이 필요한 덩어리 1개)"


def test_pass_line_counts_two_judged_hunks(tmp_path, run_cli):
    base = list("abcdefgh")
    ours = ["a", "B-main", "c", "d", "e", "f", "G-main", "h"]
    theirs = ["a", "B-feat", "c", "d", "e", "f", "G-feat", "h"]
    result = ["a", "B-main-feat", "c", "d", "e", "f", "G-main-feat", "h"]
    for name, lines in (("b", base), ("o", ours), ("t", theirs), ("r", result)):
        (tmp_path / name).write_text("".join(x + "\n" for x in lines))
    code, out, _ = run_cli("verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r")
    assert code == 0 and out.count("judgement_hunk") == 4
    assert out.strip().splitlines()[-1] == "PASS: no lost changes found (2 hunks need judgement)"


def test_pass_line_without_judged_hunks(tmp_path, run_cli):
    for name, lines in (("b", ["a", "b"]), ("o", ["a", "b", "o"]), ("t", ["t", "a", "b"]), ("r", ["t", "a", "b", "o"])):
        (tmp_path / name).write_text("".join(x + "\n" for x in lines))
    code, out, _ = run_cli("verify", "--base", "b", "--ours", "o", "--theirs", "t", "--result", "r")
    assert code == 0 and out.strip().splitlines()[-1] == "PASS: no lost changes found (0 hunks need judgement)"
