---
name: task-rebase-labels
max_turns: 14
---

I am in the middle of `git rebase main`. For handlers.py I saved the index stages as stage1_base.py, stage2_upstream.py (stage 2) and stage3_mine.py (stage 3), and my resolution is resolved.py. Check that none of my commit's changes were dropped. Start findings.txt with a line `my-commit-side: stage<N>` naming the stage that holds my commit, then add one line per problem as `<kind> <side> base:<line>`. End findings.txt with the audit's own `summary:` line (totals and `receipt=`) exactly as the audit produced it; I paste that receipt into the merge commit so a reviewer can re-run the audit on the same files and compare.
