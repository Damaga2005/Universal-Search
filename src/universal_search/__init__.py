"""Universal Search package."""

# Single source of truth for the product version:
# pyproject (hatch dynamic), packaging/version_file.txt, packaging/installer.iss
# and the GUI title all follow it; tests/test_release.py fails on any drift.
__version__ = "1.0.0"
