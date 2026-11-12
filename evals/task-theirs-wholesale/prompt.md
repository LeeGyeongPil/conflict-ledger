---
name: task-theirs-wholesale
max_turns: 14
---

My merge had conflicts in service.cfg and I resolved it by taking the other branch's file. base.cfg is the common ancestor, ours.cfg is my branch, theirs.cfg is the other branch and resolved.cfg is my resolution. Check that the resolution did not undo anything my branch did. Write the result to findings.txt, one line per problem, in the form `<kind> <side> base:<line>` (for example `addition_dropped theirs base:12`). End findings.txt with the audit's own `summary:` line (totals and `receipt=`) exactly as the audit produced it; I paste that receipt into the merge commit so a reviewer can re-run the audit on the same files and compare.
