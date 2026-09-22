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
    args = parser.parse_args()
    if args.command == "index":
        db = SearchDatabase(args.database)
        stats = Indexer(db).index_root(args.root)
        print(f"Indexed {stats.scanned} files.")
        print(stats.summary())
    else:
        for result in SearchEngine(SearchDatabase(args.database)).search(args.query, args.limit):
            print(f"[{result.source}] {result.name}\n  {result.path}\n  {result.snippet or ''}\n")
