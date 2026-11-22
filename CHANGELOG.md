# Changelog

Date-based history of key changes to `tv-cast`, newest first.

## 2026-11-22

- **CLI:** Added `--version`, and `-v`/`--verbose` to show debug logs for errors that are normally skipped quietly. Use `uv run tv-cast`; the old `tv_cast.py` wrapper is gone.
- **Native YouTube casting — honours duration and Ctrl+C:** Videos played in the TV's YouTube app now stop when `-d` runs out, or on `Ctrl+C` when no duration is given, and then restore what was open. If the native cast fails, your original app is restored instead of YouTube.
- **TV state & restore accuracy:** `--tv-state` and auto-restore keep app results that arrive in time instead of discarding them all, cancel slow app checks cleanly on `Ctrl+C`, and report failure when the TV rejects an app launch.
- **Reliability:** `Ctrl+C` at device prompts returns straight to the menu. The config file (with your TV pairing token) is saved atomically, ffmpeg's output pipes are always closed, and failed casts only show tracebacks with `--verbose`.
- **Cache reporting:** Cache counts ignore leftover temporary files, and `--status` and `--clear-cache` list RIO animations separately from image videos.
- **Code quality & CI:** CI checks lint, formatting and types, tests Python 3.11–3.13 against the lockfile, and builds the package. 120 tests run with warnings treated as errors and a 70% coverage minimum (now 72%).
- **Project hygiene:** Added weekly Dependabot updates, pre-commit hooks, `CONTRIBUTING.md`, `SECURITY.md`, and issue and PR templates.

## 2026-11-21

- **TV status & auto-restore:** Added `--tv-state` to show what your Samsung TV is currently displaying (including running apps and the active YouTube video, channel, and timestamp) and `--tv-apps` to list installed apps. Casting now remembers what was open and automatically restores it afterward, including resuming YouTube videos where you left off (`--no-restore` to skip).
- **Instant native YouTube casting:** Casting a YouTube URL now plays directly inside the TV's YouTube app with no download wait, falling back to local `yt-dlp` + HLS streaming when needed.
- **Dancing RIO logo (cached & live):** Added `--rio` (menu `r`) to cast a dancing RIO logo animation and `--rio-live` (menu `l`) to stream it continuously in real time until `Ctrl+C`.
- **Reliability & cross-platform fixes:** Fixed `Ctrl+C` interruption and cleanup, `ffmpeg` conversion deadlocks and partial cache bugs, macOS TV discovery, Linux ping/ARP handling, IPv6/mDNS crashes, menu `EOF` loops, and input validation. Expanded automated tests from 5 to 54 and added `ruff` linting in CI.
- **Security & dependency cleanup:** Removed unused dependencies (`pyatv`, `pillow`, `nmap`) and upgraded all remaining packages, resolving all 110 Dependabot alerts. Tightened `~/.tv_cast` config permissions for stored TV pairing tokens.
- **Changelog:** Added this `CHANGELOG.md`.

## 2026-05-09

- **Open-source release prep:** Updated dependency baselines, added MIT license metadata to `pyproject.toml`, added GitHub Actions CI, refreshed the README, and added initial unit tests.

## 2025-12-28

- **Initial release:** Launched `tv-cast` for casting local videos, YouTube links, and images to Samsung TVs over DLNA with automatic HLS conversion, caching, network discovery, CLI flags, and interactive menus.
