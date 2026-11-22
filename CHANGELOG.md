# Changelog

Date-based completed-work history for `tv-cast`, newest date first. Consolidate
related work under each local completion date (`## YYYY-MM-DD`) using
outcome-led bullets with bold scope-led titles (`**Scope — outcome:**`).

## 2026-11-21

- **Samsung TV state & auto-restore — detect running apps, active YouTube video, and restore after casting:** Added `--tv-state` to report a Samsung Tizen TV's power state, foreground and background apps (via bounded-concurrency `:8001` REST queries), active YouTube video title/channel/timestamp/URL (via local DIAL `<screenId>`, YouTube Lounge `getNowPlaying`, and oEmbed), and UPnP `AVTransport` status. Added `--tv-apps` to pair once over encrypted `wss://:8002` (storing the auth token in `0600` `~/.tv_cast/config.json` inside a `0700` directory) and cache installed apps using WebSocket `ed.installedApp.get` with automatic local REST catalog discovery for 2024+ Tizen firmware. Casting now snapshots active TV state and restores the previous foreground app (re-launching via `:8001` REST with authenticated `wss://:8002` `ed.apps.launch` fallback, and resuming active YouTube videos at their saved timestamp) or active DLNA stream after stopping; pass `--no-restore` to opt out. Preserves saved pairing metadata across same-IP device re-selections.
- **Native YouTube casting — instant playback in the TV's YouTube app:** Casting a YouTube URL now launches/uses the TV's native YouTube app via DIAL + Lounge (`setPlaylist`) for immediate playback without downloading or keeping a local HTTP server open, and automatically falls back to `yt-dlp` download + local DLNA HLS streaming when native YouTube casting is unavailable.
- **Dancing RIO logo — pre-rendered and live real-time streaming:** Added `--rio` (interactive menu `r`) to cast a cached 1080p dancing RIO logo animation built with zero extra dependencies (stdlib signed-distance-field RGBA PNG sprites + `ffmpeg`), and `--rio-live` (interactive menu `l`) to stream a continuous real-time rolling HLS feed (`-re`, `-tune zerolatency`, 2s segments with atomic playlist updates and segment deletion) until `Ctrl+C` or `-d <seconds>`.
- **Casting, conversion & discovery reliability — full codebase hardening:** Restored default `SIGINT` handling so `Ctrl+C` cleanly stops playback, cancels threaded `ffmpeg`/`yt-dlp` jobs cooperatively, and returns to menus; gated `atexit` stop cleanup on active playback with safe control URL/port resolution. Fixed `ffmpeg` `stderr` pipe deadlocks, made HLS/image cache writes atomic, required `#EXT-X-ENDLIST` for cache hits, excluded `*.tmp.*` from cache counts, and pinned H.264 transcodes to `yuv420p` High@L4.1 for Samsung `AVPlay` compatibility. Replaced `os.chdir()` + single-threaded `HTTPServer` with a reusable threaded HLS-only server (no directory listing, `Accept-Ranges: none`, `Cache-Control: no-cache`). Bound SSDP multicast discovery to `source=(local_ip, 0)` with unbound retry (fixing macOS `[Errno 65] No route to host`), fixed Linux `ping -W` timeout units and `ip neigh` ARP fallback, fixed mDNS IPv6 crashes, stopped marking AirPlay/Chromecast as DLNA-castable, preserved partial ARP results on deep-scan timeouts, hardened YouTube URL/video-ID/IP/duration validation, fixed directory handling and `EOFError` loops in menus, removed unused `pyatv`/`pillow`/`nmap` dependencies and stray `hello_world.jpg`, upgraded all remaining runtime and dev dependencies (`aiohttp` 3.14.5, `yt-dlp` 2026.10.26, `zeroconf` 0.154.0, `multidict` 7.2.1, `idna` 3.20, `pygments` 2.21.0, `pytest` 9.2.0) to resolve all 110 Dependabot alerts (`pip-audit` reports zero known vulnerabilities), added `ruff` linting to CI, and expanded automated coverage to 54 unit and local integration tests. [PR #2](https://github.com/richandersonio/tv_cast/pull/2).
- **Repository workflow — changelog maintenance rule:** Added root `CHANGELOG.md` and updated `AGENTS.md` to require updating `CHANGELOG.md` before every commit in the date-based, outcome-led style.

## 2026-10-31

- **Repository workflow — mandatory Git and PR protocol:** Added root `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md` requiring agents to read and follow the shared RioKM Git Workflow and Pull Request Standard before repository changes, commits, PR creation, or merge. [PR #1](https://github.com/richandersonio/tv_cast/pull/1).

## 2026-05-09

- **Packaging & security — open-source preparation:** Updated dependency baseline versions, added MIT license metadata to `pyproject.toml` (trimming the extra disclaimer from `LICENSE`), added a GitHub Actions CI workflow, updated README usage documentation, and added initial unit tests for config and utility helpers.

## 2025-12-28

- **Initial release — Samsung TV DLNA video caster:** Launched `tv-cast` with DLNA/mDNS/network device discovery, `ffmpeg` HLS conversion and caching, `yt-dlp` YouTube downloading, image-to-video casting, CLI flags, and interactive terminal menus.
