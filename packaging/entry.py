"""Entry point for the standalone (PyInstaller) builds."""

import multiprocessing
import sys

from ytdlui.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
