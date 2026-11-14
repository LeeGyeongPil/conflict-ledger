# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.4] - 2026-11-14

A bug-fix release. Finding kinds, messages, `--json` fields and `SKILL.md` (apart from its version number) do not
change.

### Fixed
- `verify` no longer passes a modify/delete resolution that keeps the file but loses the edit of the side that kept it. When one side deleted a file and the result still has it, the deletion was not taken, so the deleted side is now read as the base and the file is line-audited against the side that edited it: a line that side added or changed and the result lacks is reported as `addition_dropped` (or another existing error kind) and the run exits 1, with or without PATH and in `verify --commit`. Before, such a file was skipped as `file_skipped` (reason `modify_delete`) and the run printed `PASS` with exit 0. Taking the deletion (`git rm`, or a merge commit without the path) is still `file_skipped` with exit 0, and a conflict marker left in a kept file is still `conflict_marker_left` (exit 1). A resolution that passed before only because it kept a modify/delete file untouched can now fail.

**Known issues (still open):**
- The evaluation's mean score gain is partly the receipt line, which the baseline run cannot produce.
- `scan` shows `ours: (deleted)` for an insertion made only on the other side.
- Given only image files, an agent may look at the pictures instead of running the script. The script does not compare images (it reports `binary`, exit 3); compare them by hand.

## [0.1.3] - 2026-11-14

An evaluation-scope release. The audit is the same as in 0.1.2: findings, exit codes, `--json` fields and
receipts do not change, and `SKILL.md` changed only in its version number. One error message now says how to fix
the input.

### Changed
- Evaluation set: the task case `task-error-binary` and its fixtures are removed. It gave the agent only four PNG images (base, ours, theirs and a resolution) and asked whether the resolution kept both sides' changes. In three pre-release evaluations in a row, opus answered it at least once by looking at the images instead of running the script, while sonnet and haiku ran the script; two `SKILL.md` rewrites (0.1.1, 0.1.2) did not change that. Images are outside what this skill audits (line change sets of text files): the script can only report them as `binary` (exit 3). Binary handling stays covered by the test suite, now including a binary file next to a text file in a real merge, and the error-path task `task-error-disguised-binary` (binary content behind `.txt` names) stays in the measured set. The set now has 12 trigger, 12 non-trigger and 6 task cases; the prompts and graders of the other task cases are unchanged.

### Fixed
- The `file_missing` input problem now says how to fix it: `file not found: <path>; check the path (relative paths are read from the current folder)` (Korean: `파일을 찾을 수 없음: <path>, 경로를 확인할 것(상대 경로는 현재 폴더 기준)`). Its `kind`, `path` and `--findings-file` lines are unchanged.

**Known issues (still open):**
- The evaluation's mean score gain is partly the receipt line, which the baseline run cannot produce.
- `scan` shows `ours: (deleted)` for an insertion made only on the other side.
- Given only image files, an agent may look at the pictures instead of running the script. The script does not compare images (it reports `binary`, exit 3); compare them by hand.

## [0.1.2] - 2026-11-13

A skill-document release. The script's behavior, exit codes, finding kinds, `--json` fields and receipts are the same
as in 0.1.1; only the version number in the script changed.

### Fixed
- In a pre-release evaluation a strong model sometimes answered an image conflict (base, ours, theirs and a resolved `.png`) by viewing the images instead of running the script. The "First command" section of `SKILL.md` now says that it holds for every file type: images and other non-text files go to the same `verify` command, viewing an image or comparing its pixels is not an audit, and the script's exit code and message are the answer even when the files cannot be line-audited.

**Known issues (still open from 0.1.1):**
- A missing input file's error (`file_missing`) still says the file was not found without saying how to fix it.

## [0.1.1] - 2026-11-13

A correction release after a pre-release evaluation with several models. Exit codes, finding kinds, the `--json`
fields of 0.1.0 and the receipts of file-mode runs are unchanged; one info kind (`rename_followed`) and one input
problem kind (`skill_folder`) are new.

