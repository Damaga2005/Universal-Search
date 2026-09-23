"""PyInstaller entry point for Universal Search.

Dispatcher: with arguments it behaves like the ``universal-search`` CLI
(workers spawned as ``UniversalSearch.exe indexer run`` land here), with no
arguments it launches the desktop search window. The same script backs both
the windowed exe and the console exe in the spec.
"""

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from universal_search.cli import main as cli_main

        cli_main()  # may raise SystemExit with the command's code
        return 0
    from universal_search.gui.app import run

    return run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as exit_info:
        sys.exit(exit_info.code)
