#!/usr/bin/env python3
"""YTDL-UI entry point. Run `python server.py --help` for options."""

import sys

if sys.version_info < (3, 10):
    sys.exit("YTDL-UI needs Python 3.10 or newer.")

from ytdlui.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
