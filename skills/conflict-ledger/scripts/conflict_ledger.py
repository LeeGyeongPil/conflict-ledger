#!/usr/bin/env python3
"""conflict_ledger - classify git 3-way merge conflicts against the merge base and
audit that a resolution kept every change from both sides.

Subcommands: status, scan, resolve-trivial, verify, demo.
Standard library only (Python 3.10+) plus the git command line.
Exit codes: 0 pass, 1 error finding, 2 warnings only, 3 input problem.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

__version__ = "0.1.4"

EXIT_OK, EXIT_ERROR, EXIT_WARN, EXIT_INPUT = 0, 1, 2, 3
SIDES = ("ours", "theirs")
AUTO_CLASSES = ("ours_only", "theirs_only", "both_same", "whitespace_only")
JUDGE_CLASSES = ("true_conflict", "both_added_same_point")
SIMILAR_THRESHOLD = 0.6       # an added line this close to a result line counts as "modified"
DISCARD_THRESHOLD = 0.5       # one side counts as discarded below this similarity
EXCERPT_LINES = 6
EXCERPT_WIDTH = 120
LARGE_FILE_LINES = 20000
GIT_TIMEOUT = 120
SNAPSHOT_DIR = "conflict-ledger"
RECEIPT_PREFIX = "cl1-"       # audit receipt: "cl1-" + first 12 hex digits of a sha256 fingerprint
RECEIPT_HEX = 12

# --------------------------------------------------------------------------- messages
# All user-visible text lives here. The en and ko tables must have identical keys.
MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "prog_description": "Classify git merge conflicts against the merge base and audit that a resolution kept both sides' changes.",
        "epilog": "Exit codes: 0 pass, 1 error finding (a change was lost or a marker is left), 2 warnings only, 3 input problem.",
        "cmd_status": "show the operation in progress and what stage 2 and stage 3 mean",
        "cmd_scan": "classify the hunks of every unmerged file and save a snapshot",
        "cmd_resolve": "resolve only the provably trivial hunks, leave real conflicts as diff3 markers",
        "cmd_verify": "audit a resolution against the base and both sides",
        "cmd_demo": "run a self-contained demo in a throwaway git repository",
        "opt_repo": "repository folder (default: current folder)",
        "opt_json": "print machine-readable JSON",
        "opt_lang": "output language: en or ko (default: CONFLICT_LEDGER_LANG, then LC_ALL/LC_MESSAGES/LANG)",
        "opt_eol": "line ending handling: normalize ignores CRLF and BOM (default), keep compares exact bytes",
        "opt_version": "show the version and exit",
        "opt_help": "show this help message and exit",
        "opt_paths": "files to check (default: every unmerged file)",
        "opt_paths_verify": "files to check (default: unmerged files, files with a scan snapshot and, during a merge, rebase, cherry-pick or revert, every file both sides changed, so staged resolutions are found too)",
        "opt_base": "file mode: common ancestor file",
        "opt_ours": "file mode: our version of the file",
        "opt_theirs": "file mode: their version of the file",
        "opt_result": "file mode: the resolved file to audit",
        "opt_name": "file mode: display name for the file (default: the --result file for verify, the --out file for resolve-trivial, else the --ours file)",
        "opt_operation": "file mode: which operation decides the side labels",
        "opt_out": "write the partly resolved file here (file mode default: print it; in a repository: instead of the working file, one PATH only)",
        "opt_dry_run": "report what would be applied without writing files",
        "opt_whitespace": "which side wins a whitespace-only hunk (default: ours)",
        "opt_commit": "audit an existing two-parent merge commit",
        "opt_ignore_ws": "treat lines that differ only in whitespace as equal",
        "opt_keep": "keep the throwaway repository and print its path",
        "opt_findings": "also write short machine-friendly result lines to this file, ending with a summary line and the audit receipt",
        "err.git_missing": "git was not found on PATH; install git 2.35 or newer",
        "err.not_a_repo": "not a git repository: {path}; run inside a repository, pass --repo PATH, or use --base/--ours/--theirs/--result",
        "err.skill_folder": "this is the conflict-ledger skill's own folder, not the repository to check: {path}; run the script by its full path from the repository, or pass --repo PATH",
        "err.no_conflict": "no conflict in progress and no snapshot for {path}; run scan while the conflict is still open, or pass --base/--ours/--theirs",
        "err.no_unmerged": "no unmerged files in {path}; pass file paths or use file mode",
        "err.nothing_to_audit": "{op} in progress, but nothing to audit for {path}: no unmerged file, no snapshot and no file changed on both sides; pass PATH to audit a file, or --base/--ours/--theirs/--result",
        "err.binary": "binary file, line audit skipped: {path}; compare it by hand or pass text files",
        "err.not_two_parent": "commit {sha} is not a two-parent merge",
        "err.bad_commit": "cannot resolve commit {sha}",
        "err.no_merge_base": "the parents of {sha} have no merge base",
        "err.file_missing": "file not found: {path}; check the path (relative paths are read from the current folder)",
        "err.triple_together": "--base/--ours/--theirs must be given together",
        "err.result_required": "file mode verify needs --result FILE",
        "err.outside_repo": "path is outside the repository: {path}",
        "err.not_text": "only text files can be resolved ({kind}): {path}",
        "err.usage": "{prog}: error: {message}",
        "err.os_error": "cannot read or write {path}: {reason}",
        "label.ours": "ours",
        "label.theirs": "theirs",
        "label.both": "both sides",
        "label.HEAD": "HEAD (current branch)",
        "label.MERGE_HEAD": "MERGE_HEAD (branch being merged in)",
        "label.upstream": "upstream (branch being rebased onto)",
        "label.my-commit": "your commit (being replayed)",
        "label.CHERRY_PICK_HEAD": "CHERRY_PICK_HEAD (commit being picked)",
        "label.REVERT_HEAD": "REVERT_HEAD (inverse of the reverted commit)",
        "label.patch": "patch (being applied)",
        "label.parent1": "first parent",
        "label.parent2": "second parent",
        "side.file": "file",
        "side.result": "result",
        "st.operation": "operation: {op}",
        "st.stage2": "stage 2 (ours) = {label}",
        "st.stage3": "stage 3 (theirs) = {label}",
        "st.my_commit_side": "my-commit-side: {side}",
        "st.unmerged": "unmerged files ({count}):",
        "st.none_unmerged": "no unmerged files",
        "st.snapshots": "snapshots saved for: {paths}",
        "st.no_snapshots": "no snapshots saved",
        "st.to_audit": "no unmerged files left; verify without PATH audits the files both sides changed: {paths}",
        "st.deleted": "gone from the working tree although no side deleted them; verify without PATH audits them as lost changes: {paths}",
        "scan.file": "{path}: {kind}, {count} hunks",
        "scan.file_one": "{path}: {kind}, {count} hunk",
        "scan.cross": "git merge-file reports {count} conflicts for comparison",
        "scan.cross_one": "git merge-file reports {count} conflict for comparison",
        "scan.auto": "auto",
        "scan.deleted": "(deleted)",
        "res.edited": "{path}: {reason}; refusing to overwrite it so hand edits are not lost (nothing was written). Finish it by hand and run verify, write the machine resolution elsewhere with --out FILE, or pass --force to overwrite and discard the edits",
        "res.reason_resolved": "no conflict markers left, so it looks resolved by hand",
        "res.reason_edited": "it differs from the unresolved conflict of these three versions (a block was resolved, or lines were added or changed)",
        "opt_force": "overwrite the target even if it holds hand edits (they are discarded)",
        "err.out_one_path": "--out takes exactly one PATH inside a repository",
        "ap.usage": "usage: ",
        "ap.positionals": "positional arguments",
        "ap.options": "options",
        "ap.argument": "argument {name}: {message}",
        "ap.unrecognized": "unrecognized arguments: {args}",
        "ap.required": "the following arguments are required: {args}",
        "ap.invalid_choice": "invalid choice: {value} (choose from {choices})",
        "ap.invalid_choice_hint": "invalid choice: {value}, maybe you meant {closest}? (choose from {choices})",
        "ap.expected_one": "expected one argument",
        "scan.judge": "judge",
        "scan.snapshot": "snapshot saved under {path}",
        "scan.skipped": "line classification skipped for {kind} files",
        "scan.hunk": "{id} {cls} {span} [{mode}] ours: {ours} | theirs: {theirs}",
        "span.line": "base L{n}",
        "span.range": "base L{a}-{b}",
        "span.after": "base after L{n}",
        "span.start": "base at file start",
        "res.file": "{path}: applied {applied_n} hunks ({applied}); {remaining_n} remaining ({remaining})",
        "res.file_one": "{path}: applied {applied_n} hunk ({applied}); {remaining_n} remaining ({remaining})",
        "res.dry": "dry run: nothing was written",
        "res.written": "written: {path} (the index was not changed)",
        "res.remaining": "remaining_true_conflicts: {count}",
        "ver.header": "verify {path} (source: {source})",
        "ver.pass": "PASS: no lost changes found ({info} hunks need judgement)",
        "ver.pass_one": "PASS: no lost changes found ({info} hunk needs judgement)",
        "ver.warn": "WARNINGS: {count} items need a human look",
        "ver.fail": "FAIL: {count} error(s), {warn} warning(s)",
        "ver.operation": "operation: {op}; ours = {ours}; theirs = {theirs}",
        "kind.addition_dropped": "added by {side_label} but missing from the result",
        "kind.deletion_reverted": "removed by {side_label} but present again in the result",
        "kind.base_line_lost": "unchanged on both sides but missing or altered in the result",
        "kind.conflict_marker_left": "conflict marker left in the result",
        "kind.addition_modified": "added by {side_label}; the result has a similar but different line: {found}",
        "kind.side_discarded": "the result keeps nothing close to the change from {side_label}",
        "kind.new_line_introduced": "line added by neither side",
        "kind.multiple_merge_bases": "{count} merge bases found; the first one was used",
        "kind.judgement_hunk": "hunk {hunk} needs judgement; closest line to {side_label}: {similarity}",
        "kind.file_skipped": "{reason} file: line audit skipped",
        "kind.large_file": "large file (largest version: {lines} lines); the audit may be slow",
        "kind.rename_followed": "renamed from {old} by {side_label}; audited against the base version of {old}",
        "demo.start": "demo: building a throwaway repository in {path} (real git, real conflict)",
        "demo.merge": "demo: git merge feature -> conflict in f.txt",
        "demo.scan": "demo: scan found {count} hunks",
        "demo.try": "demo: resolution {name}",
        "demo.a": "A - take ours wholesale",
        "demo.b": "B - take theirs wholesale",
        "demo.c": "C - combine both sides by hand",
        "demo.exit": "  -> verify exit code {code}",
        "demo.kept": "demo: repository kept at {path}",
        "demo.ok": "demo: audit caught the dropped change",
        "demo.bad": "demo: unexpected result, the audit did not behave as designed",
    },
    "ko": {
        "prog_description": "git 병합 충돌을 공통 조상 기준으로 분류하고, 해결 결과가 양쪽 변경을 모두 남겼는지 감사한다.",
        "epilog": "종료 코드: 0 통과, 1 오류(변경 유실 또는 표식 남음), 2 경고만, 3 입력 문제.",
        "cmd_status": "진행 중인 작업과 스테이지 2·3 의 의미를 보여 준다",
        "cmd_scan": "병합되지 않은 파일의 덩어리를 분류하고 스냅샷을 저장한다",
        "cmd_resolve": "증명 가능한 사소한 덩어리만 풀고 진짜 충돌은 diff3 표식으로 남긴다",
        "cmd_verify": "해결 결과를 공통 조상과 양쪽 변경에 대조해 감사한다",
        "cmd_demo": "임시 git 저장소에서 처음부터 끝까지 시연한다",
        "opt_repo": "저장소 폴더 (기본: 현재 폴더)",
        "opt_json": "기계가 읽는 JSON 으로 출력한다",
        "opt_lang": "출력 언어: en 또는 ko (기본: CONFLICT_LEDGER_LANG, 그다음 LC_ALL/LC_MESSAGES/LANG)",
        "opt_eol": "줄 끝 처리: normalize 는 CRLF 와 BOM 을 무시(기본), keep 은 바이트 그대로 비교",
        "opt_version": "버전을 보여 주고 끝낸다",
        "opt_help": "도움말을 보여 주고 끝낸다",
        "opt_paths": "검사할 파일 (기본: 병합되지 않은 모든 파일)",
        "opt_paths_verify": "검사할 파일 (기본: 병합되지 않은 파일, scan 스냅샷이 있는 파일, 그리고 병합·리베이스·체리픽·되돌리기 중이면 양쪽이 모두 바꾼 파일. 그래서 git add 한 해결도 찾음)",
        "opt_base": "파일 모드: 공통 조상 파일",
        "opt_ours": "파일 모드: 우리 쪽 파일",
        "opt_theirs": "파일 모드: 상대 쪽 파일",
        "opt_result": "파일 모드: 감사할 해결 결과 파일",
        "opt_name": "파일 모드: 파일에 붙일 표시 이름 (기본값: verify 는 --result 파일, resolve-trivial 은 --out 파일, 그 밖에는 --ours 파일)",
        "opt_operation": "파일 모드: 쪽 라벨을 정하는 작업 종류",
        "opt_out": "일부만 해결한 파일을 쓸 위치 (파일 모드 기본: 화면에 출력, 저장소 안에서는 작업 파일 대신 여기에 씀, PATH 하나만)",
        "opt_dry_run": "파일을 쓰지 않고 적용될 내용만 보고한다",
        "opt_whitespace": "공백만 다른 덩어리에서 이기는 쪽 (기본: ours)",
        "opt_commit": "이미 만들어진 부모 2개짜리 병합 커밋을 감사한다",
        "opt_ignore_ws": "공백만 다른 줄은 같은 줄로 본다",
        "opt_keep": "임시 저장소를 지우지 않고 경로를 알려 준다",
        "opt_findings": "짧은 기계용 결과 줄을 이 파일에도 쓴다(마지막 줄은 합계와 감사 영수증)",
        "err.git_missing": "PATH 에서 git 을 찾을 수 없음, git 2.35 이상을 설치할 것",
        "err.not_a_repo": "git 저장소가 아님: {path}, 저장소 안에서 실행하거나 --repo PATH 또는 --base/--ours/--theirs/--result 를 줄 것",
        "err.skill_folder": "conflict-ledger 스킬 자체 폴더이지 검사할 저장소가 아님: {path}, 저장소에서 스크립트를 전체 경로로 실행하거나 --repo PATH 를 줄 것",
        "err.no_conflict": "진행 중인 충돌도 스냅샷도 없음: {path}, 충돌이 열려 있을 때 scan 을 먼저 돌리거나 --base/--ours/--theirs 를 줄 것",
        "err.no_unmerged": "병합되지 않은 파일이 없음: {path}, 파일 경로를 주거나 파일 모드를 쓸 것",
        "err.nothing_to_audit": "{op} 작업이 진행 중이지만 감사할 대상을 찾지 못함: {path}, 병합되지 않은 파일·스냅샷·양쪽이 모두 바꾼 파일이 없음, 감사할 PATH 나 --base/--ours/--theirs/--result 를 줄 것",
        "err.binary": "바이너리 파일, 줄 감사 건너뜀: {path}, 텍스트 파일을 주거나 직접 비교할 것",
        "err.not_two_parent": "커밋 {sha} 은 부모가 2개인 병합 커밋이 아님",
        "err.bad_commit": "커밋을 찾을 수 없음: {sha}",
        "err.no_merge_base": "커밋 {sha} 의 두 부모에 공통 조상이 없음",
        "err.file_missing": "파일을 찾을 수 없음: {path}, 경로를 확인할 것(상대 경로는 현재 폴더 기준)",
        "err.triple_together": "--base/--ours/--theirs 는 함께 줘야 함",
        "err.result_required": "파일 모드 verify 는 --result 파일이 필요함",
        "err.outside_repo": "저장소 밖의 경로: {path}",
        "err.not_text": "텍스트 파일만 해결할 수 있음 ({kind}): {path}",
        "err.usage": "{prog}: 오류: {message}",
        "err.os_error": "읽거나 쓸 수 없음: {path}: {reason}",
        "label.ours": "ours",
        "label.theirs": "theirs",
        "label.both": "양쪽",
        "label.HEAD": "HEAD (현재 브랜치)",
        "label.MERGE_HEAD": "MERGE_HEAD (합쳐 들어오는 브랜치)",
        "label.upstream": "upstream (리베이스 대상 브랜치)",
        "label.my-commit": "내 커밋 (다시 적용 중)",
        "label.CHERRY_PICK_HEAD": "CHERRY_PICK_HEAD (가져오는 커밋)",
        "label.REVERT_HEAD": "REVERT_HEAD (되돌리는 커밋의 역변경)",
        "label.patch": "패치 (적용 중)",
        "label.parent1": "첫째 부모",
        "label.parent2": "둘째 부모",
        "side.file": "파일",
        "side.result": "결과",
        "st.operation": "작업: {op}",
        "st.stage2": "스테이지 2 (ours) = {label}",
        "st.stage3": "스테이지 3 (theirs) = {label}",
        "st.my_commit_side": "my-commit-side: {side}",
        "st.unmerged": "병합되지 않은 파일 ({count}개):",
        "st.none_unmerged": "병합되지 않은 파일 없음",
        "st.snapshots": "스냅샷 저장됨: {paths}",
        "st.no_snapshots": "저장된 스냅샷 없음",
        "st.to_audit": "병합되지 않은 파일이 남지 않음, PATH 없는 verify 가 양쪽이 모두 바꾼 파일을 감사함: {paths}",
        "st.deleted": "어느 쪽도 지우지 않았는데 작업 폴더에서 사라진 파일, PATH 없는 verify 가 잃은 변경으로 감사함: {paths}",
        "scan.file": "{path}: {kind}, 덩어리 {count}개",
        "scan.file_one": "{path}: {kind}, 덩어리 {count}개",
        "scan.cross": "비교용 git merge-file 충돌 수: {count}",
        "scan.cross_one": "비교용 git merge-file 충돌 수: {count}",
        "scan.auto": "자동",
        "scan.deleted": "(삭제됨)",
        "res.edited": "{path}: {reason}. 손으로 고친 내용을 잃지 않도록 덮어쓰지 않음(아무것도 쓰지 않음). 손으로 마저 고친 뒤 verify 로 검사하거나, --out FILE 로 기계 해결본을 다른 곳에 쓰거나, 고친 내용을 버리고 덮어쓰려면 --force 를 줄 것",
        "res.reason_resolved": "충돌 표식이 하나도 없어 직접 해결한 파일로 보임",
        "res.reason_edited": "세 버전의 미해결 충돌 상태와 다름(덩어리를 풀었거나 줄을 추가·수정함)",
        "opt_force": "손으로 고친 내용이 있어도 덮어쓴다(고친 내용은 사라짐)",
        "err.out_one_path": "저장소 안에서 --out 은 PATH 하나에만 쓸 수 있음",
        "ap.usage": "사용법: ",
        "ap.positionals": "위치 인자",
        "ap.options": "옵션",
        "ap.argument": "인자 {name}: {message}",
        "ap.unrecognized": "알 수 없는 인자: {args}",
        "ap.required": "필요한 인자가 빠짐: {args}",
        "ap.invalid_choice": "허용되지 않는 값: {value} (고를 수 있는 값: {choices})",
        "ap.invalid_choice_hint": "허용되지 않는 값: {value}, 혹시 {closest}? (고를 수 있는 값: {choices})",
        "ap.expected_one": "값이 하나 필요함",
        "scan.judge": "판단",
        "scan.snapshot": "스냅샷 저장 위치: {path}",
        "scan.skipped": "{kind} 파일은 줄 분류를 건너뜀",
        "scan.hunk": "{id} {cls} {span} [{mode}] ours: {ours} | theirs: {theirs}",
        "span.line": "base {n}행",
        "span.range": "base {a}-{b}행",
        "span.after": "base {n}행 뒤",
        "span.start": "base 파일 맨 앞",
        "res.file": "{path}: 덩어리 {applied_n}개를 적용했고 {remaining_n}개가 남았습니다. 적용: {applied} / 남음: {remaining}",
        "res.file_one": "{path}: 덩어리 {applied_n}개를 적용했고 {remaining_n}개가 남았습니다. 적용: {applied} / 남음: {remaining}",
        "res.dry": "시험 실행: 아무것도 쓰지 않음",
        "res.written": "저장함: {path} (인덱스는 바꾸지 않음)",
        "res.remaining": "remaining_true_conflicts: {count}",
        "ver.header": "verify {path} (입력원: {source})",
        "ver.pass": "통과: 유실된 변경 없음 (판단이 필요한 덩어리 {info}개)",
        "ver.pass_one": "통과: 유실된 변경 없음 (판단이 필요한 덩어리 {info}개)",
        "ver.warn": "경고: 사람이 확인할 항목 {count}개",
        "ver.fail": "실패: 오류 {count}개, 경고 {warn}개",
        "ver.operation": "작업: {op}, ours = {ours}, theirs = {theirs}",
        "kind.addition_dropped": "{side_label} 쪽에서 추가했지만 결과에 없음",
        "kind.deletion_reverted": "{side_label} 쪽에서 지웠지만 결과에 다시 있음",
        "kind.base_line_lost": "양쪽 모두 건드리지 않았는데 결과에서 사라졌거나 바뀜",
        "kind.conflict_marker_left": "결과에 충돌 표식이 남아 있음",
        "kind.addition_modified": "{side_label} 쪽에서 추가한 줄이 결과에서 비슷하지만 다른 줄이 됨: {found}",
        "kind.side_discarded": "결과에 {side_label} 변경과 비슷한 줄이 하나도 없음",
        "kind.new_line_introduced": "어느 쪽도 추가하지 않은 줄",
        "kind.multiple_merge_bases": "공통 조상이 {count}개, 첫 번째를 사용함",
        "kind.judgement_hunk": "덩어리 {hunk}: 판단 필요, 가장 비슷한 줄 ({side_label}): {similarity}",
        "kind.file_skipped": "{reason} 파일, 줄 감사 건너뜀",
        "kind.large_file": "큰 파일 (가장 긴 버전 {lines}줄), 감사가 느릴 수 있음",
        "kind.rename_followed": "{side_label} 쪽이 {old} 에서 이름을 바꿈, {old} 의 base 버전과 비교해 감사함",
        "demo.start": "데모: {path} 에 임시 저장소를 만든다 (진짜 git, 진짜 충돌)",
        "demo.merge": "데모: git merge feature -> f.txt 충돌",
        "demo.scan": "데모: scan 이 덩어리 {count}개를 찾음",
        "demo.try": "데모: 해결 {name}",
        "demo.a": "A - ours 를 통째로 선택",
        "demo.b": "B - theirs 를 통째로 선택",
        "demo.c": "C - 양쪽을 손으로 합침",
        "demo.exit": "  -> verify 종료 코드 {code}",
        "demo.kept": "데모: 저장소를 남겨 둠: {path}",
        "demo.ok": "demo: audit caught the dropped change",
        "demo.bad": "데모: 예상과 다른 결과, 감사가 설계대로 동작하지 않음",
    },
}
# The final demo line is the machine-checkable success marker, so it stays identical in both languages.
MESSAGES["ko"]["demo.ok"] = MESSAGES["en"]["demo.ok"]


class InputError(Exception):
    """Bad input. Carries a message key so the CLI can localize it; str() is English."""

    def __init__(self, key: str, **params: object) -> None:
        self.key = key
        self.params = params
        super().__init__(MESSAGES["en"][key].format(**params))

    @property
    def kind(self) -> str:
        """Language-independent problem name for the JSON / findings ledger: the message key without its prefix
        (err.binary -> binary). A non-text file refused by resolve-trivial is named by its file kind (binary, ...)."""
        if self.key == "err.not_text":
            return str(self.params["kind"])
        return self.key.split(".", 1)[1]

    @property
    def path(self) -> str | None:
        """The path the problem is about, exactly as the user gave it (None when the problem is not about a path)."""
        value = self.params.get("path")
        return None if value is None else str(value)


def normalize_lang(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    if v.startswith("ko"):
        return "ko"
    if v.startswith("en"):
        return "en"
    return None


def resolve_lang(cli_value: str | None, environ: dict[str, str] | None = None) -> str:
    """--lang > CONFLICT_LEDGER_LANG > LC_ALL > LC_MESSAGES > LANG; anything that is not Korean is English."""
    env = os.environ if environ is None else environ
    for value in (cli_value, env.get("CONFLICT_LEDGER_LANG")):
        found = normalize_lang(value)
        if found:
            return found
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = env.get(key)
        if value:
            return "ko" if value.lower().startswith("ko") else "en"
    return "en"


class Translator:
    def __init__(self, lang: str) -> None:
        self.lang = lang if lang in MESSAGES else "en"

    def __call__(self, key: str, **params: object) -> str:
        return MESSAGES[self.lang][key].format(**params)

    def label(self, label_id: str) -> str:
        return self(f"label.{label_id}")


# --------------------------------------------------------------------------- text and diff helpers

def is_binary(data: bytes) -> bool:
    if b"\0" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def split_lines(data: bytes, eol: str = "normalize") -> list[str]:
    text = data.decode("utf-8")
    if eol == "normalize":
        if text.startswith("\ufeff"):
            text = text[1:]
        text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def norm_ws(lines: list[str]) -> list[str]:
    return [" ".join(x.split()) for x in lines]


def change_regions(base: list[str], other: list[str]) -> list[tuple[int, int, int, int]]:
    """Non-equal opcodes of base -> other as (base_start, base_end, other_start, other_end)."""
    sm = difflib.SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, j1, j2) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def side_text(base: list[str], regions: list[tuple], other: list[str], lo: int, hi: int) -> list[str]:
    """What one side has for base[lo:hi] once its regions inside that span are applied."""
    out: list[str] = []
    pos = lo
    for _side, i1, i2, j1, j2 in regions:
        out.extend(base[pos:i1])
        out.extend(other[j1:j2])
        pos = i2
    out.extend(base[pos:hi])
    return out


def build_hunks(base: list[str], ours: list[str], theirs: list[str]) -> list[dict]:
    """Group the changes of both sides into hunks and classify each one."""
    regions = [("ours", *r) for r in change_regions(base, ours)] + [("theirs", *r) for r in change_regions(base, theirs)]
    regions.sort(key=lambda r: (r[1], r[2], r[0]))
    groups: list[dict] = []
    for r in regions:
        if groups and r[1] <= groups[-1]["hi"]:      # touching or overlapping changes conflict, like git
            groups[-1]["regions"].append(r)
            groups[-1]["hi"] = max(groups[-1]["hi"], r[2])
        else:
            groups.append({"lo": r[1], "hi": r[2], "regions": [r]})
    hunks = []
    for n, g in enumerate(groups, 1):
        lo, hi = g["lo"], g["hi"]
        text = {}
        changed_by = []
        for side, other in (("ours", ours), ("theirs", theirs)):
            mine = [r for r in g["regions"] if r[0] == side]
            if mine:
                changed_by.append(side)
            text[side] = side_text(base, mine, other, lo, hi) if mine else list(base[lo:hi])
        if len(changed_by) == 1:
            cls = f"{changed_by[0]}_only"
        elif text["ours"] == text["theirs"]:
            cls = "both_same"
        elif norm_ws(text["ours"]) == norm_ws(text["theirs"]):
            cls = "whitespace_only"
        elif lo == hi:
            cls = "both_added_same_point"
        else:
            cls = "true_conflict"
        hunks.append({"id": f"H{n}", "class": cls, "lo": lo, "hi": hi, "ours": text["ours"], "theirs": text["theirs"],
                      "changed_by": changed_by, "regions": g["regions"]})
    return hunks


def excerpt(lines: list[str]) -> list[str]:
    out = [x if len(x) <= EXCERPT_WIDTH else x[:EXCERPT_WIDTH - 1] + "…" for x in lines[:EXCERPT_LINES]]
    if len(lines) > EXCERPT_LINES:
        out.append(f"… (+{len(lines) - EXCERPT_LINES})")
    return out


def hunk_json(h: dict) -> dict:
    return {"id": h["id"], "class": h["class"], "base": [h["lo"], h["hi"]], "changed_by": h["changed_by"],
            "ours_excerpt": excerpt(h["ours"]), "theirs_excerpt": excerpt(h["theirs"]),
            "auto_resolvable": h["class"] in AUTO_CLASSES}


DEFAULT_MARKER_LABELS = {"ours": "ours", "base": "base", "theirs": "theirs"}


def render_resolution(base: list[str], hunks: list[dict], whitespace: str = "ours",
                      labels: dict[str, str] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Resolve provably trivial hunks; leave the rest as diff3 conflict blocks.

    labels names the three marker lines (default ours / base / theirs; in a repository the labels git wrote)."""
    names = labels or DEFAULT_MARKER_LABELS
    out: list[str] = []
    applied: list[str] = []
    remaining: list[str] = []
    pos = 0
    for h in hunks:
        out.extend(base[pos:h["lo"]])
        cls = h["class"]
        if cls in AUTO_CLASSES:
            pick = {"ours_only": "ours", "theirs_only": "theirs", "both_same": "ours", "whitespace_only": whitespace}[cls]
            out.extend(h[pick])
            applied.append(h["id"])
        else:
            out.append(f"<<<<<<< {names['ours']}")
            out.extend(h["ours"])
            out.append(f"||||||| {names['base']}")
            out.extend(base[h["lo"]:h["hi"]])
            out.append("=======")
            out.extend(h["theirs"])
            out.append(f">>>>>>> {names['theirs']}")
            remaining.append(h["id"])
        pos = h["hi"]
    out.extend(base[pos:])
    return out, applied, remaining


