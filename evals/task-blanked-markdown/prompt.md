---
name: task-blanked-markdown
max_turns: 14
---

docs.md was merged and the result looks fine, but I suspect lines that neither branch touched were emptied. base.md is the common ancestor, ours.md and theirs.md are the two branches, resolved.md is the merge result. Find every such line. Write the result to findings.txt, one line per problem, in the form `<kind> <side> base:<line>` (for example `addition_dropped theirs base:12`). Name the kind `base_line_lost` and write `-` as the side, since no side applies (for example `base_line_lost - base:7`). End findings.txt with the audit's own `summary:` line (totals and `receipt=`) exactly as the audit produced it; I paste that receipt into the merge commit so a reviewer can re-run the audit on the same files and compare.
