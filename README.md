# TV Cast

Cast local videos, YouTube links and images to your Samsung TV over DLNA, from
the command line or an interactive menu.

[![CI](https://github.com/richandersonio/tv_cast/actions/workflows/ci.yml/badge.svg)](https://github.com/richandersonio/tv_cast/actions/workflows/ci.yml)
[![Python 3.11–3.13](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## Features

- **Cast anything:** local video files, YouTube URLs and still images. Videos
  are converted to HLS with ffmpeg and cached, so replays start instantly.
- **Native YouTube:** YouTube links play directly in the TV's YouTube app when
  possible, falling back to a local download and stream.
- **Find your TV:** DLNA, mDNS and network scans discover TVs, and the
  selected device is remembered.
- **TV state & auto-restore:** see what the TV is showing (`--tv-state`), and
  return to the previous app — even the same YouTube video and position — after
  casting.
- **Interactive menu:** browse local media, cached YouTube videos and devices
  without remembering flags.

## Requirements

- Python 3.11 or newer, managed with [uv](https://docs.astral.sh/uv/)
- [ffmpeg](https://ffmpeg.org/) on your `PATH`
- A Samsung TV (or other DLNA renderer) on the same network
- Optional: [deno](https://deno.com/) for more reliable YouTube format extraction

## Installation

```bash
git clone https://github.com/richandersonio/tv_cast.git
cd tv_cast
uv sync
```

Install ffmpeg with `brew bundle` on macOS (uses the included `Brewfile`) or
`sudo apt install ffmpeg` on Debian/Ubuntu.

## Usage

Run `uv run tv-cast` with no arguments for the interactive menu, or use flags:

### Casting

```bash
uv run tv-cast video.mp4                          # Cast a video until Ctrl+C
uv run tv-cast video.mp4 -d 60                    # Cast for 60 seconds
uv run tv-cast "https://youtube.com/watch?v=..."  # Cast a YouTube video
uv run tv-cast --image photo.jpg                  # Show an image (10 seconds)
uv run tv-cast --rio                              # Dancing RIO logo (15 seconds)
uv run tv-cast --rio-live                         # Live real-time RIO stream
uv run tv-cast --stop                             # Stop playback
uv run tv-cast video.mp4 --no-restore             # Don't restore the previous app afterwards
```

### Devices and TV state

```bash
uv run tv-cast --scan                 # Find TVs
uv run tv-cast --scan-all             # Deep network scan
uv run tv-cast --device 192.168.1.50  # Select a TV by IP
uv run tv-cast --select-device        # Choose a TV interactively
uv run tv-cast --list-devices         # List discovered devices
uv run tv-cast --status               # Show the selected TV and cache usage
uv run tv-cast --tv-state             # Show what the TV is currently displaying
uv run tv-cast --tv-apps              # Pair once and list installed TV apps
uv run tv-cast --forget               # Forget the selected TV
```

### Other

```bash
uv run tv-cast --clear-cache          # Delete cached conversions and downloads
uv run tv-cast --version              # Show the version
uv run tv-cast -v video.mp4           # Verbose debug logging on stderr
```

Settings, pairing tokens and caches live in `~/.tv_cast/`.

## How it works

TV Cast converts media to HLS, serves the segments from a temporary local HTTP
server, and tells the TV to play them through the UPnP AVTransport service.
Before casting it snapshots what the TV is showing using Samsung's local REST
and WebSocket APIs, and restores it when playback ends.

| Module | Responsibility |
| ------ | -------------- |
| `cli.py`, `menu.py` | Command-line flags and interactive menus |
| `discovery.py` | DLNA, mDNS and network device discovery |
| `conversion.py` | ffmpeg HLS conversion and caching |
| `casting.py` | HLS HTTP server and DLNA playback control |
| `youtube.py` | YouTube downloads via yt-dlp |
| `tv_state.py` | Samsung TV state, installed apps and auto-restore |
| `rio.py` | Dancing RIO logo rendering |
| `config.py`, `utils.py` | Settings, device state and shared helpers |

## Safety

TV Cast runs on your local network, starts a temporary HTTP server that serves
only HLS files, and downloads media from URLs you provide. Use it on trusted
networks, with media, devices and accounts you are authorized to use.

Please report security issues privately as described in
[SECURITY.md](SECURITY.md).

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and
the checks every pull request must pass.

## Disclaimer

TV Cast is an independent, unofficial project. It is not affiliated with,
endorsed by or sponsored by Samsung, Google, YouTube or the Digital Living
Network Alliance. Samsung, YouTube, DLNA and other names are trademarks of
their respective owners and are used here only to describe compatibility.

You are responsible for how you use TV Cast. Only cast or download content you
own or have permission to use, and follow the terms of service of any site you
download from, including YouTube's. TV Cast is provided "as is", without
warranty of any kind; see the [LICENSE](LICENSE).

## License

[MIT](LICENSE)
