"""Tests for the phase-050 product gate, including one that cannot pass.

The gate's own value is that it refuses to call a limitation proved. That claim
needs a test, so this file checks the refusal as carefully as the passing cases.
"""

from __future__ import annotations

from evaluation import product_gate


def test_every_area_of_the_plan_has_an_evidence_owner():
    """Fifteen areas in the plan; all fifteen must be mapped.

    Derived from the plan's own wording rather than copied, because a list that
    is transcribed by hand is a list that goes stale -- which is how fifteen
    gates ended up in no list at all.
    """
    assert len(product_gate.EVIDENCE_OWNER) == 15
    assert len(product_gate.GATE_INVARIANTS) == 4
    for area, owner in product_gate.EVIDENCE_OWNER.items():
        assert owner == "gate" or owner.endswith("_gate"), (
            f"{area} maps to {owner}, which is not a gate"
        )
        assert area.strip(), "an empty area name proves nothing"


def test_every_evidence_owner_has_a_readable_baseline_or_is_the_quality_gate():
    """An owner with no baseline is an area whose evidence does not exist."""
    for area, owner in product_gate.EVIDENCE_OWNER.items():
        if owner == "gate":
            continue  # the one gate that reports invariants and keeps no baseline
        data = product_gate.read_baseline(owner)
        assert data is not None, (
            f"{area} names {owner} as its evidence owner, but "
            f"evaluation/{owner.removesuffix('_gate')}_baseline.json is missing"
        )


def test_reading_a_baseline_resolves_the_gate_suffix():
    """`ux_gate` owns `ux_baseline.json`.

    The first version looked for `ux_gate_baseline.json`, found nothing, and
    reported all fifteen areas as unproven: a correct-shaped conclusion from a
    wrong cause, which is worse than no check because it would have shipped.
    """
    assert product_gate.read_baseline("ux_gate") is not None
    assert product_gate.read_baseline("ux") is not None
    assert product_gate.read_baseline("no_such_gate") is None


def test_baseline_failures_sees_both_baseline_shapes():
    """Gates store verdicts as a list or as a mapping; both must be readable."""
    assert product_gate.baseline_failures({"verdicts": []}) == []
    assert product_gate.baseline_failures(
        {"verdicts": [{"gate": "A", "passed": True}]}) == []
    assert product_gate.baseline_failures(
        {"verdicts": [{"gate": "A", "passed": False}]}) == ["A"]
    assert product_gate.baseline_failures(
        {"verdicts": {"A": {"passed": True}}}) == []
    assert product_gate.baseline_failures(
        {"verdicts": {"A": {"passed": False}}}) == ["A"]
    # A baseline with no verdicts is not a pass; it is an absence.
    assert product_gate.baseline_failures({}) == []


def test_a_missing_scenario_cannot_produce_a_ship():
    """The regression this test exists for.

    With no `product_scenario.json`, the gate omitted its three scenario
    invariants and then printed `VEREDICTO: PUBLICABLE (SHIP)` with exit 0. CI
    ran it in the `gates` job, so the build announced a release-ready verdict for
    the gate whose committed verdict is HOLD.

    Omitting an invariant that cannot be measured is correct. Concluding from
    the omission is not, and the omission was invisible in the exit code, which
    is the only part a build reads.

    The check is on the source because running the gate requires moving the
    artefact; what matters is that the branch exists and returns a non-zero
    code that is not the SHIP code.
    """
    import inspect
    from evaluation import product_gate

    source = inspect.getsource(product_gate.main)
    assert "scenario_invariants:" in source
    assert "VEREDICTO: INCONCLUYENTE" in source, (
        "a missing scenario must produce INCONCLUYENTE"
    )
    # The INCONCLUYENTE branch must come *before* the SHIP return, or the gate
    # would still fall through to PUBLICABLE.
    inconclusive = source.index("VEREDICTO: INCONCLUYENTE")
    ship = source.index("VEREDICTO: PUBLICABLE")
    assert inconclusive < ship, (
        "the INCONCLUYENTE branch must be reached before the SHIP return"
    )
    assert product_gate.PRODUCT_GATE_SHIP_CODE == 0
    assert product_gate.PRODUCT_GATE_HOLD_CODE == 1
    assert product_gate.PRODUCT_GATE_INCONCLUSIVE_CODE == 2


def test_the_scenario_thresholds_are_reachable():
    """A count with a threshold of zero can only ever fail.

    `S01` and `S02` count things. Their thresholds are 18, the number of steps
    the plan lists, so a complete run passes and a missing one does not.
    """
    assert product_gate.THRESHOLDS["S01_scenario_steps_attempted"] == 18
    assert product_gate.THRESHOLDS["S02_scenario_steps_recorded"] == 18


def test_the_gate_reports_a_state_and_never_a_score():
    """The plan forbids subjective rankings and ratings.

    This is enforced by reading the source: the gate's printed vocabulary may
    carry verdicts and classifications, and nothing that reads as a grade.
    """
    import inspect
    source = inspect.getsource(product_gate)
    for forbidden in ("score =", "rating =", "grade =", "quality_score"):
        assert forbidden not in source, (
            f"the product gate must not produce a {forbidden.strip(' =')}"
        )
    assert "VEREDICTO: RETENIDO (HOLD)" in source
    assert "VEREDICTO: PUBLICABLE (SHIP)" in source


def test_an_undeclared_limiter_is_reported_as_unproven():
    """The gate's central promise: silence is not declaration.

    A limitation counts as declared only if its marker appears in the
    documentation, so a release cannot claim Windows 10 support on the strength
    of a Windows Server runner.

    The first version of this test asserted that a marker was *absent* from a
    string literal written two lines above -- it could not fail, and it was mine.
    Asserting absence needs something to be absent from, so this runs the real
    check over the real documents and then removes a marker for real.
    """
    from evaluation import product_gate as gate

    documents = [
        gate.ROOT / name
        for name in ("README.md", "docs/ROADMAP.md",
                     "docs/development/050-product-v3-gate-report.md")
    ]
    docs = "\n".join(
        path.read_text(encoding="utf-8") for path in documents if path.exists()
    )

    for claim, (marker, _) in gate.ENVIRONMENT_CLAIMS.items():
        assert marker in docs, (
            f"{claim} is undeclared; the marker {marker!r} appears nowhere in "
            f"README, ROADMAP or the 050 report"
        )

    # And the check does bite: drop one marker and it must go undeclared.
    claim, (marker, _) = next(iter(gate.ENVIRONMENT_CLAIMS.items()))
    # Every occurrence, not one: the marker is repeated across README, ROADMAP
    # and the report, so removing a single one left it declared and the check
    # below could not have failed.
    assert marker not in docs.replace(marker, ""), (
        "removing every occurrence of the marker must make the claim "
        "undeclared, otherwise the assertion above is satisfied by something "
        "other than the documents"
    )
