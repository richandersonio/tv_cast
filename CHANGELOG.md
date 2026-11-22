# Changelog

Date-based history of key changes to `tv-cast`, newest first.

## 2026-11-21

- **TV status & auto-restore:** Added `--tv-state` to show what your Samsung TV is currently displaying (including running apps and the active YouTube video, channel, and timestamp) and `--tv-apps` to list installed apps. Casting now remembers what was open and automatically restores it afterward, including resuming YouTube videos where you left off (`--no-restore` to skip).
- **Instant native YouTube casting:** Casting a YouTube URL now plays directly inside the TV's YouTube app with no download wait, falling back to local `yt-dlp` + HLS streaming when needed.
- **Dancing RIO logo (cached & live):** Added `--rio` (menu `r`) to cast a dancing RIO logo animation and `--rio-live` (menu `l`) to stream it continuously in real time until `Ctrl+C`.
- **Reliability & cross-platform fixes:** Fixed `Ctrl+C` interruption and cleanup, `ffmpeg` conversion deadlocks and partial cache bugs, macOS TV discovery, Linux ping/ARP handling, IPv6/mDNS crashes, menu `EOF` loops, and input validation. Expanded automated tests from 5 to 54 and added `ruff` linting in CI.
- **Security & dependency cleanup:** Removed unused dependencies (`pyatv`, `pillow`, `nmap`) and upgraded all remaining packages, resolving all 110 Dependabot alerts with zero known vulnerabilities. Tightened `~/.tv_cast` config permissions for stored TV pairing tokens.
- **Repository workflow:** Added `CHANGELOG.md` and updated `AGENTS.md` to keep it current before every commit. [PR #2](https://github.com/richandersonio/tv_cast/pull/2).

## 2026-10-31

- **Repository workflow:** Added `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` requiring the shared RioKM Git and PR standard before changes, commits, PRs, or merges. [PR #1](https://github.com/richandersonio/tv_cast/pull/1).

## 2026-05-09

- **Open-source release prep:** Updated dependency baselines, added MIT license metadata to `pyproject.toml`, added GitHub Actions CI, refreshed the README, and added initial unit tests.

## 2025-12-28

- **Initial release:** Launched `tv-cast` for casting local videos, YouTube links, and images to Samsung TVs over DLNA with automatic HLS conversion, caching, network discovery, CLI flags, and interactive menus.
