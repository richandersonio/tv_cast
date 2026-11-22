# Repository agent instructions

## Changelog before every commit

Before every commit, review the intended changes and bring the root
`CHANGELOG.md` up to date (using the `rich-changelog` skill when available).
Follow this style:

- **Date-based completed-work history, newest date first:** Group work under
  ISO local date headings (`## YYYY-MM-DD`) with outcome-led bullets directly
  beneath each date heading (no `### Outcomes` subheading).
- **Bold scope-led titles, concise plain English:** Start each bullet with
  `**Scope:**` or `**Scope — outcome:**` in 1–2 short, human-friendly sentences
  covering key user-visible updates rather than verbose internal details or a
  raw commit list.
- **Consolidate to final outcomes:** Merge related commits and follow-up fixes
  for the same capability into one accurate entry; amend an existing entry for
  the same date rather than duplicating it.
- **Verify against the staged diff:** Record only evidenced behavior and check
  formatting with `git diff --check` before committing.
