# Repository agent instructions

<!-- rio-git-protocol:start -->
## Mandatory Git and pull request protocol

Before repository changes, commits, PR creation (including drafts), or merge,
read and follow the active required
[Git Workflow and Pull Request Standard](https://github.com/richandersonio/riokm/blob/main/riokm/knowledge/standards/git-workflow-and-pull-request-standard.md)
through RioKM:

```sh
riokm document show riokm/01a19408-563c-76b1-ab8e-6e1eb767397a
```

Use this repository's linked space, a configured RioKM home space, or run the
command from an authenticated checkout of `richandersonio/riokm`. If the active
standard cannot be read, stop before changing the repository or opening a PR.
Fetch the actual PR target, integrate its latest tip, complete required validation
and independent review on that result, and recheck freshness immediately before
PR creation and merge. Record target and tested head/tree SHAs. If the target
advances, repeat integration and required checks. No exceptions or bypasses.
<!-- rio-git-protocol:end -->

## Changelog before every commit

Before every commit, review the intended changes and bring the root
`CHANGELOG.md` up to date (using the `rich-changelog` skill when available).
Follow this style:

- **Date-based completed-work history, newest date first:** Group work under
  ISO local date headings (`## YYYY-MM-DD`) with outcome-led bullets directly
  beneath each date heading (no `### Outcomes` subheading).
- **Bold scope-led titles:** Start each bullet with `**Scope — outcome:**`
  leading with user-visible behavior, functionality added, compatibility
  impact, or operational improvements rather than a raw commit list.
- **Consolidate to final outcomes:** Merge related commits and follow-up fixes
  for the same capability into one accurate entry; amend an existing entry for
  the same date rather than duplicating it.
- **Verify against the staged diff:** Record only evidenced behavior and check
  formatting with `git diff --check` before committing.
