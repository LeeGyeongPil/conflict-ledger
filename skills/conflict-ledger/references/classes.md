# Hunk classes, findings and labels

## Contents

- Hunk classes
- Findings
- Exit codes
- Input problems (exit 3)
- Side labels

## Hunk classes

A hunk is a run of base lines changed by ours, theirs, or both (touching changes count as one hunk, like git).

| Class | Meaning | Script resolves it |
|---|---|---|
| `ours_only` | only ours changed it | yes, takes ours |
| `theirs_only` | only theirs changed it | yes, takes theirs |
| `both_same` | both made the identical change | yes |
| `whitespace_only` | both changed it, equal after collapsing whitespace | yes, ours unless `--whitespace theirs` |
| `true_conflict` | both changed it differently | no, needs judgement |
| `both_added_same_point` | both inserted different lines at the same point | no, needs judgement |

File-level kinds reported by `scan` and not line-audited: `add_add` (no base), `modify_delete`, `binary`.

## Findings

| Finding | Level | Meaning |
|---|---|---|
| `addition_dropped` | error | a line one side added is missing from the result |
| `deletion_reverted` | error | a base line one side deleted is back in the result |
| `base_line_lost` | error | a base line nobody changed is missing or altered |
| `conflict_marker_left` | error | an opening and closing conflict marker are still in the result |
| `addition_modified` | warning | an added line is missing but a similar line (ratio 0.6 or more) sits nearby |
| `side_discarded` | warning | nothing close to one side's change survived in a judged hunk |
| `new_line_introduced` | warning | a line outside judged hunks that neither side wrote |
| `multiple_merge_bases` | warning | several merge bases; the first was used |
| `judgement_hunk` | info | a hunk that needed judgement and how close each side is to the result |
| `rename_followed` | info | `verify --commit`: one side (or both, to the same name) renamed the file; the new path was audited against the base and the other side at the old path |

## Exit codes

0 pass, 1 error finding, 2 warnings only, 3 input problem (also command-line usage mistakes).

## Input problems (exit 3)

Every input problem prints one line on stderr. With `--json` stdout also carries
`{"ok": false, "exit_code": 3, "input_error": {"kind": ..., "path": ..., "message": ...}}`, and with
`--findings-file FILE` (verify, resolve-trivial) the file holds `input_error <kind> <path>` (`-` when no path)
and `summary: exit=3 input_error=<kind>`. `kind` and `path` do not change with `--lang`; `message` does.

| kind | When |
|---|---|
| `not_a_repo` | the folder is not inside a git repository and no file-mode options were given |
| `skill_folder` | the script was run from this skill's own folder (or `--repo` points into it) instead of the repository to check |
| `no_conflict` | no conflict in progress and no snapshot for the path |
| `no_unmerged` | `scan` or `resolve-trivial` found no unmerged files and no operation is in progress |
| `nothing_to_audit` | a merge, rebase, cherry-pick or revert is in progress, but no unmerged file, snapshot or file changed on both sides was found |
| `binary` | a file is binary by content (a NUL byte or not UTF-8), whatever its name or extension |
| `add_add`, `modify_delete` | `resolve-trivial` was asked to resolve a file of that kind |
| `hand_edited` | `resolve-trivial` refused to overwrite a file that was already edited by hand |
| `file_missing` | a named file does not exist |
| `outside_repo` | a path is outside the repository |
| `triple_together`, `result_required`, `out_one_path` | file-mode options given incompletely, or `--out` with several paths |
| `not_two_parent`, `bad_commit`, `no_merge_base` | `verify --commit` got a commit that is not an auditable two-parent merge |
| `git_missing` | git is not installed |
| `os_error` | a file could not be read or written (for example `--findings-file` in a folder that does not exist) |

Command-line usage mistakes also exit 3 but print only the usage line and the error on stderr.

`status --json` reports each unmerged path's kind in `unmerged_kinds`: `text`, `binary`, `add_add`,
`modify_delete` or `symlink`. Only `text` paths are line-audited. `verify` on binary files given in file mode or named
as a PATH is the `binary` input problem above; binary files it finds by itself (no PATH given), every modify/delete
file (one side deleted it) and every add/add file still present in the result are listed as `file_skipped` (info) and
need a person's decision. Skipping the line audit never skips the marker check: if the result of such a file is text
and still holds an opening and a closing conflict marker that no version had, it is `conflict_marker_left` (exit 1),
with or without PATH and in `verify --commit`. In file mode a text result with markers is that error even when the
inputs are binary.

`verify --commit` follows a rename that git's rename detection pairs without doubt: one side renamed old to new and
the other side still has old, or both sides renamed old to the same new path, and the merge commit has new and not
old. The new path is then audited with base and the unrenamed side read at old, old is not listed separately, and an
info `rename_followed` names old. Other renames (for example to different names on each side) are audited path by
path.

A resolution that deletes a file no side deleted (`git rm`, `rm`, or a merge commit without the path) is not skipped:
the file is audited as an empty result. Every line a side added is `addition_dropped` (judged hunks included, since
nothing of them survived) and every line both sides kept is `base_line_lost`, so `verify` fails with exit 1. This
holds whether one or both sides changed the file, with or without PATH, before and after the commit
(`verify --commit`). Taking a deletion that one side made (a real modify/delete, or a clean one-sided delete) is
still `file_skipped` with reason `modify_delete`.

Without PATH, `verify` audits the unmerged files, the files with a `scan` snapshot and, while a merge, rebase,
cherry-pick or revert is in progress, every file both sides changed against the base, plus every file either side
changed that is gone from the working tree although no side deleted it. These sets find conflicts that were resolved
and staged with `git add` or `git rm` before `scan` ran; their base, ours and theirs are rebuilt from the operation's
commits (`source: heads`). A PATH you name that no version has is the `file_missing` problem. `scan` without PATH
uses the unmerged files, or the files both sides changed once nothing is unmerged.

In the text output the side column of a finding that belongs to no side reads `file` (`파일` in Korean), or
`result` (`결과`) when it points at a result line; the `--findings-file` rows keep `-` there.

## Side labels

| Operation | ours (stage 2) | theirs (stage 3) |
|---|---|---|
| merge | HEAD, the current branch | MERGE_HEAD, the branch being merged in |
| rebase | upstream, the branch rebased onto | your commit being replayed |
| cherry-pick | HEAD | CHERRY_PICK_HEAD |
| revert | HEAD | REVERT_HEAD, the inverse of the reverted commit |
| merge commit audit | first parent | second parent |
