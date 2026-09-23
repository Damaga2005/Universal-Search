"""``python -m evaluation`` — run the phase-013 ranking evaluation.

Examples (repo root):

    python -m evaluation                        # labelled corpus, P@K/R@K/MRR
    python -m evaluation --top 5                # show more of each ranking
    python -m evaluation --json results.json    # raw measurements
    python -m evaluation --write-fixture evaluation/baseline.json
"""

import argparse
import logging
from pathlib import Path

from evaluation import experiments
from evaluation.runner import DEFAULT_LIMIT, K_VALUES, run


def main() -> None:
    # The corpus contains one deliberately unreadable binary so the
    # metadata-only path is exercised; pypdf logs about it on stderr and
    # that noise would only obscure the report.
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    parser = argparse.ArgumentParser(
        prog="python -m evaluation",
        description="Ranking-quality evaluation over the labelled corpus (phase 013).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"results requested per query (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        default=list(K_VALUES),
        help="cut-offs for Precision@K / Recall@K (default: 1 3 5)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="how many ranked ids to print per query (default: 3)",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="also write the raw measurements to this JSON file",
    )
    parser.add_argument(
        "--write-fixture",
        type=Path,
        default=None,
        help="write the current ranking as the regression baseline",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary corpus instead of deleting it",
    )
    parser.add_argument(
        "--flip",
        nargs=2,
        metavar=("WEIGHT", "QUERY"),
        default=None,
        help=(
            "instead of the full report, report the value at which WEIGHT "
            "would change the top ranking of QUERY (e.g. --flip recency diagrama)"
        ),
    )
    args = parser.parse_args()
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    if args.flip:
        weight, query = args.flip
        result = experiments.run(weight, query)
        print(f"weight={weight}  current={result['default_value']}  query={query!r}")
        print(f"  default top:   {result['default_top']}")
        print(
            "  load-bearing:  "
            + ("yes" if result["load_bearing"] else "no (ranking unchanged at 0)")
        )
        if result["load_bearing"]:
            print(f"  top at zero:   {result['top_at_zero']}")
        boundary = result["flip_up_at"]
        if boundary is None:
            print(
                f"  headroom:      no change up to {experiments.DEFAULT_HIGH} "
                f"({experiments.DEFAULT_HIGH / max(result['default_value'], 1e-9):.1f}x)"
            )
        else:
            print(
                f"  headroom:      changes at {boundary:.3f} "
                f"({result['headroom_ratio']:.1f}x the current value)"
            )
            print(f"  top at flip:   {result['top_at_flip']}")
        return
    run(
        limit=args.limit,
        k_values=tuple(args.k),
        top=args.top,
        json_out=args.json,
        keep=args.keep,
        write_fixture=args.write_fixture,
    )


if __name__ == "__main__":
    main()
