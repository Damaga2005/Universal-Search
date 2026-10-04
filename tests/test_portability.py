"""Phase 048: the claims, the CI and the docs must say the same thing.

Phase 048 exists because of one measurable mismatch: `requires-python` said
`>=3.12` -- an open promise covering 3.15, 3.16 and everything after -- while the
CI proved exactly one version and the development machine ran a fourth. A
project that develops on a version its own CI never runs is making a support
claim it cannot back.

Two things had to be true afterwards, and both are cheap to check and easy to
break by accident:

  * the promise is a bounded range and the CI covers exactly that range;
  * the package says what platform it is for, and does not say more than the
    matrix supports.

The third group is about the interpreter this suite is running on, which is the
check that would have caught the original problem.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

from evaluation import portability_gate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


@pytest.fixture(scope="module")
def ci() -> str:
    return (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


# -- the promise is bounded ---------------------------------------------------

def test_requires_python_has_an_upper_bound(project):
    """An open range is a promise about versions nobody has run.

    `>=3.12` claims 3.15, 3.16 and everything after. The upper bound costs a
    `pyproject.toml` edit when 3.15 ships, which is the right price for refusing
    to promise what has not been measured.
    """
    spec = project["requires-python"]
    assert "<" in spec, f"requires-python={spec!r} promises versions without bound"
    _floor, ceiling, inclusive = portability_gate._parse_range(spec)
    assert ceiling is not None


def test_the_upper_bound_is_exclusive_and_that_matters():
    """`>=3.12,<3.15` allows 3.14 and not 3.15.

    The first version of the gate treated the bound as inclusive and reported a
    version in the range that the CI did not prove -- on a range and a matrix
    that agreed perfectly. The off-by-one was in the instrument, and it is
    pinned here because the same mistake in the other direction would let a
    version nobody tested pass as declared.
    """
    _floor, ceiling, inclusive = portability_gate._parse_range(">=3.12,<3.15")
    assert ceiling == "3.15"
    assert inclusive is False

    declared = portability_gate.Declared(
        requires_python=">=3.12,<3.15", floor="3.12", ceiling="3.15",
        ceiling_inclusive=False, classifiers=(), ci_versions=(),
        probe_marked_non_gating=True, support_doc="",
    )
    versions = declared.declared_versions
    assert "3.12" in versions and "3.13" in versions and "3.14" in versions
    assert "3.15" not in versions, (
        "an exclusive upper bound must not admit the version it excludes"
    )

    inclusive_declared = portability_gate.Declared(
        requires_python=">=3.12,<=3.15", floor="3.12", ceiling="3.15",
        ceiling_inclusive=True, classifiers=(), ci_versions=(),
        probe_marked_non_gating=True, support_doc="",
    )
    assert "3.15" in inclusive_declared.declared_versions, (
        "`<=` is inclusive and must say so; the two spellings allow different "
        "sets of interpreters"
    )


# -- the promise and the proof are the same set -------------------------------

def test_the_ci_covers_exactly_the_declared_range(project, ci):
    """A promise with no proof is marketing; a proof nobody needs is CI minutes."""
    _floor, _ceiling, _inclusive = portability_gate._parse_range(
        project["requires-python"]
    )
    declared = portability_gate.gather()
    proven = set(declared.ci_versions)

    assert proven, (
        "the gating job pins a version instead of declaring a matrix, so the "
        "declared range cannot be covered"
    )
    allowed = set(declared.declared_versions)
    for version in proven:
        assert version in allowed, (
            f"the CI proves {version}, which the package does not declare"
        )
    # The versions worth naming, not all 300 minors between the bounds.
    for version in ("3.12", "3.13", "3.14"):
        if version in allowed:
            assert version in proven, (
                f"{version} is inside the declared range and the CI does not "
                f"prove it"
            )


def test_the_gating_job_is_the_one_with_the_matrix(ci):
    """Only the gating job counts as evidence.

    The Linux probe and the packaging job also name a Python version. Counting
    those would let a job explicitly marked `continue-on-error` masquerade as
    proof.
    """
    gating = ci.split("  core-portability:")[0]
    assert "matrix:" in gating, "the gating job has no version matrix"
    assert "python:" in gating


# -- the package says what it is ----------------------------------------------

def test_platform_classifiers_exist(project):
    """There were none. A package that ships a Windows-only application and
    declares nothing gets its platform guessed by whoever installs it."""
    classifiers = project.get("classifiers", [])
    for required in (
        "Operating System :: Microsoft :: Windows",
        "Operating System :: Microsoft :: Windows :: Windows 10",
        "Operating System :: Microsoft :: Windows :: Windows 11",
    ):
        assert required in classifiers, f"missing classifier: {required}"


def test_no_classifier_promotes_an_unsupported_platform(project):
    """The Ubuntu job exists, and it is a probe. A classifier reads as a claim."""
    classifiers = project.get("classifiers", [])
    for forbidden in ("POSIX", "MacOS", "Linux", "OS Independent"):
        offenders = [c for c in classifiers if forbidden in c]
        assert not offenders, (
            f"{offenders} would advertise a platform docs/SUPPORT.md "
            f"declares unsupported"
        )


def test_declared_python_versions_agree_with_the_classifiers(project):
    """Two places declare Python versions. They must not drift."""
    spec = project["requires-python"]
    floor, ceiling, _inclusive = portability_gate._parse_range(spec)
    declared = portability_gate.Declared(
        requires_python=spec, floor=floor, ceiling=ceiling,
        ceiling_inclusive=_inclusive, classifiers=(), ci_versions=(),
        probe_marked_non_gating=True, support_doc="",
    )
    allowed = set(declared.declared_versions)
    named = {
        line.rsplit(" ", 1)[-1].strip()
        for line in project.get("classifiers", [])
        if line.startswith("Programming Language :: Python :: 3.")
    }
    assert named, "no minor Python version is named in the classifiers"
    for version in named:
        assert version in allowed, (
            f"the classifiers name {version}, outside requires-python {spec!r}"
        )


# -- the interpreter running the tests ---------------------------------------

def test_this_interpreter_is_inside_the_declared_range(project):
    """The check that would have caught the original mismatch.

    The project developed and ran on a version its CI never proved. This
    asserts the narrower, more useful thing: whatever runs the suite is
    something the package actually claims to support.
    """
    declared = portability_gate.gather()
    runtime = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert declared.declared_versions, "the declared range could not be read"
    assert runtime in set(declared.declared_versions), (
        f"the suite runs on Python {runtime}, which requires-python "
        f"{project['requires-python']!r} does not cover. Either the range is "
        f"too narrow or the environment is outside the support promise."
    )


# -- support versus probe -----------------------------------------------------

def test_the_linux_job_is_a_probe_not_a_claim(ci):
    """A probe that blocks releases is a support claim in disguise."""
    probe = ci.split("  core-portability:")[-1]
    assert "continue-on-error: true" in probe, (
        "the Linux job would block a release, which means it is a support "
        "claim rather than a probe"
    )
    assert "ubuntu" in probe.lower()


def test_macos_is_not_mentioned_as_tested(ci):
    """No job, no measurement, therefore no claim."""
    assert "macos" not in ci.lower(), (
        "a macOS job would make macOS a tested platform; if one is added, "
        "docs/SUPPORT.md has to say what it means"
    )


def test_the_support_document_separates_support_from_probe():
    """The distinction has to be written down, not implied."""
    support = ROOT / "docs" / "SUPPORT.md"
    assert support.exists(), "docs/SUPPORT.md is missing"
    text = support.read_text(encoding="utf-8")
    assert "sonda" in text.lower(), "the probe is never named"
    assert "no se declara soportado" in text.lower(), (
        "there is no explicit list of what is not supported"
    )
    # The three platforms the boundary depends on.
    assert "Windows 10" in text
    assert "Windows 11" in text
    assert "macOS" in text, (
        "macOS has to appear, precisely in order to say it is not supported"
    )


def test_the_support_document_names_the_runtime_it_was_measured_on():
    """A matrix that does not say when it was measured rots silently."""
    text = (ROOT / "docs" / "SUPPORT.md").read_text(encoding="utf-8")
    assert re.search(r"3\.14", text), (
        "the measured interpreter version is not recorded; the matrix has to "
        "say which environment produced it"
    )
    assert re.search(r"(Tcl/Tk|tk)\s*\**8\.6", text, re.I) or "8.6" in text, (
        "the measured Tk version is not recorded"
    )