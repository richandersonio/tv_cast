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
