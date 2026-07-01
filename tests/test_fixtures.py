"""At least one fixture repo is checked in and loadable by the test harness.

Fixture repos are the driver for the pipeline's end-to-end tests in later slices
(the primary behavioral seam), so the harness that locates them is established here.
"""

import json


def test_express_fixture_is_present_and_loadable(fixture_repo):
    path = fixture_repo("express-min")
    assert path.is_dir()
    pkg_file = path / "package.json"
    assert pkg_file.is_file()
    pkg = json.loads(pkg_file.read_text())
    assert "express" in pkg.get("dependencies", {})


def test_unknown_fixture_raises(fixture_repo):
    import pytest

    with pytest.raises(FileNotFoundError):
        fixture_repo("does-not-exist")
