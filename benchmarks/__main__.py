"""``python -m benchmarks`` — run the phase-011 benchmark suite.

Examples (repo root):

    python -m benchmarks                        # profile 1000, ~15 s
    python -m benchmarks --profile 10000        # ~3 min
    python -m benchmarks --profile 100000 --memory   # heavy, on demand
    python -m benchmarks --json results.json    # raw numbers for records
"""

import argparse
from pathlib import Path

from benchmarks.suite import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m benchmarks",
        description="Deterministic local benchmark suite (phase 011).",
    )
    parser.add_argument(
        "--profile",
        type=int,
        default=1000,
        choices=(1000, 10000, 100000),
        help="corpus size (default: 1000; 100000 is the heavy on-demand profile)",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help="measure peak memory with tracemalloc (adds overhead to that run)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="also write raw results to this JSON file",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary corpus instead of deleting it",
    )
    args = parser.parse_args()
    run(args.profile, memory=args.memory, json_out=args.json, keep=args.keep)


if __name__ == "__main__":
    main()
