"""Entry point for running as a module: python -m tv_cast"""

import atexit
import signal
import sys
from typing import Any

from .casting import cleanup_on_exit
from .cli import run_cli


def _sigterm_handler(sig: int, frame: Any) -> None:
    """Exit cleanly on SIGTERM so atexit/finally hooks run outside signal context."""
    sys.exit(0)


def main() -> None:
    """Main entry point."""
    atexit.register(cleanup_on_exit)
    # Leave SIGINT as Python's default handler so KeyboardInterrupt works properly
    # for interactive menus, ffmpeg cancellation, and playback stopping.
    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        run_cli()
    except KeyboardInterrupt:
        cleanup_on_exit()
        print("👋 Goodbye!")


if __name__ == "__main__":
    main()
