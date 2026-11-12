---
name: conflict-ledger
description: Classifies git merge, rebase and cherry-pick conflict hunks against the common ancestor and audits that a resolution kept every change from both sides, with a bundled Python script that reads the index stages and compares line change sets. Use when git reports conflicts, when asked to resolve or double-check a merge, rebase or cherry-pick resolution, when someone asks which side is "ours" during a rebase, or when a merge commit may have silently dropped work. Not for explaining git concepts, setting merge drivers or conflict style, or conflict-free diffs.
license: MIT
compatibility: Python 3.10+ (standard library only) and git 2.35+. No network access.
---

# Conflict ledger

A resolution can pass the tests and still delete one side's change, because lines removed or added outside the conflict markers are invisible when you read only the markers. The script compares the result with the common ancestor (base) line by line, which is exact; reading the file by eye is not.

Run every command as `python3 scripts/conflict_ledger.py <command>` (the path is relative to this skill's folder) from inside the repository, or pass `--repo PATH`. Add `--json` for machine-readable output.

**Always run the script, even when the diff looks small enough to check by eye.** Blanked lines, lines moved between repeated braces and deletions brought back by a wholesale take are exactly what by-eye checks miss. Every audit result you report (findings, totals, receipt) must come from a script run in this session; never write them from your own reading of the files.

## Workflow

1. `python3 scripts/conflict_ledger.py status` tells which operation is running and what stage 2 and stage 3 mean. In a rebase, stage 2 (ours) is the upstream side and stage 3 (theirs) is your own commit being replayed; keep that in mind before choosing a side.
2. `python3 scripts/conflict_ledger.py scan` lists every hunk of every unmerged file with a class and saves a snapshot, so the audit still works after `git add`. Classes are explained in `references/classes.md`.
3. `python3 scripts/conflict_ledger.py resolve-trivial PATH` resolves only the provably trivial hunks (one side only, identical on both sides, whitespace only) and leaves each real conflict as a diff3 marker block. It never touches the index. Use `--dry-run` to preview. It rewrites a file only while it is still exactly the conflict git left; if any block was already resolved or any line was added or changed, it refuses (exit 3) and writes nothing. Then do not reach for `--force` (it discards those edits): finish the remaining blocks yourself (step 4) and go to step 5. `--out FILE` writes the machine resolution to another file if you want to compare.
4. Resolve each remaining `true_conflict` or `both_added_same_point` hunk yourself. Read both sides and the base text in the marker block and combine the intent of both; never pick a whole file from one side unless the other side's change is truly unwanted.
5. `python3 scripts/conflict_ledger.py verify PATH` audits the result. Do not run `git add` or commit until it exits 0. Exit 1 means a change was lost or a marker is left: fix the file and run `verify` again. Exit 2 means warnings that a person should look at; show them to the user. Exit 3 means the input was unusable and the message says how to fix it.
6. Report the exit code, each error with its kind, side and base line, anything left for the user to judge, and the `summary:` line with its receipt.

## Other modes

- Audit a merge commit that already exists: `python3 scripts/conflict_ledger.py verify --commit SHA`.
- Files without a repository (saved stages, or a task that hands you base/ours/theirs/result files): `verify --base FILE --ours FILE --theirs FILE --result FILE` (add `--operation rebase` so labels are right). `resolve-trivial` takes the same three files plus `--out FILE`. The same rule applies: run `verify` (and `resolve-trivial` when asked to resolve), do not compare the files yourself.
- `--findings-file FILE` also writes one short line per finding (`<kind> <side> base:<line>`), and for `resolve-trivial` the `applied` and `remaining_true_conflicts` lines, and always ends with a `summary:` line: totals (errors, warnings and exit code, or applied and remaining counts) and `receipt=cl1-<12 hex>`. The receipt is a fingerprint of the exact input files and the findings; anyone who re-runs the same command on the same files gets the same receipt, so users paste it into the merge commit. It cannot be worked out by hand. When the user asks for the result in a file, pass this option so the script writes the file, then copy the `summary:` line unchanged into your answer. `verify --json` has the same value in its `receipt` field.
- `--lang en|ko` or the `CONFLICT_LEDGER_LANG` variable picks the output language.
- `python3 scripts/conflict_ledger.py demo` shows the whole flow in a throwaway repository.

## Rules

- Never auto-resolve a `true_conflict`; the script will not, and neither should you without reading both sides.
- Binary files, add/add and modify/delete conflicts are reported by kind but not line-audited; tell the user they need a human decision.
- If the script exits with an error, show its error line as is; it already says how to fix the input.