# --------------------------------------------------------------------------- the audit

def find_markers(result: list[str], base: list[str]) -> list[int]:
    """1-based line numbers of conflict blocks: an opening and a closing marker line must pair up."""
    known = set(base)
    found: list[int] = []
    i = 0
    while i < len(result):
        if re.match(r"<{7}( |$)", result[i]):
            for j in range(i + 1, len(result)):
                if re.match(r">{7}( |$)", result[j]):
                    if not (result[i] in known and result[j] in known):
                        found.append(i + 1)
                    i = j
                    break
        i += 1
    return found


def missing_lines(expected: list[str], have: list[str]) -> list[tuple[int, str]]:
    """Lines of expected that the multiset have cannot supply, with their index in expected."""
    pool = Counter(have)
    out = []
    for k, line in enumerate(expected):
        if pool[line] > 0:
            pool[line] -= 1
        else:
            out.append((k, line))
    return out


def best_ratio(line: str, candidates: list[tuple[int, str]]) -> tuple[float, int | None, str | None]:
    s = line.strip()
    best: tuple[float, int | None, str | None] = (0.0, None, None)
    if not s:
        return best
    for idx, cand in candidates:
        c = cand.strip()
        if not c:
            continue
        m = difflib.SequenceMatcher(None, s, c)
        if m.real_quick_ratio() <= best[0] or m.quick_ratio() <= best[0]:
            continue
        ratio = m.ratio()
        if ratio > best[0]:
            best = (ratio, idx, cand)
    return best


