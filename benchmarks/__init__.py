"""Reproducible benchmark suite, separate from the unit-test suite.

Run from the repository root:

    python -m benchmarks --profile 1000
    python -m benchmarks --profile 10000 --memory

Profiles: 1000 (default, minutes), 10000, 100000 (heavy — on demand,
kept out of ordinary CI by design, spec 011). Datasets are deterministic
synthetic corpora; nothing here talks to a network or a service.
"""
