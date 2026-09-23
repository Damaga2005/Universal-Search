import argparse
import sys
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine


def main() -> None:
    # Windows consoles default to a legacy code page; never crash while printing results.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="universal-search")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index")
    index.add_argument("root", type=Path)
    index.add_argument("--database", type=Path, default=Path("universal-search.db"))
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--database", type=Path, default=Path("universal-search.db"))
    search.add_argument("--limit", type=int, default=20)
    sub.add_parser("gui", help="launch the desktop search window")
    indexer = sub.add_parser("indexer", help="background indexer lifecycle")
    indexer_sub = indexer.add_subparsers(dest="indexer_command", required=True)
    indexer_sub.add_parser("run", help="run the worker in this process")
    indexer_sub.add_parser("start", help="start the worker in the background")
    indexer_sub.add_parser("stop", help="stop the running worker")
    indexer_sub.add_parser("status", help="show worker status")
    indexer_sub.add_parser("pause", help="pause indexing")
    indexer_sub.add_parser("resume", help="resume indexing")
    autostart = indexer_sub.add_parser("autostart", help="start with Windows")
    autostart.add_argument("choice", choices=("on", "off", "status"))
    args = parser.parse_args()
    if args.command == "index":
        db = SearchDatabase(args.database)
        stats = Indexer(db).index_root(args.root)
        print(f"Indexed {stats.scanned} files.")
        print(stats.summary())
    elif args.command == "gui":
        from universal_search.gui.app import run

        raise SystemExit(run())
    elif args.command == "indexer":
        raise SystemExit(_indexer_command(args))
    else:
        for result in SearchEngine(SearchDatabase(args.database)).search(args.query, args.limit):
            print(f"[{result.source}] {result.name}\n  {result.path}\n  {result.snippet or ''}\n")


def _indexer_command(args) -> int:
    """Presentation for the background-indexer lifecycle (logic lives elsewhere)."""
    from universal_search import background
    from universal_search.appconfig import setup_logging

    setup_logging()
    command = args.indexer_command
    if command == "run":
        return background.BackgroundIndexer().run()
    if command == "start":
        state, message = background.start()
        print(message)
        return 0 if state in ("started", "already-running") else 1
    if command == "stop":
        state, message = background.stop()
        print(message)
        return 0 if state in ("stopped", "not-running") else 1
    if command == "status":
        print(background.status_report())
        return 0
    if command == "pause":
        background.pause()
        print("indexación pausada")
        return 0
    if command == "resume":
        background.resume()
        print("indexación reanudada")
        return 0
    if command == "autostart":
        if args.choice == "status":
            print("activado" if background.get_autostart() else "desactivado")
            return 0
        background.set_autostart(args.choice == "on")
        print(
            "se iniciará con Windows"
            if args.choice == "on"
            else "ya no se inicia con Windows"
        )
        return 0
    return 1  # pragma: no cover - argparse restricts the choices


if __name__ == "__main__":
    main()
