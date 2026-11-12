# Hunk classes, findings and labels

## Contents

- Hunk classes
- Findings
- Exit codes
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

## Exit codes

0 pass, 1 error finding, 2 warnings only, 3 input problem (also command-line usage mistakes).

## Side labels

| Operation | ours (stage 2) | theirs (stage 3) |
|---|---|---|
| merge | HEAD, the current branch | MERGE_HEAD, the branch being merged in |
| rebase | upstream, the branch rebased onto | your commit being replayed |
| cherry-pick | HEAD | CHERRY_PICK_HEAD |
| revert | HEAD | REVERT_HEAD, the inverse of the reverted commit |
| merge commit audit | first parent | second parent |
