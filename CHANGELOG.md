# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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

### Known issues
- The evaluation's mean score gain (+0.46) is partly the receipt line, which the baseline run cannot produce; a correctness-only gain is about +0.56 by the maintainers' recomputation.
- `verify` ends with `PASS ... (N hunks need judgement)` counting per-side notes, so one hunk can be counted twice.
- `scan` shows `ours: (deleted)` for an insertion made only on the other side.
- The default text output of `verify` has no `summary:` line; use `--findings-file` or `--json` to get the receipt.
- `resolve-trivial` writes `<<<<<<< ours` / `>>>>>>> theirs` marker labels instead of git's original labels, and says "1 hunks" for a single hunk.