### Fixed
- `SKILL.md` let an agent answer about binary files without running the script: it said such files "are reported by kind but not line-audited", and a strong model concluded from the `.png` extension alone. The skill now starts with a "First command" section (`verify --base … --result … --json` when files are given, `verify --commit SHA --json` for a merge commit, `resolve-trivial … --out FILE` when asked to resolve files, `status --json` otherwise), and binary, add/add, modify/delete, symlink and missing-file cases are judged by the script, never from a name, an extension or the bytes.
- `SKILL.md` has an early-exit rule: when the first command exits 3, or no conflict is in progress and nothing was given to audit, the agent reports that and stops instead of searching the disk or creating a repository.
- Input problems (exit 3) now come back as a ledger in one call: with `--json`, stdout carries `{"ok": false, "exit_code": 3, "input_error": {"kind", "path", "message"}}`; with `--findings-file`, the file holds `input_error <kind> <path>` and `summary: exit=3 input_error=<kind>`. The stderr line is unchanged. `resolve-trivial`'s refusal to overwrite a hand-edited file uses the same ledger (`kind` `hand_edited`).
- A file that cannot be read or written (for example `--findings-file` in a folder that does not exist) exits 3 with `kind` `os_error` instead of a traceback with exit 1, which reads as "a change was lost".
- Binary detection is by content only (a NUL byte or invalid UTF-8): a binary file named `*.txt` gets the same verdict as any other.
- `verify` ended with `PASS ... (N hunks need judgement)` counting one note per side, so a single hunk was counted twice. It now counts distinct hunks and says `(1 hunk needs judgement)` / `(2 hunks need judgement)`.
- `resolve-trivial` said "applied 1 hunks"; it now says "applied 1 hunk". Inside a repository the rewritten conflict markers keep the labels git wrote (`<<<<<<< HEAD`, the base label, `>>>>>>> <branch>`) instead of `ours` / `base` / `theirs`; file mode keeps `ours` / `base` / `theirs`.
- `SKILL.md` step 6 asked for a `summary:` line that the default text output does not print; it now points at the `summary:` line of `--findings-file` or the `receipt` field of `--json`.
- `verify` and `scan` without PATH did nothing for a conflict that was resolved and staged with `git add` before `scan` ran and not yet committed: they exited 3 with "no conflict in progress" although a merge was in progress, so a dropped change was caught only when the path was typed by hand. While a merge, rebase, cherry-pick or revert is in progress, `verify` without PATH now also audits every file both sides changed against the base (rebuilt from the operation's commits, `source: heads`), and `scan` uses those files once nothing is unmerged. Text `status` names them. When an operation is in progress but nothing can be found, the input problem is the new kind `nothing_to_audit` instead of `no_conflict`. A missing relative PATH run from outside the repository is reported as not found instead of "outside the repository".
- A resolution that deleted a file no side deleted passed: `git rm` of a file both sides changed gave `PASS` with exit 0 and an `INFO ... file_skipped ... modify_delete` note, before the commit (`verify` without PATH) and after it (`verify --commit HEAD`, since 0.1.0), although it was not a modify/delete conflict. Such a file is now audited as an empty result: each line a side added is `addition_dropped` (judged hunks included) and each line both sides kept is `base_line_lost`, so `verify` exits 1. The same holds when only one side changed the file, when the file was removed with plain `rm` while still unmerged, and for an add/add file the resolution deleted. `verify` without PATH also finds a file either side changed that is gone from the working tree although no side deleted it, and text `status` names such files. Naming such a path gives the same verdict instead of `file_missing` (still the problem for a path no version has). Taking a deletion one side made stays `file_skipped` with reason `modify_delete`.
- Text output printed a bare `-` in the side column of findings that belong to no side (`base_line_lost`, `large_file`, `file_skipped`, ...). It now prints `file` (`파일`), or `result` (`결과`) for a finding that points at a result line. `--json` (`side: null`) and the `--findings-file` rows (`-`) are unchanged.
- `SKILL.md` sends a resolved-and-staged operation (an operation other than `none`, nothing unmerged) straight to `verify --json` without PATH, and no longer runs `verify --commit HEAD` after a fast-forward merge (which has one parent and exits 3); it says there was nothing to resolve.
- A file whose line audit is skipped (add/add, modify/delete, binary) was never checked for conflict markers: a real merge with an add/add file left exactly as git wrote it (`<<<<<<< HEAD` ... `>>>>>>> feature`) gave only `INFO ... file_skipped`, then `PASS` and exit 0, although exit 1 means "a marker is left". The marker check now runs on every audited file whose result is text, whatever its kind: such a file gets `conflict_marker_left` (exit 1) without PATH, with PATH and in `verify --commit`, next to its `file_skipped` note. Marker-like pairs that the base or a side already had are not counted. An add/add file resolved without markers still passes with `file_skipped`. In file mode, binary inputs with a text result that still holds markers give exit 1 instead of the `binary` input problem.
- `resolve-trivial` in file mode named its report after the `--ours` file when `--name` was not given (`ours.py: applied 4 hunks ...`), which read as if the ours file had been resolved. It now names the `--out` file (the `--ours` file only when there is no `--out`); `--name` still wins and the receipt does not change. The `path` value of `resolve-trivial --json` in file mode follows the same rule.
- `verify --commit` reported a clean merge where one parent renamed a file and the other edited it under the old name as a loss (exit 1, `addition_dropped` of base lines and `new_line_introduced` of the edit), because the new path was read as a file added whole by the renaming side. It now follows renames that git's rename detection pairs without doubt (one side renamed, or both to the same name, and the commit has only the new path): the new path is audited against the base and the other side at the old path, the old path is not listed separately, and an info `rename_followed` names it. A change lost across the rename is still an error.
- An agent that ran the script from inside the skill's own folder (`cd <skill folder> && python3 scripts/conflict_ledger.py status --json`) got the installed plugin's git clone back as a clean repository (`"operation": "none"`), and a request such as "merge the two agent branches" then turned into a search for branches in the wrong repository. Repository commands run from the skill's folder, or with `--repo` pointing into it, now stop with the input problem `skill_folder` (exit 3) in one call; the repository that holds the skill folder and file mode are unaffected. `SKILL.md` says to call the script by its full path from the user's folder, that exit 3 is the answer also for a merge, rebase or cherry-pick request (rerun once from the user's folder only for `skill_folder`), and that unnamed branches are asked about after one `git branch` instead of guessed.
- `scan` said "git merge-file reports 1 conflicts"; it now says "1 conflict". The `large_file` note names the line count of the largest of the four versions ("largest version: N lines").

### Changed
- `status --json` adds `unmerged_kinds` (`text`, `binary`, `add_add`, `modify_delete`, `symlink` per path, read from the staged contents); the `unmerged` list is unchanged.
- The skill description also covers merging branches (including parallel agent branches) and confirming nothing was silently overwritten, and excludes configuring merge drivers or conflict style, undoing merges and writing CI jobs.
- Evaluation set: three near-miss queries that asked for multi-step work in an empty folder now ask to be shown the configuration or commands without changing files (near-miss 02 was "Set up a custom merge driver for package-lock.json in .gitattributes.", 05 was "Write a GitHub Actions job that fails PRs which have merge conflicts with main.", 07 was "Undo my last merge - I want to go back to before it."). New error-path task `task-error-disguised-binary` (binary content behind `.txt` names). The six existing task prompts and graders are unchanged, and the turn budget stays at 6.

### Known issues
- The evaluation's mean score gain is partly the receipt line, which the baseline run cannot produce; a correctness-only gain is about +0.56 by the maintainers' recomputation of the 0.1.0 run.
- A missing input file's error (`file_missing`) says the file was not found but not how to fix it (for example which option to check).
- `scan` shows `ours: (deleted)` for an insertion made only on the other side.

## [0.1.0] - 2026-11-12

### Added
- `status`, `scan`, `resolve-trivial`, `verify` and `demo` commands in `skills/conflict-ledger/scripts/conflict_ledger.py`.
- Hunk classification against the merge base: `ours_only`, `theirs_only`, `both_same`, `whitespace_only`, `true_conflict`, `both_added_same_point`.
- Resolution audit findings: `addition_dropped`, `deletion_reverted`, `base_line_lost`, `conflict_marker_left`, `addition_modified`, `side_discarded`, `new_line_introduced`.
- Input from index stages, scan snapshots, merge/rebase/cherry-pick heads, plain files, or an existing two-parent merge commit.
- English and Korean output (`--lang`, `CONFLICT_LEDGER_LANG`).
- Evaluation set under `evals/` and Claude Code plugin manifests.
- `--findings-file` output ends with a language-independent `summary:` line: totals plus `receipt=cl1-<12 hex>`, a fingerprint of the input files and the findings that is the same on every re-run with the same files. `verify --json` carries it as `receipt`.
- `resolve-trivial` writes a file only while it is still exactly the unresolved conflict (both sides of every block and every line outside the blocks match git's own merge of the three versions); a fully or partly hand-resolved file, or one with added or changed lines, is refused with exit 3 and nothing is written. The same rule protects an existing `--out` file; `--force` overrides. Inside a repository `--out FILE` writes elsewhere instead of the working file (one path).
- `--lang ko` also translates argparse's own help headers (`usage:`, positional arguments, options) and common usage errors.

The issues known at 0.1.0 are listed under 0.1.1: fixed ones under Fixed, those that remain under Known issues.

[0.1.4]: https://github.com/LeeGyeongPil/conflict-ledger/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/LeeGyeongPil/conflict-ledger/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/LeeGyeongPil/conflict-ledger/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/LeeGyeongPil/conflict-ledger/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/LeeGyeongPil/conflict-ledger/releases/tag/v0.1.0
