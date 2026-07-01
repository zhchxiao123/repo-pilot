"""Confidence = noisy-OR over evidence kinds + conflict discount (ADR-0011).

Worked examples come from docs/confidence-model.md (an independent source of truth).
"""

import pytest

from repo_pilot.confidence import confidence


def test_package_script_plus_readme():
    # 1 - (1-0.65)(1-0.70) = 0.895
    assert confidence(["package_script", "readme_command"]) == pytest.approx(0.895)


def test_ci_readme_package_script():
    # 1 - (1-0.85)(1-0.70)(1-0.65) = 0.98425
    assert confidence(
        ["ci_step", "readme_command", "package_script"]
    ) == pytest.approx(0.98425)


def test_duplicate_kinds_count_once():
    # two readme_command items are one distinct source
    assert confidence(["readme_command", "readme_command"]) == pytest.approx(0.70)


def test_conflict_discounts():
    # readme-only (0.70) contradicted by a package_manager conflict (r=0.65)
    # 0.70 * (1 - 0.5*0.65) = 0.4725
    assert confidence(
        ["readme_command"], conflict="package_manager"
    ) == pytest.approx(0.4725)


def test_empty_evidence_is_zero():
    assert confidence([]) == 0.0
