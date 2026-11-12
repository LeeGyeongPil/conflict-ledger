---
name: task-trivial-then-judge
max_turns: 14
---

base.py, ours.py and theirs.py are three versions of a file with merge conflicts. Resolve every hunk that can be resolved without judgement into merged.py, leave the real conflicts in merged.py as diff3 markers, and write findings.txt with the lines `applied: <hunk ids>` and `remaining_true_conflicts: <count>`, then end it with the tool's own `summary:` line (hunk counts and `receipt=`) exactly as the tool produced it; I paste that receipt into the merge commit so a reviewer can re-run the same step and compare.
