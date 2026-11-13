---
name: conflict-ledger
description: Classifies git merge, rebase and cherry-pick conflict hunks against the common ancestor and audits that a resolution kept every change from both sides, with a bundled Python script that reads the index stages and compares line change sets. Use for a merge, rebase or cherry-pick conflict, fixing conflicts, which side is "ours" during a rebase, merging branches (including parallel agent branches) and confirming nothing was silently overwritten, checking a resolution before commit, or a merge commit that may have dropped work. Not for explaining git concepts, configuring merge drivers or conflict style, conflict-free diffs, undoing merges, or writing CI jobs.
license: MIT
compatibility: Python 3.10+ (standard library only) and git 2.35+. No network access.
metadata:
  version: "0.1.1"
---

# Conflict ledger

A resolution can pass the tests and still delete one side's change, because lines removed or added outside the conflict markers are invisible when you read only the markers. The script compares the result with the common ancestor (base) line by line, which is exact; reading the file by eye is not.

Run every command from the user's current folder (the repository), calling the script by its full path in this skill's folder: `python3 <skill-folder>/scripts/conflict_ledger.py <command>`, shortened below to `python3 scripts/conflict_ledger.py`. Never `cd` into the skill's folder; it is not the user's repository. Use `--repo PATH` only for a repository the user named.

## First command

Run exactly one of these before you say anything about the conflict, and start from its output. Do not open, diff or inspect the files yourself first.

- The user gives the base, ours and theirs files and a resolved file: `python3 scripts/conflict_ledger.py verify --base BASE --ours OURS --theirs THEIRS --result RESULT --json`. Add `--findings-file FILE` when the user wants the result in a file, and `--operation rebase` when the files come from a rebase.
- The user gives the base, ours and theirs files and asks you to resolve them: `python3 scripts/conflict_ledger.py resolve-trivial --base BASE --ours OURS --theirs THEIRS --out FILE` (add `--findings-file FILE` when the user wants the result in a file).
- The user names a merge commit that already exists: `python3 scripts/conflict_ledger.py verify --commit SHA --json`.
- Anything else: `python3 scripts/conflict_ledger.py status --json`.

The script decides every kind of input, including binary files, add/add, modify/delete and symlink conflicts and missing files. One call gives the verdict; an input problem comes back as exit code 3 with an `input_error` object (`kind`, `path`, `message`) in the JSON and, with `--findings-file`, as an `input_error` line and a `summary:` line in the file.

### Stop early

- If the first command exits with code 3, that is the answer, also when the user asked you to merge, rebase or cherry-pick: report the exit code and the `message` (with its `kind` and `path`) as the script wrote it, say what the user can do next, and stop. Only for kind `skill_folder` run the same command once more from the user's folder by its full path.
- If `status --json` shows `"operation": "none"`, no `unmerged` paths and no `snapshots`, no conflict is in progress. If the user asked you to merge, rebase or cherry-pick and named the branches or commits, run that git command now; on conflicts continue with the workflow below. If they did not name them, do not guess: list the branches once with `git branch`, ask which ones to use, and stop. If a merge finishes cleanly with a merge commit, audit it with `verify --commit HEAD --json`; if git fast-forwarded, or a rebase or cherry-pick finished without conflicts, there was nothing to resolve: say so and stop. Otherwise tell the user that no conflict is in progress, that `verify --commit SHA` audits a merge commit after the fact and that `verify --base FILE --ours FILE --theirs FILE --result FILE` audits saved versions, and stop.
- If `status --json` shows an operation other than `none` and no `unmerged` paths, the conflicts are already resolved and staged but not committed: go straight to step 5 and run `verify --json` with no PATH (it finds every file both sides changed).
- Never search the file system for other repositories or files, and never create repositories, branches or sample files just to have something to check.

## Workflow

**Always run the script, even when the diff looks small enough to check by eye.** Blanked lines, lines moved between repeated braces and deletions brought back by a wholesale take are exactly what by-eye checks miss. Every result you report (findings, totals, receipt, input problems) must come from a script run in this session; never write them from your own reading of the files.

1. Read the `status` output. It tells which operation is running and what stage 2 and stage 3 mean. In a rebase, stage 2 (ours) is the upstream side and stage 3 (theirs) is your own commit being replayed; keep that in mind before choosing a side. `unmerged_kinds` is the script's verdict on each path's kind (`text`, `binary`, `add_add`, `modify_delete`, `symlink`); only `text` paths go through the line audit.
2. `python3 scripts/conflict_ledger.py scan` lists every hunk of every unmerged file with a class and saves a snapshot, so the audit still works after `git add`. Classes are explained in `references/classes.md`.
3. `python3 scripts/conflict_ledger.py resolve-trivial PATH` resolves only the provably trivial hunks (one side only, identical on both sides, whitespace only) and leaves each real conflict as a diff3 marker block with git's own labels. It never touches the index. Use `--dry-run` to preview. It rewrites a file only while it is still exactly the conflict git left; if any block was already resolved or any line was added or changed, it refuses (exit 3) and writes nothing. Then do not reach for `--force` (it discards those edits): finish the remaining blocks yourself (step 4) and go to step 5. `--out FILE` writes the machine resolution to another file if you want to compare.
4. Resolve each remaining `true_conflict` or `both_added_same_point` hunk yourself. Read both sides and the base text in the marker block and combine the intent of both; never pick a whole file from one side unless the other side's change is truly unwanted.
5. `python3 scripts/conflict_ledger.py verify PATH` audits the result. Do not run `git add` or commit until it exits 0. Exit 1 means a change was lost or a marker is left: fix the file and run `verify` again. Exit 2 means warnings that a person should look at; show them to the user. Exit 3 means the input was unusable; report the message as is.
6. Report the exit code, each error with its kind, side and base line, anything left for the user to judge, and the receipt: the `summary:` line written by `--findings-file`, or the `receipt` value of `--json`. The default text output has no receipt.

## Other modes

- `--findings-file FILE` (on `verify` and `resolve-trivial`) also writes one short line per finding (`<kind> <side> base:<line>`), and for `resolve-trivial` the `applied` and `remaining_true_conflicts` lines, and always ends with a `summary:` line: totals (errors, warnings and exit code, or applied and remaining counts) and `receipt=cl1-<12 hex>`. The receipt is a fingerprint of the exact input files and the findings; anyone who re-runs the same command on the same files gets the same receipt, so users paste it into the merge commit. It cannot be worked out by hand. When the user asks for the result in a file, pass this option so the script writes the file, then copy the `summary:` line unchanged into your answer. `verify --json` has the same value in its `receipt` field.
- `resolve-trivial` in file mode takes the same three files plus `--out FILE`; its report names the `--out` file, and `--name PATH` names the file's real path instead. `--operation rebase` sets the side labels in file mode.
- `--lang en|ko` or the `CONFLICT_LEDGER_LANG` variable picks the output language; `--json` keys and kinds do not change.
- `python3 scripts/conflict_ledger.py demo` shows the whole flow in a throwaway repository.

## Rules

- Never auto-resolve a `true_conflict`; the script will not, and neither should you without reading both sides.
- Binary files, add/add, modify/delete and symlink conflicts and missing files are judged by the script, not by you. Do not draw a conclusion from a file name, an extension or the bytes; run the first command and report its exit code and its line. Only after that, and only for what the script could not line-audit, tell the user which files need a person's decision.
- If the script exits with an error, show its error line as is; it already says how to fix the input.
