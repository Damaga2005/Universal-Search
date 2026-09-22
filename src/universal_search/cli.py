import argparse
from pathlib import Path

from universal_search.index.database import SearchDatabase
from universal_search.index.indexer import Indexer
from universal_search.index.search import SearchEngine
from universal_search.providers.local import discover_local


def main() -> None:
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
        indexer = Indexer(db)
        count = 0
        for document in discover_local(args.root):
            indexer.upsert(document)
            count += 1
        print(f"Indexed {count} files.")
    else:
        for result in SearchEngine(SearchDatabase(args.database)).search(args.query, args.limit):
            print(f"[{result.source}] {result.name}\n  {result.path}\n  {result.snippet or ''}\n")
