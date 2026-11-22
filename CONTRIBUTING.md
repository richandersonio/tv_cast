# Contributing to TV Cast

Thanks for helping improve TV Cast! This guide covers local setup and what a
pull request needs before review.

## Setup

```bash
git clone https://github.com/richandersonio/tv_cast.git
cd tv_cast
uv sync --dev
uv run pre-commit install   # ruff, formatting and mypy on every commit
```

You also need `ffmpeg` on your `PATH` (`brew bundle` on macOS,
`sudo apt install ffmpeg` on Debian/Ubuntu).

## Before opening a pull request

Run the same checks as CI:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest               # fails if coverage drops below the configured minimum
```

- Add or update tests for any behaviour change; tests must not need a real TV.
- Keep changes focused, and update `README.md` when user-facing behaviour changes.
- Add a short entry to `CHANGELOG.md` (see the style notes in `AGENTS.md`).
- If you change dependencies, use `uv add` / `uv lock` and commit `uv.lock`.

## Reporting bugs and ideas

Open an [issue](https://github.com/richandersonio/tv_cast/issues/new/choose)
using the bug report or feature request template. For security problems, follow
[SECURITY.md](SECURITY.md) instead of filing a public issue.
