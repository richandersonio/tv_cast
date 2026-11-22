# TV Cast

Cast videos to your Samsung TV via DLNA.

[![CI](https://github.com/richandersonio/tv_cast/actions/workflows/ci.yml/badge.svg)](https://github.com/richandersonio/tv_cast/actions/workflows/ci.yml)

## Prerequisites

**macOS:**

```bash
brew install uv
```

**Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sudo apt install ffmpeg  # Debian/Ubuntu
```

## Setup

```bash
git clone https://github.com/richandersonio/tv_cast.git
cd tv_cast
uv sync --dev
brew bundle  # macOS only
```

## Usage

### Casting

```bash
uv run tv-cast video.mp4              # Cast video
uv run tv-cast video.mp4 -d 60        # Cast for 60 seconds
uv run tv-cast "https://youtube.com/watch?v=..."  # YouTube
uv run tv-cast --image photo.jpg      # Display image (10s)
uv run tv-cast --rio                  # Cast dancing RIO logo (15s)
uv run tv-cast --rio-live             # Live real-time dancing RIO stream
uv run tv-cast --tv-state             # Show what TV is currently showing
uv run tv-cast --tv-apps              # Pair once & list all installed TV apps
uv run tv-cast --rio --no-restore     # Skip auto-restoring previous TV app
uv run tv-cast --stop                 # Stop playback
```

**Alternative entry points:**

```bash
uv run python -m tv_cast video.mp4
uv run tv-cast video.mp4
```

### Device Management

```bash
uv run tv-cast --scan                 # Find TVs
uv run tv-cast --scan-all             # Deep network scan
uv run tv-cast --device 192.168.1.50  # Set TV by IP
uv run tv-cast --select-device        # Interactive device selection
uv run tv-cast --status               # Show current device
uv run tv-cast --list-devices         # List all devices
uv run tv-cast --forget               # Forget device
```

### Cache & Interactive Mode

```bash
uv run tv-cast --clear-cache          # Clear cache
uv run tv-cast                        # Interactive menu
uv run tv-cast --version              # Show version
uv run tv-cast -v video.mp4           # Verbose: show debug logging on stderr
```

## System Dependencies

| Tool   | Required | Purpose                                         |
| ------ | -------- | ----------------------------------------------- |
| ffmpeg | ✅ Yes   | Video conversion to HLS                         |
| deno   | Optional | YouTube format extraction (suppresses warnings) |

## Safety & Responsibility

TV Cast runs on your local network, starts a temporary HTTP server for HLS
segments, and can download media from third-party URLs when you provide them.
Use it only on trusted networks and only with media, devices, and accounts you
are authorized to use.

This project is provided without warranty or liability. See [LICENSE](LICENSE).

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```bash
uv sync --dev
uv run pre-commit install        # Run ruff + mypy on every commit
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest                    # Includes coverage with a minimum threshold
```

Security issues: please follow [SECURITY.md](SECURITY.md) rather than opening a
public issue.

## Project Structure

```
tv_cast/
├── tv_cast/             # Main package
│   ├── __init__.py      # Package metadata
│   ├── __main__.py      # Module entry point
│   ├── cli.py           # Command-line interface
│   ├── config.py        # Configuration management
│   ├── casting.py       # DLNA video casting
│   ├── conversion.py    # HLS/ffmpeg conversion
│   ├── discovery.py     # Device discovery (DLNA, mDNS)
│   ├── menu.py          # Interactive menus
│   ├── rio.py           # Dancing RIO logo animation
│   ├── tv_state.py      # Samsung TV state, apps, and restore
│   ├── utils.py         # Helper functions
│   └── youtube.py       # YouTube downloading
├── pyproject.toml       # Python dependencies
├── Brewfile             # System dependencies
├── tests/               # Unit tests
└── README.md
```

## Package Files

**`tv_cast/__init__.py`** - Package metadata (`__version__`, read from `pyproject.toml`)

**`tv_cast/__main__.py`** - Module entry point, signal handlers, cleanup on exit

**`tv_cast/config.py`** - Configuration management (device state, cache dirs, load/save)

**`tv_cast/utils.py`** - Network utilities, formatting helpers, YouTube URL validation

**`tv_cast/discovery.py`** - Device discovery via DLNA (UPnP), mDNS (Bonjour), network scanning

**`tv_cast/conversion.py`** - HLS video conversion (ffmpeg), caching, image-to-video

**`tv_cast/youtube.py`** - YouTube video downloading and caching via yt-dlp

**`tv_cast/casting.py`** - DLNA video casting, HTTP server for HLS segments, playback control

**`tv_cast/menu.py`** - Interactive menus (main, playback, device selection)

**`tv_cast/cli.py`** - Command-line argument parsing and CLI commands

**`tv_cast/rio.py`** - Dancing RIO logo rendering (cached video and live stream)

**`tv_cast/tv_state.py`** - Samsung TV state snapshot, installed apps, and auto-restore

## Common uv Commands

```bash
uv sync                          # Sync dependencies
uv add package-name              # Add dependency
uv lock --upgrade && uv sync    # Update all dependencies
uv run <command>                 # Run command in venv
```

## License

MIT License - see [LICENSE](LICENSE) for details.
