# Security Policy

## Supported versions

Security fixes are made on the latest release on the `main` branch.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, report
them privately through GitHub's
[private vulnerability reporting](https://github.com/richandersonio/tv_cast/security/advisories/new).

Include the affected version or commit, steps to reproduce, and the impact you
observed. You should get an acknowledgement within a few days, and we will keep
you updated until a fix is released.

## Scope notes

TV Cast is designed for trusted home networks. It starts a temporary local HTTP
server that serves only HLS playlist and segment files, and it stores TV pairing
tokens in `~/.tv_cast/config.json` with owner-only permissions. Reports about
these areas are especially welcome.