def audit(base: list[str], ours: list[str], theirs: list[str], result: list[str],
          result_deleted: bool = False) -> dict[str, list[dict]]:
    """Check that the result kept every change both sides made to the base.

    Errors are only reported where the expected outcome is provable. Hunks that need judgement
    produce info items, and a side whose change left no trace becomes a warning.
    result_deleted: the resolution deleted the file although no side deleted it. Then nothing of
    any hunk survived, so a judged hunk's added lines are provably lost too (errors, not notes).
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    info: list[dict] = []
    hunks = build_hunks(base, ours, theirs)
    sm = difflib.SequenceMatcher(None, base, result, autojunk=False)
    kept: dict[int, int] = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            kept[a + k] = b + k

    def span(lo: int, hi: int) -> tuple[int, int]:
        p = lo - 1
        while p >= 0 and p not in kept:
            p -= 1
        start = kept[p] + 1 if p >= 0 else 0
        n = hi
        while n < len(base) and n not in kept:
            n += 1
        end = kept[n] if n < len(base) else len(result)
        return start, max(start, end)

    for line_no in find_markers(result, base):
        errors.append({"kind": "conflict_marker_left", "side": None, "result_line": line_no, "text": result[line_no - 1]})

    modified_result_lines: set[int] = set()
    covered: set[int] = set()
    for h in hunks:
        lo, hi, cls = h["lo"], h["hi"], h["class"]
        covered.update(range(lo, hi))
        start, end = span(lo, hi)
        window = result[start:end]
        if cls in JUDGE_CLASSES and result_deleted:
            # 파일이 통째로 지워졌다: 판단할 거리가 없다. 각 쪽이 이 덩어리에 더한 줄이 전부 사라졌다.
            for side in SIDES:
                mine = [r for r in h["regions"] if r[0] == side]
                for line in [x for x in h[side] if x not in base[lo:hi]]:
                    # 이 쪽 파일에서의 줄 번호: 덩어리 시작(lo)이 그 쪽 파일의 몇째 줄인지 + 덩어리 안 위치
                    side_line = mine[0][3] - (mine[0][1] - lo) + h[side].index(line) + 1 if len(mine) == 1 else None
                    errors.append({"kind": "addition_dropped", "side": side, "base_line": lo + 1,
                                   "side_line": side_line, "text": line})
            continue
        if cls in JUDGE_CLASSES:
            sims: dict[str, float | None] = {}
            for side in SIDES:
                sims[side] = _side_similarity(h[side], window)
                info.append({"kind": "judgement_hunk", "side": side, "hunk": h["id"], "base_line": lo + 1,
                             "similarity": None if sims[side] is None else round(sims[side], 2)})
            for side, other in (("ours", "theirs"), ("theirs", "ours")):
                mine, theirs_sim = sims[side], sims[other]
                if mine is None or theirs_sim is None:
                    continue
                if mine < DISCARD_THRESHOLD and (theirs_sim >= 0.999 or theirs_sim < DISCARD_THRESHOLD):
                    warnings.append({"kind": "side_discarded", "side": side, "hunk": h["id"], "base_line": lo + 1,
                                     "text": h[side][0] if h[side] else ""})
            continue
        side = "both" if len(h["changed_by"]) == 2 else h["changed_by"][0]
        sol = h["ours"] if side in ("ours", "both") else h["theirs"]
        first_region = next(r for r in h["regions"] if r[0] == ("theirs" if side == "theirs" else "ours"))
        single = len([r for r in h["regions"] if r[0] == first_region[0]]) == 1
        if cls == "whitespace_only":
            expected = [x for x in norm_ws(sol) if x not in norm_ws(base[lo:hi])]
            have = norm_ws(window)
        else:
            expected = [x for x in sol if x not in base[lo:hi]]
            have = window
        first = max(0, start - 3)
        nearby = Counter(norm_ws(result[first:end + 3]) if cls == "whitespace_only" else result[first:end + 3])
        for k, line in missing_lines(expected, have):
            if nearby[line] > 0:             # present a few lines away: moved a little, not lost
                nearby[line] -= 1
                continue
            side_line = first_region[3] + sol.index(line) + 1 if single and cls != "whitespace_only" and cls != "both_same" else None
            cands = [(first + n, x) for n, x in enumerate(result[first:end + 3])]
            ratio, idx, found = best_ratio(line, cands)
            if ratio >= SIMILAR_THRESHOLD and idx is not None:
                modified_result_lines.add(idx)
                warnings.append({"kind": "addition_modified", "side": side, "base_line": lo + 1, "side_line": side_line,
                                 "result_line": idx + 1, "text": line, "found": found})
            else:
                errors.append({"kind": "addition_dropped", "side": side, "base_line": lo + 1, "side_line": side_line, "text": line})
        if cls != "whitespace_only":
            for i in range(lo, hi):
                if i in kept and base[i] not in sol:
                    errors.append({"kind": "deletion_reverted", "side": side, "base_line": i + 1, "text": base[i]})
    for i, line in enumerate(base):
        if i not in covered and i not in kept:
            errors.append({"kind": "base_line_lost", "side": None, "base_line": i + 1, "text": line})
    known = {x.strip() for x in base + ours + theirs}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("equal", "delete"):
            continue
        if any(h["class"] in JUDGE_CLASSES and h["lo"] <= i2 and i1 <= h["hi"] for h in hunks):
            continue
        for j in range(j1, j2):
            line = result[j]
            if line.strip() and line.strip() not in known and j not in modified_result_lines \
                    and not re.match(r"(<{7}|\|{7}|={7}|>{7})( |$)", line):
                warnings.append({"kind": "new_line_introduced", "side": None, "result_line": j + 1, "text": line})
    return {"errors": errors, "warnings": warnings, "info": info}


def _side_similarity(side_lines: list[str], window: list[str]) -> float | None:
    cands = list(enumerate(window))
    ratios = []
    for line in side_lines:
        if not line.strip():
            continue
        if line in window:
            ratios.append(1.0)
        else:
            ratios.append(best_ratio(line, cands)[0])
    return max(ratios) if ratios else None


# --------------------------------------------------------------------------- git access

def run_git(repo: Path | str, *args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update({"LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=GIT_TIMEOUT,
                              stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError as exc:
        raise InputError("err.git_missing") from exc


class Repo:
    def __init__(self, top: Path) -> None:
        self.top = top

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return run_git(self.top, *args)

    def out(self, *args: str) -> str | None:
        r = self.git(*args)
        return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None

    def git_path(self, name: str) -> Path:
        value = self.out("rev-parse", "--git-path", name) or f".git/{name}"
        p = Path(value)
        return p if p.is_absolute() else self.top / p

    def head(self) -> str | None:
        return self.out("rev-parse", "--verify", "-q", "HEAD")

    def blob(self, rev_path: str) -> bytes | None:
        r = self.git("cat-file", "blob", rev_path)
        return r.stdout if r.returncode == 0 else None

    def operation(self) -> str:
        if self.git_path("rebase-merge").exists():
            return "rebase"
        if self.git_path("rebase-apply").exists():
            return "rebase" if self.git_path("rebase-apply/rebasing").exists() else "am"
        for name, op in (("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"), ("MERGE_HEAD", "merge")):
            if self.git_path(name).exists():
                return op
        return "none"

    def unmerged(self) -> dict[str, dict[int, tuple[str, str]]]:
        """Unmerged paths mapped to {stage: (mode, blob sha)}."""
        r = self.git("ls-files", "-u", "-z")
        found: dict[str, dict[int, tuple[str, str]]] = {}
        for entry in r.stdout.split(b"\0"):
            if not entry or b"\t" not in entry:
                continue
            meta, path = entry.split(b"\t", 1)
            mode, sha, stage = meta.decode().split()
            found.setdefault(path.decode("utf-8", "replace"), {})[int(stage)] = (mode, sha)
        return found

    def rel(self, path: str) -> str:
        p = Path(path)
        if p.is_absolute():
            full = p
        else:       # relative to the current folder, or to the repository root when only that exists
            here, root = Path.cwd() / p, self.top / p
            if here.exists():
                full = here
            elif root.exists():
                full = root
            else:   # 어디에도 없는 경로(지운 파일): 현재 폴더가 저장소 밖이면 저장소 루트 기준으로 본다
                inside = Path.cwd().resolve().is_relative_to(self.top.resolve())
                full = here if inside else root
        try:
            return full.resolve().relative_to(self.top.resolve()).as_posix()
        except ValueError as exc:
            raise InputError("err.outside_repo", path=path) from exc


# 이 스크립트가 든 스킬 폴더(scripts/ 의 부모). 에이전트가 스킬 폴더로 cd 해서 돌리면, 플러그인이 git 클론일 때
# 스킬 자체 저장소를 "충돌 없음"으로 읽고 엉뚱한 저장소에서 작업을 이어 가므로 입력 문제로 바로 멈춘다.
SKILL_FOLDER = Path(__file__).resolve().parent.parent


def open_repo(path: str | Path) -> Repo:
    if Path(path).resolve().is_relative_to(SKILL_FOLDER):
        raise InputError("err.skill_folder", path=str(path))
    r = run_git(path, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise InputError("err.not_a_repo", path=str(path))
    return Repo(Path(r.stdout.decode("utf-8", "replace").strip()))


OPERATION_LABELS = {
    "merge": ("HEAD", "MERGE_HEAD"),
    "rebase": ("upstream", "my-commit"),
    "cherry-pick": ("HEAD", "CHERRY_PICK_HEAD"),
    "revert": ("HEAD", "REVERT_HEAD"),
    "am": ("HEAD", "patch"),
    "none": ("ours", "theirs"),
}


def labels_for(op: str) -> dict[str, str]:
    ours, theirs = OPERATION_LABELS.get(op, OPERATION_LABELS["none"])
    return {"ours": ours, "theirs": theirs}


def file_kind(base: bytes | None, ours: bytes | None, theirs: bytes | None) -> str:
    present = [x for x in (base, ours, theirs) if x is not None]
    if any(is_binary(x) for x in present):
        return "binary"
    if base is None and ours is not None and theirs is not None:
        return "add_add"
    if base is not None and (ours is None or theirs is None):
        return "modify_delete"
    return "text"


def snapshot_dir(repo: Repo, path: str) -> Path:
    return repo.git_path(SNAPSHOT_DIR) / hashlib.sha1(path.encode("utf-8")).hexdigest()


def save_snapshot(repo: Repo, item: dict) -> Path:
    d = snapshot_dir(repo, item["path"])
    d.mkdir(parents=True, exist_ok=True)
    for name in ("base", "ours", "theirs"):
        f = d / name
        if item[name] is None:
            if f.exists():
                f.unlink()
        else:
            f.write_bytes(item[name])
    kind = file_kind(item["base"], item["ours"], item["theirs"])
    meta = {"path": item["path"], "operation": item["op"], "head": repo.head(), "kind": kind}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def load_snapshot(repo: Repo, path: str) -> dict | None:
    d = snapshot_dir(repo, path)
    meta_file = d / "meta.json"
    if not meta_file.is_file():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("head") != repo.head():     # an older conflict that has since been committed
        return None
    data = {name: ((d / name).read_bytes() if (d / name).is_file() else None) for name in ("base", "ours", "theirs")}
    return {**data, "op": meta.get("operation", "none")}


def list_snapshots(repo: Repo) -> list[str]:
    root = repo.git_path(SNAPSHOT_DIR)
    names = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if meta.get("head") == repo.head():
                names.append(meta["path"])
    return sorted(names)


def prune_snapshots(repo: Repo) -> None:
    root = repo.git_path(SNAPSHOT_DIR)
    if not root.is_dir():
        return
    head = repo.head()
    for d in root.iterdir():
        try:
            stale = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("head") != head
        except (OSError, ValueError):
            stale = True
        if stale and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


HEAD_FILES = {"merge": "MERGE_HEAD", "rebase": "REBASE_HEAD", "cherry-pick": "CHERRY_PICK_HEAD", "revert": "REVERT_HEAD"}


def head_revs(repo: Repo, op: str) -> tuple[str, str, list[dict]] | None:
    """The (base, theirs) revisions of the operation in progress, read from its head file; ours is always HEAD.

    merge: merge base of HEAD and MERGE_HEAD -> MERGE_HEAD; rebase / cherry-pick: <head>^ -> <head>;
    revert: <head> -> <head>^. None when no such head file exists (no operation, or git am)."""
    head_file = HEAD_FILES.get(op)
    if head_file is None or not repo.git_path(head_file).exists():
        return None
    notes: list[dict] = []
    if op == "merge":
        bases = (repo.out("merge-base", "--all", "HEAD", "MERGE_HEAD") or "").split()
        if not bases:
            return None
        if len(bases) > 1:
            notes.append({"kind": "multiple_merge_bases", "side": None, "count": len(bases)})
        return bases[0], head_file, notes
    if op == "revert":
        return head_file, f"{head_file}^", notes
    return f"{head_file}^", head_file, notes


def head_sources(repo: Repo, op: str, rel: str) -> dict | None:
    """Rebuild base, ours and theirs from commits when the index stages are gone."""
    revs = head_revs(repo, op)
    if revs is None:
        return None
    base_rev, theirs_rev, notes = revs
    return {"base": repo.blob(f"{base_rev}:{rel}"), "ours": repo.blob(f"HEAD:{rel}"),
            "theirs": repo.blob(f"{theirs_rev}:{rel}"), "op": op, "notes": notes}


def changed_paths(repo: Repo, old: str, new: str) -> set[str]:
    """Paths that differ between two commits (renames count as delete + add, like the audit itself)."""
    r = repo.git("diff", "--name-only", "--no-renames", "-z", old, new)
    return {x.decode("utf-8", "replace") for x in r.stdout.split(b"\0") if x} if r.returncode == 0 else set()


def both_changed_paths(repo: Repo, op: str) -> list[str]:
    """Paths that both sides of the operation in progress changed against the base: every file that could have
    conflicted. Once a conflict is resolved and staged its index stages are gone, so this is how a call without
    PATH still finds it before the commit. Empty when no operation head exists."""
    revs = head_revs(repo, op)
    if revs is None:
        return []
    base_rev, theirs_rev, _notes = revs
    # 우리 쪽(HEAD)과 상대 쪽이 둘 다 base 에서 바꾼 경로만: 충돌이 날 수 있었던 파일
    return sorted(changed_paths(repo, base_rev, "HEAD") & changed_paths(repo, base_rev, theirs_rev))


def deleted_change_paths(repo: Repo, op: str) -> list[str]:
    """Paths that at least one side of the operation in progress changed, that are gone from the working tree,
    although no side deleted them: the resolution deleted a change (for example with git rm). A path that one
    side deleted is a real modify/delete and is not listed. Empty when no operation head exists."""
    revs = head_revs(repo, op)
    if revs is None:
        return []
    base_rev, theirs_rev, _notes = revs
    out = []
    for rel in sorted(changed_paths(repo, base_rev, "HEAD") | changed_paths(repo, base_rev, theirs_rev)):
        full = repo.top / rel
        if full.exists() or full.is_symlink():
            continue
        # 작업 폴더에 없는 경로만 세 버전을 꺼내 본다(대부분의 경로는 위에서 바로 넘어간다)
        base, ours, theirs = (repo.blob(f"{rev}:{rel}") for rev in (base_rev, "HEAD", theirs_rev))
        if base is not None and (ours is None or theirs is None):
            continue                    # 한쪽이 지웠다: 지움을 받아들인 것이지 변경을 잃은 것이 아니다
        out.append(rel)
    return out


def acquire(repo: Repo, rel: str, prefer_snapshot: bool) -> dict:
    """Collect base, ours and theirs for one path: live stages, snapshot, or commits."""
    op = repo.operation()
    stages = repo.unmerged().get(rel)

    def from_stages() -> dict | None:
        if not stages:
            return None
        got = {}
        for n, name in ((1, "base"), (2, "ours"), (3, "theirs")):
            got[name] = repo.git("cat-file", "blob", stages[n][1]).stdout if n in stages else None
            if n in stages and stages[n][0] == "120000":     # symlink: treat like binary
                got[name] = b"\0"
        return {**got, "op": op, "source": "stages", "notes": []}

    def from_snapshot() -> dict | None:
        snap = load_snapshot(repo, rel)
        return {**snap, "source": "snapshot", "notes": []} if snap else None

    order = (from_snapshot, from_stages) if prefer_snapshot else (from_stages, from_snapshot)
    for fn in order:
        found = fn()
        if found:
            return {"path": rel, **found}
    found = head_sources(repo, op, rel)
    if found:
        return {"path": rel, "source": "heads", **found}
    if op != "none":            # 작업은 진행 중인데 세 버전을 다시 만들 곳이 없다(git am 등): "충돌 없음"이라고 하지 않는다
        raise InputError("err.nothing_to_audit", op=op, path=rel)
    raise InputError("err.no_conflict", path=rel)


def commit_items(repo: Repo, sha: str, paths: list[str]) -> list[dict]:
    r = repo.git("rev-list", "--parents", "-n", "1", sha)
    fields = r.stdout.decode().split() if r.returncode == 0 else []
    if not fields:
        raise InputError("err.bad_commit", sha=sha)
    if len(fields) != 3:
        raise InputError("err.not_two_parent", sha=sha)
    commit, p1, p2 = fields
    bases = (repo.out("merge-base", "--all", p1, p2) or "").split()
    if not bases:
        raise InputError("err.no_merge_base", sha=sha)
    notes = [{"kind": "multiple_merge_bases", "side": None, "count": len(bases)}] if len(bases) > 1 else []
    explicit = bool(paths)
    if not paths:
        names: set[str] = set()
        for parent in (p1, p2):
            out = repo.git("diff", "--name-only", "--no-renames", "-z", bases[0], parent).stdout
            names.update(x.decode("utf-8", "replace") for x in out.split(b"\0") if x)
        paths = sorted(names)
    # 이름 바꾸기 짝: 한쪽(또는 양쪽이 같은 새 이름으로)이 파일 이름을 바꾸고 다른 쪽은 옛 이름에서 고친 경우,
    # git 은 이름 바꾸기 감지로 깨끗이 합친다. 새 경로를 감사할 때 base 와 바꾸지 않은 쪽을 옛 경로에서 읽어야
    # 그 쪽 수정이 "새 파일 통째 추가" 로 잘못 읽히지 않는다. 짝을 확실히 맞출 수 있을 때만 쓴다.
    moved = {"ours": renamed_paths(repo, bases[0], p1), "theirs": renamed_paths(repo, bases[0], p2)}
    pairs = rename_pairs(repo, moved, p1, p2, commit)
    followed_old = {old for old, _side in pairs.values()}
    items = []
    for rel in paths:
        if rel in followed_old and not explicit:
            continue                    # 옛 경로: 새 경로 감사에 합쳐졌다(결과에 옛 경로가 없을 때만 짝이 된다)
        old, by = pairs.get(rel, (rel, None))
        ours_path = rel if by in ("ours", "both") else old
        theirs_path = rel if by in ("theirs", "both") else old
        # 짝을 맞췄다는 사실은 경고가 아니라 정보(INFO)로 알린다: 어느 옛 경로의 base 와 비교했는지 보이게
        followed = [{"kind": "rename_followed", "side": by, "old": old}] if by else []
        items.append({"path": rel, "base": repo.blob(f"{bases[0]}:{old}"), "ours": repo.blob(f"{p1}:{ours_path}"),
                      "theirs": repo.blob(f"{p2}:{theirs_path}"), "result": repo.blob(f"{commit}:{rel}"), "op": "commit",
                      "source": "commit", "notes": list(notes), "info_notes": followed, "explicit": explicit,
                      "labels": {"ours": "parent1", "theirs": "parent2"}})
    return items


def renamed_paths(repo: Repo, old_rev: str, new_rev: str) -> dict[str, str]:
    """new path -> old path for every rename git detects between two commits (git's default similarity)."""
    r = repo.git("diff", "-M", "--name-status", "--diff-filter=R", "-z", old_rev, new_rev)
    fields = [x.decode("utf-8", "replace") for x in r.stdout.split(b"\0")] if r.returncode == 0 else []
    out: dict[str, str] = {}
    # -z 출력은 "R<유사도>\0옛 경로\0새 경로\0" 의 반복이다(맨 끝은 빈 조각)
    for i in range(0, len(fields) - 2, 3):
        status, old, new = fields[i], fields[i + 1], fields[i + 2]
        if status.startswith("R"):
            out[new] = old
    return out


def rename_pairs(repo: Repo, moved: dict[str, dict[str, str]], p1: str, p2: str, commit: str) -> dict[str, tuple[str, str]]:
    """new path -> (old path, which side renamed: ours / theirs / both) for renames that can be audited as one file.

    Only unambiguous cases: one side renamed old -> new and the other side still has old (not renamed, no file at new),
    or both sides renamed old to the same new path. The result must have new and no longer have old."""
    pairs: dict[str, tuple[str, str]] = {}
    rev = {"ours": p1, "theirs": p2}
    for new in sorted(set(moved["ours"]) | set(moved["theirs"])):
        olds = {side: moved[side].get(new) for side in SIDES}
        if olds["ours"] and olds["theirs"]:
            if olds["ours"] != olds["theirs"]:
                continue
            old, by = olds["ours"], "both"
        else:
            by = "ours" if olds["ours"] else "theirs"
            other = "theirs" if by == "ours" else "ours"
            old = olds[by]
            if old in moved[other].values():                    # 다른 쪽도 같은 파일을 다른 이름으로 바꿨다
                continue
            if repo.blob(f"{rev[other]}:{old}") is None or repo.blob(f"{rev[other]}:{new}") is not None:
                continue
        if repo.blob(f"{commit}:{new}") is None or repo.blob(f"{commit}:{old}") is not None:
            continue
        pairs[new] = (old, by)
    return pairs


# --------------------------------------------------------------------------- verification driver

def lines_of(data: bytes | None, eol: str) -> list[str]:
    return [] if data is None else split_lines(data, eol)


def skipped_markers(item: dict, eol: str) -> list[dict]:
    """conflict_marker_left errors for a file whose line audit is skipped (add/add, modify/delete, binary).

    Only a text result is searched. A marker pair that the base or either side already had is not counted,
    so a file that legitimately contains marker-like lines can still be kept as it was."""
    result = item.get("result")
    if result is None or is_binary(result):
        return []
    known = [line for k in ("base", "ours", "theirs")
             if item.get(k) is not None and not is_binary(item[k]) for line in split_lines(item[k], eol)]
    lines = split_lines(result, eol)
    return [{"kind": "conflict_marker_left", "side": None, "result_line": n, "text": lines[n - 1]}
            for n in find_markers(lines, known)]


def verify_item(item: dict, eol: str, ignore_ws: bool) -> dict:
    """Audit one file. Returns findings plus a 'skipped' reason when no line audit applies."""
    parts = [item["base"], item["ours"], item["theirs"], item.get("result")]
    present = [x for x in parts if x is not None]
    result_deleted = item.get("result") is None
    if any(is_binary(x) for x in present):
        kind = "binary"
    elif item["base"] is not None and (item["ours"] is None) != (item["theirs"] is None) and not result_deleted:
        # 한쪽이 지웠는데 결과가 파일을 남겼다: 지움을 받아들이지 않았으니 지운 쪽을 base 와 같다고 보고
        # 고친 쪽 기준으로 줄 감사를 한다(고친 쪽의 추가·수정이 사라졌으면 기존 오류 종류로 보고).
        kind = "text"
        item = dict(item)
        for side in ("ours", "theirs"):
            if item[side] is None:
                item[side] = item["base"]
    elif item["base"] is not None and (item["ours"] is None or item["theirs"] is None):
        kind = "modify_delete"      # 지움을 받아들인 결과(또는 양쪽이 지움): 줄 감사를 건너뛴다
    elif result_deleted:
        # 어느 쪽도 지우지 않은 파일을 해결이 지웠다(git rm 등): 건너뛰지 않고 빈 결과로 감사한다.
        # 양쪽(또는 바꾼 한쪽)이 더한 줄은 addition_dropped, 둘 다 둔 줄은 base_line_lost 오류가 된다.
        kind = "text"
    elif item["base"] is None and item["ours"] is not None and item["theirs"] is not None:
        kind = "add_add"
    else:
        kind = "text"
    if kind != "text":
        # 줄 감사를 건너뛰는 파일이라도 결과에 충돌 표식이 남아 있으면 항상 오류다(add/add 를 git 이 쓴 그대로 둔 경우 등).
        # 결과가 읽을 수 있는 텍스트일 때만 찾는다. 양쪽 버전에 이미 있던 표식 모양 줄(테스트 자료 등)은 남겨도 된다.
        markers = skipped_markers(item, eol)
        if item.get("explicit", True) and kind == "binary" and not markers:
            named = [item["inputs"][k] for k in ("base", "ours", "theirs", "result")
                     if item.get("inputs") and item.get(k) is not None and is_binary(item[k])]
            raise InputError("err.binary", path=named[0] if named else item["path"])
        return {"errors": markers, "warnings": [], "info": [{"kind": "file_skipped", "side": None, "reason": kind}],
                "skipped": kind}
    base, ours, theirs, result = (lines_of(item[k], eol) for k in ("base", "ours", "theirs", "result"))
    if ignore_ws:
        base, ours, theirs, result = (norm_ws(x) for x in (base, ours, theirs, result))
    found = audit(base, ours, theirs, result, result_deleted=result_deleted)
    biggest = max(len(base), len(ours), len(theirs), len(result))
    if biggest > LARGE_FILE_LINES:
        found["info"].append({"kind": "large_file", "side": None, "lines": biggest})
    for note in item.get("notes", []):
        found["warnings"].append(dict(note))
    found["skipped"] = None
    return found


def collect_verify_items(args: argparse.Namespace) -> list[dict]:
    if args.base or args.ours or args.theirs:
        if not (args.base and args.ours and args.theirs):
            raise InputError("err.triple_together")
        if not args.result:
            raise InputError("err.result_required")
        # 입력 경로는 이름→값 표로 명시적으로 모은다(동적 속성 조회를 쓰지 않는다)
        inputs = {"base": args.base, "ours": args.ours, "theirs": args.theirs, "result": args.result}
        files = {}
        for key, raw in inputs.items():
            p = Path(raw)
            if not p.is_file():
                raise InputError("err.file_missing", path=str(p))
            files[key] = p.read_bytes()
        op = args.operation or "none"
        return [{"path": args.name or Path(args.result).name, **files, "op": op, "source": "files", "notes": [],
                 "explicit": True, "labels": labels_for(op), "inputs": inputs}]
    repo = open_repo(args.repo)
    if args.commit:
        return commit_items(repo, args.commit, [repo.rel(p) for p in args.paths])
    explicit = bool(args.paths)
    op = repo.operation()
    # PATH 가 없으면: 아직 병합되지 않은 파일 + scan 스냅샷 + 진행 중 작업에서 양쪽이 바꾼 파일
    # + 한쪽이라도 바꿨는데 어느 쪽도 지우지 않은 채 작업 폴더에서 사라진 파일.
    # 뒤의 두 집합 덕분에 scan 없이 git add / git rm 까지 마친(아직 커밋 전) 해결도 찾아서 감사한다.
    paths = [repo.rel(p) for p in args.paths] or sorted(
        set(repo.unmerged()) | set(list_snapshots(repo)) | set(both_changed_paths(repo, op))
        | set(deleted_change_paths(repo, op)))
    if not paths:
        if op != "none":
            raise InputError("err.nothing_to_audit", op=op, path=str(repo.top))
        raise InputError("err.no_conflict", path=str(repo.top))
    items = []
    for rel in paths:
        got = acquire(repo, rel, prefer_snapshot=True)
        full = repo.top / rel
        if full.is_file():
            result = full.read_bytes()
        elif explicit and all(got.get(k) is None for k in ("base", "ours", "theirs")):
            raise InputError("err.file_missing", path=str(full))     # 어디에도 없는 경로(오타 등)
        else:
            # 해결이 파일을 지웠다(git rm / rm): verify --commit 에서 결과 트리에 경로가 없을 때와 같이
            # result=None 으로 넘긴다. 한쪽이 지운 진짜 수정/삭제면 건너뛰고, 아니면 빈 결과로 감사한다.
            result = None
        items.append({**got, "result": result, "explicit": explicit, "labels": labels_for(got["op"])})
    return items


def build_verify_report(items: list[dict], eol: str, ignore_ws: bool) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    info: list[dict] = []
    files = []
    for item in items:
        found = verify_item(item, eol, ignore_ws)
        found["info"] = list(item.get("info_notes", [])) + found["info"]
        for bucket, name in ((errors, "errors"), (warnings, "warnings"), (info, "info")):
            for f in found[name]:
                f = dict(f)
                f["path"] = item["path"]
                side = f.get("side")
                f["side_label"] = "both" if side == "both" else (item["labels"].get(side) if side else None)
                bucket.append(f)
        files.append({"path": item["path"], "source": item["source"], "skipped": found["skipped"],
                      "operation": item["op"], "labels": item["labels"]})
    for prefix, bucket in (("E", errors), ("W", warnings), ("I", info)):
        for n, f in enumerate(bucket, 1):
            f["id"] = f"{prefix}{n}"
    code = EXIT_ERROR if errors else (EXIT_WARN if warnings else EXIT_OK)
    report = {"ok": code == EXIT_OK, "exit_code": code, "files": files, "errors": errors, "warnings": warnings, "info": info}
    # 영수증: 입력 바이트 + 지적 줄 + 합계로 만든 지문. 같은 입력이면 언어·폴더와 무관하게 같은 값이 나온다.
    inputs = [item.get(k) for item in items for k in ("base", "ours", "theirs", "result")]
    report["receipt"] = audit_receipt("verify", inputs, finding_rows(report) + [totals_text(report)])
    return report


def audit_receipt(command: str, inputs: list[bytes | None], rows: list[str]) -> str:
    """Fingerprint of one run: the exact input bytes and the result lines it produced.

    A reviewer who re-runs the same command on the same files gets the same receipt, so it
    can be pasted into a commit message as proof that the audit ran on exactly these files.
    """
    digest = hashlib.sha256(f"conflict-ledger receipt v1 {command}\n".encode())
    for data in inputs:
        digest.update((hashlib.sha256(data).hexdigest() if data is not None else "-").encode("ascii") + b"\n")
    for row in rows:
        digest.update(row.encode("utf-8") + b"\n")
    return RECEIPT_PREFIX + digest.hexdigest()[:RECEIPT_HEX]


# --------------------------------------------------------------------------- text rendering

def clip(text: str) -> str:
    text = text if len(text) <= EXCERPT_WIDTH else text[:EXCERPT_WIDTH - 1] + "…"
    return json.dumps(text, ensure_ascii=False)


def finding_line(sev: str, f: dict, t: Translator) -> str:
    side = f.get("side")
    label = t.label(f["side_label"]) if f.get("side_label") else ""
    where = ""
    if f.get("base_line") is not None:
        where = f" base:{f['base_line']}"
    elif f.get("result_line") is not None:
        where = f" result:{f['result_line']}"
    params = {"side_label": label, "found": clip(f.get("found") or ""), "count": f.get("count", 0), "hunk": f.get("hunk", ""),
              "similarity": "-" if f.get("similarity") is None else f["similarity"], "reason": f.get("reason", ""), "lines": f.get("lines", 0),
              "old": f.get("old", "")}
    why = t(f"kind.{f['kind']}", **params)
    text = f" {clip(f['text'])}" if f.get("text") not in (None, "") else ""
    if not side:
        # 어느 한쪽에 속하지 않는 지적: 결과의 줄을 가리키면 "result", 아니면 파일 전체에 대한 것("file")
        side = t("side.result") if f.get("base_line") is None and f.get("result_line") is not None else t("side.file")
    return f"{sev:<5} {f['path']} {f['kind']} {side}{where}{text} -- {why}"


def label_header(op: str, labels: dict[str, str], t: Translator) -> list[str]:
    lines = [t("ver.operation", op=op, ours=t.label(labels["ours"]), theirs=t.label(labels["theirs"]))]
    if labels["theirs"] == "my-commit":
        lines.append(t("st.my_commit_side", side="stage3"))
    return lines


def render_verify(report: dict, t: Translator) -> str:
    lines: list[str] = []
    seen = set()
    for f in report["files"]:
        lines.append(t("ver.header", path=f["path"], source=f["source"]))
        key = (f["operation"], tuple(sorted(f["labels"].items())))
        if key not in seen:
            seen.add(key)
            lines.extend(label_header(f["operation"], f["labels"], t))
    for sev, name in (("ERROR", "errors"), ("WARN", "warnings"), ("INFO", "info")):
        for f in report[name]:
            lines.append(finding_line(sev, f, t))
    # 판단이 필요한 덩어리 수: 쪽마다 INFO 가 하나씩 나오므로 (파일, 덩어리 id) 를 한 번만 센다
    judge = len({(f["path"], f["hunk"]) for f in report["info"] if f["kind"] == "judgement_hunk"})
    if report["errors"]:
        lines.append(t("ver.fail", count=len(report["errors"]), warn=len(report["warnings"])))
    elif report["warnings"]:
        lines.append(t("ver.warn", count=len(report["warnings"])))
    else:
        lines.append(t("ver.pass_one" if judge == 1 else "ver.pass", info=judge))
    return "\n".join(lines)


def finding_rows(report: dict) -> list[str]:
    """'<kind> <side> base:<n>' per error or warning (path first when several files were audited)."""
    rows: list[str] = []
    many = len(report["files"]) > 1
    for name in ("errors", "warnings"):
        for f in report[name]:
            where = f"base:{f['base_line']}" if f.get("base_line") is not None else f"result:{f.get('result_line', 0)}"
            prefix = f"{f['path']} " if many else ""
            rows.append(f"{prefix}{f['kind']} {f.get('side') or '-'} {where}")
    return rows


def totals_text(report: dict) -> str:
    return f"errors={len(report['errors'])} warnings={len(report['warnings'])} exit={report['exit_code']}"


def findings_lines(report: dict) -> list[str]:
    """Short result lines: an optional label line, one row per finding, then the summary line."""
    lines: list[str] = []
    for f in report["files"]:
        if f["labels"]["theirs"] == "my-commit":
            lines.append("my-commit-side: stage3")
            break
    lines.extend(finding_rows(report))
    # 마지막 줄: 언어와 무관한 합계 + 영수증. 스크립트를 실제로 돌려야만 나오는 값이다.
    lines.append(f"summary: {totals_text(report)} receipt={report['receipt']}")
    return lines


def write_findings(path: str | None, lines: list[str]) -> None:
    if path:
        Path(path).write_text("".join(x + "\n" for x in lines), encoding="utf-8")


def input_problem_lines(kind: str, path: str | None) -> list[str]:
    """--findings-file lines for an input problem (exit 3). The success-path summary format is not changed."""
    return [f"input_error {kind} {path or '-'}", f"summary: exit={EXIT_INPUT} input_error={kind}"]


def report_input_problem(args: argparse.Namespace, kind: str, path: str | None, message: str) -> int:
    """Exit-3 answer in one call: the message on stderr as before, plus the same verdict as a ledger
    (--json on stdout, --findings-file lines) so the caller never has to guess why the audit stopped."""
    sys.stderr.write(message + "\n")
    # findings_file 는 verify / resolve-trivial 에만 있는 옵션이라 Namespace 에 있는지 먼저 본다
    if "findings_file" in args and args.findings_file:
        try:
            write_findings(args.findings_file, input_problem_lines(kind, path))
        except OSError:
            pass                    # the file itself cannot be written (that may be the problem); stderr has the reason
    if args.json:
        emit(dump({"ok": False, "exit_code": EXIT_INPUT, "input_error": {"kind": kind, "path": path, "message": message}}))
    return EXIT_INPUT


def span_text(h: dict, t: Translator) -> str:
    lo, hi = h["lo"], h["hi"]
    if lo == hi:
        return t("span.after", n=lo) if lo else t("span.start")
    if hi - lo == 1:
        return t("span.line", n=lo + 1)
    return t("span.range", a=lo + 1, b=hi)


# --------------------------------------------------------------------------- commands

def emit(text: str) -> None:
    print(text)


def dump(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def stage_kind(repo: Repo, stages: dict[int, tuple[str, str]]) -> str:
    """What kind of conflict one unmerged path is, judged from the staged contents (never from the file name)."""
    if any(mode == "120000" for mode, _sha in stages.values()):
        return "symlink"
    blobs = {n: repo.git("cat-file", "blob", sha).stdout for n, (_mode, sha) in stages.items()}
    return file_kind(blobs.get(1), blobs.get(2), blobs.get(3))


def cmd_status(args: argparse.Namespace, t: Translator) -> int:
    repo = open_repo(args.repo)
    op = repo.operation()
    labels = labels_for(op)
    stages = repo.unmerged()
    unmerged = sorted(stages)
    snaps = list_snapshots(repo)
    mine = "stage3" if op == "rebase" else None
    if args.json:
        # unmerged_kinds 는 0.1.1 에서 더한 필드: 경로별 text / binary / add_add / modify_delete / symlink
        kinds = {path: stage_kind(repo, stages[path]) for path in unmerged}
        emit(dump({"operation": op, "labels": labels, "my_commit_side": mine, "unmerged": unmerged,
                   "unmerged_kinds": kinds, "snapshots": snaps}))
        return EXIT_OK
    lines = [t("st.operation", op=op), t("st.stage2", label=t.label(labels["ours"])), t("st.stage3", label=t.label(labels["theirs"]))]
    if mine:
        lines.append(t("st.my_commit_side", side=mine))
    lines.append(t("st.unmerged", count=len(unmerged)) if unmerged else t("st.none_unmerged"))
    lines.extend(f"  {p}" for p in unmerged)
    if not unmerged and op != "none":
        # 모두 git add 했지만 아직 커밋 전: PATH 없는 verify 가 감사할 파일을 알려 준다(JSON 키는 늘리지 않는다)
        resolved = both_changed_paths(repo, op)
        if resolved:
            lines.append(t("st.to_audit", paths=", ".join(resolved)))
    deleted = deleted_change_paths(repo, op) if op != "none" else []
    if deleted:
        # 어느 쪽도 지우지 않았는데 작업 폴더에서 사라진 파일: PATH 없는 verify 가 잃은 변경으로 감사한다
        lines.append(t("st.deleted", paths=", ".join(deleted)))
    lines.append(t("st.snapshots", paths=", ".join(snaps)) if snaps else t("st.no_snapshots"))
    emit("\n".join(lines))
    return EXIT_OK


def run_merge_file(base: bytes, ours: bytes, theirs: bytes, *flags: str) -> subprocess.CompletedProcess | None:
    """Run git's own three-way file merge on the three versions (None if git is unavailable or fails)."""
    with tempfile.TemporaryDirectory(prefix="conflict-ledger-") as tmp:
        names = []
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            (Path(tmp) / name).write_bytes(data)
            names.append(str(Path(tmp) / name))
        try:
            r = subprocess.run(["git", "merge-file", "-p", *flags, *names], capture_output=True, timeout=GIT_TIMEOUT,
                               env={**os.environ, "LC_ALL": "C"}, stdin=subprocess.DEVNULL, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
    return r if 0 <= r.returncode < 128 else None


def merge_file_conflicts(base: bytes, ours: bytes, theirs: bytes) -> int | None:
    """Cross-check: number of conflicts git's own merge-file reports (None if git is unavailable)."""
    r = run_merge_file(base, ours, theirs, "--diff3")
    return None if r is None else r.returncode


def ledger_entry(item: dict, eol: str) -> dict:
    kind = file_kind(item["base"], item["ours"], item["theirs"])
    entry: dict = {"path": item["path"], "kind": kind, "hunks": []}
    if kind == "text":
        base, ours, theirs = (lines_of(item[k], eol) for k in ("base", "ours", "theirs"))
        entry["hunks"] = [hunk_json(h) for h in build_hunks(base, ours, theirs)]
        entry["git_merge_file_conflicts"] = merge_file_conflicts(item["base"], item["ours"], item["theirs"])
    return entry


def cmd_scan(args: argparse.Namespace, t: Translator) -> int:
    snapshots: list[str] = []
    if args.base or args.ours or args.theirs:
        if not (args.base and args.ours and args.theirs):
            raise InputError("err.triple_together")
        files = {}
        for key, raw in {"base": args.base, "ours": args.ours, "theirs": args.theirs}.items():
            p = Path(raw)
            if not p.is_file():
                raise InputError("err.file_missing", path=str(p))
            files[key] = p.read_bytes()
        op = args.operation or "none"
        items = [{"path": args.name or Path(args.ours).name, **files, "op": op, "source": "files"}]
        labels = labels_for(op)
    else:
        repo = open_repo(args.repo)
        op = repo.operation()
        # PATH 가 없으면 병합되지 않은 파일, 그것도 없으면(모두 git add 함) 진행 중 작업에서 양쪽이 바꾼 파일
        paths = [repo.rel(p) for p in args.paths] or sorted(repo.unmerged()) or both_changed_paths(repo, op)
        if not paths:
            if op != "none":
                raise InputError("err.nothing_to_audit", op=op, path=str(repo.top))
            raise InputError("err.no_unmerged", path=str(repo.top))
        labels = labels_for(op)
        items = [acquire(repo, rel, prefer_snapshot=False) for rel in paths]
        prune_snapshots(repo)
        for item in items:
            item["kind"] = file_kind(item["base"], item["ours"], item["theirs"])
            snapshots.append(str(save_snapshot(repo, item)))
    ledger = {"version": 1, "operation": op, "labels": labels, "my_commit_side": "stage3" if op == "rebase" else None,
              "files": [ledger_entry(i, args.eol) for i in items]}
    if args.json:
        emit(dump(ledger))
        return EXIT_OK
    lines = label_header(op, labels, t)
    for entry in ledger["files"]:
        n = len(entry["hunks"])
        lines.append(t("scan.file_one" if n == 1 else "scan.file", path=entry["path"], kind=entry["kind"], count=n))
        if entry["kind"] != "text":
            lines.append("  " + t("scan.skipped", kind=entry["kind"]))
            continue
        base, ours, theirs = (lines_of(next(i for i in items if i["path"] == entry["path"])[k], args.eol) for k in ("base", "ours", "theirs"))
        for h in build_hunks(base, ours, theirs):
            mode = t("scan.auto") if h["class"] in AUTO_CLASSES else t("scan.judge")
            one = lambda xs: clip(xs[0]) if xs else t("scan.deleted")    # noqa: E731 - short, local, reads top to bottom
            lines.append("  " + t("scan.hunk", id=h["id"], cls=h["class"], span=span_text(h, t), mode=mode,
                                  ours=one(h["ours"]), theirs=one(h["theirs"])))
        if entry.get("git_merge_file_conflicts") is not None:
            cross = entry["git_merge_file_conflicts"]
            lines.append("  " + t("scan.cross_one" if cross == 1 else "scan.cross", count=cross))
    lines.extend(t("scan.snapshot", path=s) for s in snapshots[:1])
    emit("\n".join(lines))
    return EXIT_OK


MARKER_OPEN = re.compile(r"(<{7,})(?: |$)")     # git's opening marker; conflict-marker-size can make it longer


def has_markers(lines: list[str]) -> bool:
    """True when an opening conflict marker line is still in the file."""
    return any(MARKER_OPEN.match(line) for line in lines)


def is_marker(line: str, char: str, size: int) -> bool:
    return line.startswith(char * size) and line[size:size + 1] in ("", " ")


def pick_side(lines: list[str], side: str) -> list[str] | None:
    """Rebuild one side from a file with conflict blocks: the text outside the blocks plus that side's part of every
    block (the base part of a diff3 block is dropped). None when the markers do not pair up."""
    out: list[str] = []
    part, size = "outside", 0
    for line in lines:
        if part == "outside":
            opened = MARKER_OPEN.match(line)
            if opened:
                part, size = "ours", len(opened.group(1))
            else:
                out.append(line)
        elif part in ("ours", "base") and line == "=" * size:
            part = "theirs"
        elif part == "ours" and is_marker(line, "|", size):
            part = "base"
        elif part == "theirs" and is_marker(line, ">", size):
            part = "outside"
        elif part == side:
            out.append(line)
    return out if part == "outside" else None


def marker_labels(data: bytes | None) -> dict[str, str]:
    """The labels git wrote on the first conflict block of a working file, e.g. HEAD and the merged branch name.

    resolve-trivial reuses them so the rewritten markers read like git's own. A label git did not write
    (no base part in the default merge style) or a file without a conflict block keeps ours / base / theirs."""
    names = dict(DEFAULT_MARKER_LABELS)
    if not data or is_binary(data):
        return names
    size = 0
    for line in split_lines(data):
        if not size:
            opened = re.match(r"(<{7,}) (.*\S)", line)
            if opened:                                   # 첫 충돌 블록의 여는 표식: ours 라벨과 표식 길이
                size = len(opened.group(1))
                names["ours"] = opened.group(2).strip()
            continue
        base_label = re.match(rf"\|{{{size}}} (.*\S)", line)
        if base_label:
            names["base"] = base_label.group(1).strip()
        closed = re.match(rf">{{{size}}} (.*\S)", line)
        if closed:                                       # 닫는 표식: theirs 라벨, 첫 블록만 본다
            names["theirs"] = closed.group(1).strip()
            break
    return names


def untouched_conflict(current: bytes, item: dict, eol: str) -> bool:
    """True when current is still exactly the unresolved conflict of base/ours/theirs.

    Taking our part of every block must give git's own merge with every conflict settled for ours
    (git merge-file --ours), and the same for theirs. A resolved block, or an added, removed or changed line
    anywhere in the file, breaks at least one of the two. Only content is compared, so this works for every
    merge.conflictStyle (merge, diff3, zdiff3) and marker label.
    git merge (merge-ort) aligns lines with the histogram diff while merge-file defaults to Myers, and the two
    can place a conflict differently, so either alignment is accepted (older git without --diff-algorithm
    simply fails that second try).
    """
    if is_binary(current):
        return False
    lines = split_lines(current, eol)
    for algorithm in ((), ("--diff-algorithm=histogram",)):
        matched = True
        for side in SIDES:
            mine = pick_side(lines, side)
            settled = run_merge_file(item["base"], item["ours"], item["theirs"], f"--{side}", *algorithm)
            if mine is None or settled is None or mine != split_lines(settled.stdout, eol):
                matched = False
                break
        if matched:
            return True
    return False


def overwrite_problem(dest: Path, body: bytes, item: dict, eol: str) -> str | None:
    """Why writing body over dest could lose someone's edits (a message key), or None when it is safe:
    dest does not exist, already holds exactly body, or is still the untouched conflict."""
    if not dest.exists():
        return None
    current = dest.read_bytes()
    if current == body or untouched_conflict(current, item, eol):
        return None
    lines = [] if is_binary(current) else split_lines(current, eol)
    return "res.reason_edited" if has_markers(lines) else "res.reason_resolved"


def cmd_resolve(args: argparse.Namespace, t: Translator) -> int:
    results = []
    git_mode = not (args.base or args.ours or args.theirs)
    if not git_mode:
        if not (args.base and args.ours and args.theirs):
            raise InputError("err.triple_together")
        files = {}
        for key, raw in {"base": args.base, "ours": args.ours, "theirs": args.theirs}.items():
            p = Path(raw)
            if not p.is_file():
                raise InputError("err.file_missing", path=str(p))
            files[key] = p.read_bytes()
        # 보고서의 파일 이름: --name > --out 파일(지금 만드는 해결본) > --ours 파일. 영수증에는 이름이 들어가지 않는다.
        shown = args.name or Path(args.out or args.ours).name
        targets = [({"path": shown, **files}, Path(args.out) if args.out else None)]
        marker_names = [DEFAULT_MARKER_LABELS]           # 파일 모드: 0.1.0 과 같은 ours / base / theirs
    else:
        repo = open_repo(args.repo)
        paths = [repo.rel(p) for p in args.paths] or sorted(repo.unmerged())
        if not paths:
            raise InputError("err.no_unmerged", path=str(repo.top))
        if args.out and len(paths) != 1:
            raise InputError("err.out_one_path")
        targets = [(acquire(repo, rel, prefer_snapshot=False), Path(args.out) if args.out else repo.top / rel)
                   for rel in paths]
        # 저장소 모드: 작업 파일에 git 이 써 둔 표식 라벨(HEAD, 병합한 브랜치 이름 등)을 그대로 다시 쓴다
        marker_names = [marker_labels((repo.top / rel).read_bytes() if (repo.top / rel).is_file() else None)
                        for rel in paths]
    for (item, dest), names in zip(targets, marker_names):
        kind = file_kind(item["base"], item["ours"], item["theirs"])
        if kind != "text":
            raise InputError("err.not_text", kind=kind, path=item["path"])
        base, ours, theirs = (lines_of(item[k], args.eol) for k in ("base", "ours", "theirs"))
        merged, applied, remaining = render_resolution(base, build_hunks(base, ours, theirs), args.whitespace, names)
        crlf = item["ours"] is not None and b"\r\n" in item["ours"]
        body = ("\r\n" if crlf else "\n").join(merged) + (("\r\n" if crlf else "\n") if merged else "")
        results.append({"path": item["path"], "applied": applied, "remaining": remaining, "written": False,
                        "_body": body, "_dest": dest, "_item": item})
    # 쓰기 전에 모든 대상을 검사한다: 손으로 고친 흔적(푼 덩어리, 더한 줄)이 하나라도 있으면 아무 파일도 쓰지 않고 멈춘다
    if not args.dry_run and not args.force:
        refused = []
        for r in results:
            if r["_dest"] is None:          # file mode without --out prints the result; nothing to protect
                continue
            reason = overwrite_problem(r["_dest"], r["_body"].encode("utf-8"), r["_item"], args.eol)
            if reason:
                refused.append((str(r["_dest"]), t("res.edited", path=r["_dest"], reason=t(reason))))
        if refused:
            # 입력 문제와 같은 장부로 돌려준다(--json / --findings-file), 경로는 처음 거부한 파일
            return report_input_problem(args, "hand_edited", refused[0][0], "\n".join(msg for _p, msg in refused))
    for r in results:
        if r["_dest"] is not None and not args.dry_run:
            r["_dest"].write_bytes(r["_body"].encode("utf-8"))
            r["written"] = True
    applied_n = sum(len(r["applied"]) for r in results)
    remaining_n = sum(len(r["remaining"]) for r in results)
    rows = ([f"applied: {','.join(r['applied']) or '-'}" for r in results[:1]]
            + [f"remaining_true_conflicts: {remaining_n}"])
    totals = f"applied={applied_n} remaining={remaining_n}"
    # 영수증: 세 입력 + 만들어진 본문 + 결과 줄의 지문(verify 와 같은 방식)
    inputs = [x for (item, _), r in zip(targets, results)
              for x in (item["base"], item["ours"], item["theirs"], r["_body"].encode("utf-8"))]
    receipt = audit_receipt("resolve-trivial", inputs, rows + [totals])
    write_findings(args.findings_file, rows + [f"summary: {totals} receipt={receipt}"])
    if args.json:
        emit(dump([{k: v for k, v in r.items() if not k.startswith("_")} for r in results]))
        return EXIT_OK
    lines = []
    for r in results:
        lines.append(t("res.file_one" if len(r["applied"]) == 1 else "res.file", path=r["path"],
                       applied_n=len(r["applied"]), remaining_n=len(r["remaining"]),
                       applied=", ".join(r["applied"]) or "-", remaining=", ".join(r["remaining"]) or "-"))
        lines.append(t("res.remaining", count=len(r["remaining"])))
        if r["written"]:
            lines.append(t("res.written", path=r["_dest"]))
    if args.dry_run:
        lines.append(t("res.dry"))
    emit("\n".join(lines))
    if not args.dry_run and len(results) == 1 and results[0]["_dest"] is None:
        sys.stdout.write(results[0]["_body"])
    return EXIT_OK


def cmd_verify(args: argparse.Namespace, t: Translator) -> int:
    report = build_verify_report(collect_verify_items(args), args.eol, args.ignore_whitespace)
    write_findings(args.findings_file, findings_lines(report))
    emit(dump(report) if args.json else render_verify(report, t))
    return report["exit_code"]


# --------------------------------------------------------------------------- demo

DEMO_BASE = [f"line {i}" for i in range(1, 13)]


def demo_scenario() -> tuple[list[str], list[str], list[str]]:
    theirs = list(DEMO_BASE)
    theirs[1] = "line 2 THEIRS"
    theirs[6] = "line 7   "
    theirs.insert(10, "added by theirs")
    ours = list(DEMO_BASE)
    ours[1] = "line 2 OURS"
    ours[6] = "line 7 "
    del ours[4]
    good = list(ours)
    good[1] = "line 2 OURS + THEIRS"
    good.insert(good.index("line 11"), "added by theirs")
    return ours, theirs, good


def cmd_demo(args: argparse.Namespace, t: Translator) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="conflict-ledger-demo-"))
    env = {"GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.invalid", "GIT_COMMITTER_NAME": "demo",
           "GIT_COMMITTER_EMAIL": "demo@example.invalid", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    repo_dir = tmp / "repo"
    repo_dir.mkdir()
    try:
        emit(t("demo.start", path=repo_dir))

        def git(*a: str) -> subprocess.CompletedProcess:
            return run_git(repo_dir, *a, extra_env=env)

        def write(lines: list[str]) -> None:
            (repo_dir / "f.txt").write_text("".join(x + "\n" for x in lines), encoding="utf-8")

        ours, theirs, good = demo_scenario()
        git("init", "-q", "-b", "main")
        write(DEMO_BASE)
        git("add", ".")
        git("commit", "-q", "-m", "base")
        git("checkout", "-q", "-b", "feature")
        write(theirs)
        git("commit", "-q", "-am", "theirs")
        git("checkout", "-q", "main")
        write(ours)
        git("commit", "-q", "-am", "ours")
        merged = git("merge", "--no-edit", "feature")
        if merged.returncode == 0:
            emit(t("demo.bad"))
            return EXIT_ERROR
        emit(t("demo.merge"))
        repo = open_repo(repo_dir)
        item = acquire(repo, "f.txt", prefer_snapshot=False)
        save_snapshot(repo, item)
        hunks = build_hunks(*(lines_of(item[k], "normalize") for k in ("base", "ours", "theirs")))
        emit(t("demo.scan", count=len(hunks)))
        for h in hunks:
            mode = t("scan.auto") if h["class"] in AUTO_CLASSES else t("scan.judge")
            emit("  " + f"{h['id']} {h['class']} {span_text(h, t)} [{mode}]")
        outcomes = {}
        for key, lines in (("a", ours), ("b", theirs), ("c", good)):
            emit(t("demo.try", name=t(f"demo.{key}")))
            write(lines)
            items = [{**item, "result": (repo_dir / "f.txt").read_bytes(), "explicit": True, "labels": labels_for(item["op"])}]
            report = build_verify_report(items, "normalize", False)
            for sev, name in (("ERROR", "errors"), ("WARN", "warnings")):
                for f in report[name]:
                    emit("  " + finding_line(sev, f, t))
            emit(t("demo.exit", code=report["exit_code"]))
            outcomes[key] = report
        kinds_a = {f["kind"] for f in outcomes["a"]["errors"]}
        reverted = [f for f in outcomes["b"]["errors"] if f["kind"] == "deletion_reverted"]
        passed = (outcomes["a"]["exit_code"] == EXIT_ERROR and "addition_dropped" in kinds_a
                  and outcomes["b"]["exit_code"] == EXIT_ERROR and any(f["base_line"] == 5 for f in reverted)
                  and outcomes["c"]["exit_code"] == EXIT_OK)
        if args.keep:
            emit(t("demo.kept", path=repo_dir))
        emit(t("demo.ok") if passed else t("demo.bad"))
        return EXIT_OK if passed else EXIT_ERROR
    finally:
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- command line

class _QuietParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:      # used only to peek at --lang; never prints
        raise ValueError(message)


def peek_lang(argv: list[str]) -> str | None:
    """Find --lang before the real parser runs. Never prints, never exits, ignores bad values."""
    pre = _QuietParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--lang", default=None)
    try:
        known, _rest = pre.parse_known_args(argv)
    except (ValueError, SystemExit):
        return None
    return known.lang


# argparse writes its own usage errors in English; these patterns map them to message keys (unknown ones stay as is)
ARGPARSE_ERRORS = (
    (re.compile(r"unrecognized arguments: (?P<args>.*)", re.S), "ap.unrecognized"),
    (re.compile(r"the following arguments are required: (?P<args>.*)", re.S), "ap.required"),
    (re.compile(r"invalid choice: (?P<value>.*?), maybe you meant (?P<closest>.*?)\? \(choose from (?P<choices>.*)\)", re.S),
     "ap.invalid_choice_hint"),
    (re.compile(r"invalid choice: (?P<value>.*?) \(choose from (?P<choices>.*)\)", re.S), "ap.invalid_choice"),
    (re.compile(r"expected one argument"), "ap.expected_one"),
)


def localize_usage_error(message: str, t: Translator) -> str:
    name = None
    about = re.fullmatch(r"argument (?P<name>[^:]+): (?P<rest>.*)", message, re.S)
    if about:
        name, message = about["name"], about["rest"]
    for pattern, key in ARGPARSE_ERRORS:
        hit = pattern.fullmatch(message)
        if hit:
            message = t(key, **hit.groupdict())
            break
    return t("ap.argument", name=name, message=message) if name else message


class _Parser(argparse.ArgumentParser):
    def __init__(self, *a: object, translator: Translator, **kw: object) -> None:
        super().__init__(*a, **kw)            # type: ignore[arg-type]
        self._t = translator

    # argparse 가 붙이는 영어 머리 "usage: " 를 메시지 표 문구로 바꾼다(묶음 제목은 build_parser 에서 직접 만든다)
    def format_usage(self, *a: object, **kw: object) -> str:
        return super().format_usage(*a, **kw).replace("usage: ", self._t("ap.usage"), 1)

    def format_help(self, *a: object, **kw: object) -> str:
        return super().format_help(*a, **kw).replace("usage: ", self._t("ap.usage"), 1)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        sys.stderr.write(self._t("err.usage", prog=self.prog, message=localize_usage_error(message, self._t)) + "\n")
        sys.exit(EXIT_INPUT)


def add_common(group, t: Translator, top: bool) -> None:
    """Options every command takes; group is a parser or an argument group (both have add_argument)."""
    keep = {} if top else {"default": argparse.SUPPRESS}
    group.add_argument("--repo", metavar="PATH", help=t("opt_repo"), **({"default": "."} if top else keep))
    group.add_argument("--json", action="store_true", help=t("opt_json"), **keep)
    group.add_argument("--lang", choices=("en", "ko"), help=t("opt_lang"), **keep)
    group.add_argument("--eol", choices=("normalize", "keep"), help=t("opt_eol"), **({"default": "normalize"} if top else keep))


def add_file_mode(group, t: Translator) -> None:
    group.add_argument("--base", metavar="FILE", help=t("opt_base"))
    group.add_argument("--ours", metavar="FILE", help=t("opt_ours"))
    group.add_argument("--theirs", metavar="FILE", help=t("opt_theirs"))
    group.add_argument("--name", metavar="NAME", help=t("opt_name"))
    group.add_argument("--operation", choices=("merge", "rebase", "cherry-pick", "revert"), help=t("opt_operation"))


def build_parser(t: Translator) -> argparse.ArgumentParser:
    # argparse 의 기본 묶음 제목("positional arguments", "options")은 영어로 고정돼 있어서,
    # 같은 순서(위치 인자 -> 옵션)의 묶음을 메시지 표 제목으로 직접 만들고 모든 인자를 거기에 넣는다.
    parser = _Parser(prog="conflict_ledger.py", description=t("prog_description"), epilog=t("epilog"), translator=t,
                     add_help=False)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser, title=t("ap.positionals"))
    options = parser.add_argument_group(t("ap.options"))
    options.add_argument("-h", "--help", action="help", help=t("opt_help"))
    options.add_argument("--version", action="version", version=f"%(prog)s {__version__}", help=t("opt_version"))
    add_common(options, t, top=True)

    def make(name: str, help_key: str) -> tuple:
        """A subcommand parser; returns its (positional arguments, options) groups."""
        p = sub.add_parser(name, help=t(help_key), description=t(help_key), translator=t, epilog=t("epilog"),
                           add_help=False)
        positional = p.add_argument_group(t("ap.positionals"))
        opts = p.add_argument_group(t("ap.options"))
        opts.add_argument("-h", "--help", action="help", help=t("opt_help"))
        add_common(opts, t, top=False)
        return positional, opts

    make("status", "cmd_status")
    scan_paths, scan = make("scan", "cmd_scan")
    scan_paths.add_argument("paths", nargs="*", metavar="PATH", help=t("opt_paths"))
    add_file_mode(scan, t)
    res_paths, res = make("resolve-trivial", "cmd_resolve")
    res_paths.add_argument("paths", nargs="*", metavar="PATH", help=t("opt_paths"))
    add_file_mode(res, t)
    res.add_argument("--out", metavar="FILE", help=t("opt_out"))
    res.add_argument("--force", action="store_true", help=t("opt_force"))
    res.add_argument("--dry-run", action="store_true", help=t("opt_dry_run"))
    res.add_argument("--findings-file", metavar="FILE", help=t("opt_findings"))
    res.add_argument("--whitespace", choices=("ours", "theirs"), default="ours", help=t("opt_whitespace"))
    ver_paths, ver = make("verify", "cmd_verify")
    ver_paths.add_argument("paths", nargs="*", metavar="PATH", help=t("opt_paths_verify"))
    add_file_mode(ver, t)
    ver.add_argument("--result", metavar="FILE", help=t("opt_result"))
    ver.add_argument("--commit", metavar="SHA", help=t("opt_commit"))
    ver.add_argument("--ignore-whitespace", action="store_true", help=t("opt_ignore_ws"))
    ver.add_argument("--findings-file", metavar="FILE", help=t("opt_findings"))
    _, demo = make("demo", "cmd_demo")
    demo.add_argument("--keep", action="store_true", help=t("opt_keep"))
    return parser


COMMANDS = {"status": cmd_status, "scan": cmd_scan, "resolve-trivial": cmd_resolve, "verify": cmd_verify, "demo": cmd_demo}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")          # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    t = Translator(resolve_lang(peek_lang(argv)))
    try:
        args = build_parser(t).parse_args(argv)
    except SystemExit as stop:                  # --help, --version and usage errors
        return stop.code if isinstance(stop.code, int) else EXIT_INPUT
    t = Translator(resolve_lang(args.lang))
    try:
        return COMMANDS[args.command](args, t)
    except InputError as err:
        return report_input_problem(args, err.kind, err.path, t(err.key, **err.params))
    except BrokenPipeError:
        return EXIT_OK
    except OSError as err:
        # 파일을 읽거나 쓰지 못한 것도 입력 문제(종료 3)로 돌려준다 — 처리 안 된 예외의 종료 1 은 "변경 유실"로 읽힌다
        path = None if err.filename is None else str(err.filename)
        return report_input_problem(args, "os_error", path,
                                    t("err.os_error", path=path or "-", reason=err.strerror or str(err)))


if __name__ == "__main__":
    sys.exit(main())
