"""AC-5: English and Korean output, language precedence, identical keys."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import conflict_ledger as cl

SOURCE = Path(cl.__file__).read_text(encoding="utf-8")


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"{(\w+)}", text))


def test_message_tables_have_identical_keys_and_placeholders():
    assert set(cl.MESSAGES["en"]) == set(cl.MESSAGES["ko"])
    for key, en in cl.MESSAGES["en"].items():
        assert placeholders(en) == placeholders(cl.MESSAGES["ko"][key]), key


def test_every_message_key_used_in_code_exists():
    used = set(re.findall(r'\bt\("([\w.-]+)"', SOURCE)) | set(re.findall(r'InputError\(\s*"([\w.]+)"', SOURCE))
    used |= {f"kind.{k}" for k in ("addition_dropped", "deletion_reverted", "base_line_lost", "conflict_marker_left",
                                   "addition_modified", "side_discarded", "new_line_introduced", "multiple_merge_bases",
                                   "judgement_hunk", "file_skipped", "large_file")}
    assert used <= set(cl.MESSAGES["en"]), used - set(cl.MESSAGES["en"])


def test_korean_text_has_no_particle_placeholders_and_is_translated():
    for key, ko in cl.MESSAGES["ko"].items():
        assert not re.search(r"\((이|가|을|를|은|는|과|와|으로)\)|이\(가\)|을\(를\)", ko), key
    translated = [k for k in cl.MESSAGES["en"] if cl.MESSAGES["en"][k] != cl.MESSAGES["ko"][k]]
    assert len(translated) > 0.8 * len(cl.MESSAGES["en"])
    assert re.search(r"[가-힣]", cl.MESSAGES["ko"]["err.not_a_repo"])


@pytest.mark.parametrize("cli,env,expected", [
    (None, {}, "en"),
    ("ko", {"CONFLICT_LEDGER_LANG": "en"}, "ko"),
    ("en", {"CONFLICT_LEDGER_LANG": "ko", "LANG": "ko_KR.UTF-8"}, "en"),
    (None, {"CONFLICT_LEDGER_LANG": "ko", "LANG": "en_US.UTF-8"}, "ko"),
    (None, {"LC_ALL": "ko_KR.UTF-8", "LANG": "en_US.UTF-8"}, "ko"),
    (None, {"LC_ALL": "en_US.UTF-8", "LC_MESSAGES": "ko_KR.UTF-8"}, "en"),
    (None, {"LC_MESSAGES": "ko_KR.UTF-8", "LANG": "en_US.UTF-8"}, "ko"),
    (None, {"LANG": "ko_KR.UTF-8"}, "ko"),
    (None, {"LANG": "fr_FR.UTF-8"}, "en"),
    ("xx", {"LANG": "ko_KR.UTF-8"}, "ko"),                  # an invalid value falls through to the next source
    (None, {"CONFLICT_LEDGER_LANG": "zz", "LANG": "ko_KR.UTF-8"}, "ko"),
])
def test_language_precedence(cli, env, expected):
    assert cl.resolve_lang(cli, env) == expected


def test_peek_lang_never_prints_or_exits(capsys):
    assert cl.peek_lang(["--lang", "ko", "scan"]) == "ko"
    assert cl.peek_lang(["scan", "--lang=en"]) == "en"
    assert cl.peek_lang(["--lang"]) is None
    assert cl.peek_lang(["--bogus", "--lang", "zz"]) == "zz"
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_help_in_both_languages(run_cli):
    code, out_ko, _ = run_cli("--lang", "ko", "--help")
    assert code == 0 and "git 병합 충돌을 공통 조상 기준으로 분류" in out_ko and "종료 코드" in out_ko
    code, out_en, _ = run_cli("--lang", "en", "--help")
    assert code == 0 and "Classify git merge conflicts against the merge base" in out_en and "Exit codes" in out_en
    code, out_ko, _ = run_cli("verify", "--lang", "ko", "--help")
    assert "해결 결과를 공통 조상과 양쪽 변경에 대조해 감사한다" in out_ko


def test_text_output_follows_language(conflict):
    conflict.write("f.txt", conflict.theirs)
    _, en, _ = conflict.run("--lang", "en", "verify", "f.txt")
    _, ko, _ = conflict.run("--lang", "ko", "verify", "f.txt")
    assert "present again in the result" in en and "FAIL: 1 error(s)" in en
    assert "결과에 다시 있음" in ko and "실패: 오류 1개" in ko
    assert 'deletion_reverted ours base:5 "line 5"' in ko          # identifiers stay the same


def test_environment_variable_picks_language(conflict, monkeypatch):
    monkeypatch.setenv("CONFLICT_LEDGER_LANG", "ko")
    _, out, _ = conflict.run("status")
    assert "작업: merge" in out
    _, out, _ = conflict.run("--lang", "en", "status")
    assert "operation: merge" in out
    monkeypatch.delenv("CONFLICT_LEDGER_LANG")
    monkeypatch.setenv("LANG", "ko_KR.UTF-8")
    _, out, _ = conflict.run("status")
    assert "작업: merge" in out


def test_json_is_language_independent(conflict):
    conflict.write("f.txt", conflict.theirs)
    _, en, _ = conflict.run("--json", "--lang", "en", "verify", "f.txt")
    _, ko, _ = conflict.run("--json", "--lang", "ko", "verify", "f.txt")
    assert json.loads(en) == json.loads(ko)
    _, en, _ = conflict.run("--json", "--lang", "en", "scan")
    _, ko, _ = conflict.run("--json", "--lang", "ko", "scan")
    assert json.loads(en) == json.loads(ko)


def test_input_error_is_localized_in_the_cli(tmp_path, run_cli):
    code, _, err = run_cli("--lang", "ko", "--repo", str(tmp_path), "status")
    assert code == 3 and err.strip().startswith(f"git 저장소가 아님: {tmp_path}")
    code, _, err = run_cli("--lang", "en", "--repo", str(tmp_path), "status")
    assert err.strip().startswith(f"not a git repository: {tmp_path}; run inside")
    code, _, err = run_cli("--lang", "ko", "verify", "--base", "b")
    assert code == 3 and "함께 줘야 함" in err


def test_invalid_lang_value_reports_one_error(run_cli, capsys):
    code, _, err = run_cli("--lang", "xx", "status")
    assert code == 3
    assert len(re.findall(r"invalid choice", err)) == 1
    assert re.search(r"(error|오류).*--lang.*invalid choice.*xx", err)


def test_usage_error_in_korean(run_cli):
    code, _, err = run_cli("--lang", "ko", "verify", "--no-such-flag")
    assert code == 3 and "conflict_ledger.py: 오류: 알 수 없는 인자: --no-such-flag" in err
    assert err.startswith("사용법: ") and "usage:" not in err and "unrecognized" not in err
    code, _, err = run_cli("--lang", "en", "verify", "--no-such-flag")
    assert code == 3 and err.startswith("usage: ") and "conflict_ledger.py: error: unrecognized arguments: --no-such-flag" in err
    code, _, err = run_cli("--lang", "ko")
    assert code == 3 and "필요한 인자가 빠짐: command" in err
    code, _, err = run_cli("--lang", "ko", "--eol", "xx", "status")
    assert code == 3 and "인자 --eol: 허용되지 않는 값: 'xx'" in err


def test_help_headers_follow_language(run_cli):
    """argparse's own headers (usage, positional arguments, options) come from the message table too."""
    for args in (("--help",), ("verify", "--help"), ("status", "--help")):
        _, ko, _ = run_cli("--lang", "ko", *args)
        _, en, _ = run_cli("--lang", "en", *args)
        assert ko.startswith("사용법: conflict_ledger.py") and "\n옵션:\n" in ko
        assert "usage:" not in ko and "options:" not in ko and "positional arguments" not in ko
        assert en.startswith("usage: conflict_ledger.py") and "\noptions:\n" in en
    _, ko, _ = run_cli("--lang", "ko", "verify", "--help")
    _, en, _ = run_cli("--lang", "en", "verify", "--help")
    assert "\n위치 인자:\n" in ko and "\npositional arguments:\n" in en


@pytest.mark.parametrize("message", [
    "unrecognized arguments: --bogus",
    "the following arguments are required: command",
    "argument --lang: invalid choice: 'xx' (choose from 'en', 'ko')",
    "argument command: invalid choice: 'verfy', maybe you meant 'verify'? (choose from 'status', 'verify')",
    "argument --out: expected one argument",
    "argument --x: some message argparse may add later",
])
def test_argparse_messages_round_trip_in_english_and_translate_to_korean(message):
    assert cl.localize_usage_error(message, cl.Translator("en")) == message
    ko = cl.localize_usage_error(message, cl.Translator("ko"))
    assert ko != message
    if message.startswith("argument "):
        assert ko.startswith("인자 ")             # an unknown argparse text keeps its words but gets the Korean frame
    if "maybe you meant" in message:
        assert "혹시 'verify'?" in ko and "(고를 수 있는 값: 'status', 'verify')" in ko


def test_demo_output_follows_language(run_cli):
    code, out, _ = run_cli("--lang", "ko", "demo")
    assert code == 0 and "데모:" in out and out.strip().splitlines()[-1] == "demo: audit caught the dropped change"
