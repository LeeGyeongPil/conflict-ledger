---
type: regex
pattern: 'base_line_lost - base:\d+'
target: {source: file, path: findings.txt}
match: count:40
---
