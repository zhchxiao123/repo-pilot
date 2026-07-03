"""Tests for deterministic run-shape detection (Task 5)."""

from __future__ import annotations

from repo_pilot.shape_detection import detect_shapes


def test_detects_node_service_from_start_script():
    profile = {
        "languages": ["javascript"],
        "frameworks": ["express"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "script", "key": "start", "command": "node index.js"}],
    }
    hints = detect_shapes(profile, [])
    assert hints.primary.shape == "service"


def test_detects_library_from_package_without_start_but_with_tests():
    profile = {
        "languages": ["javascript"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "script", "key": "test", "command": "npm test"}],
    }
    hints = detect_shapes(profile, [])
    assert hints.primary.shape == "library"


def test_detects_cli_from_bin_entrypoint():
    profile = {
        "languages": ["javascript"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "binary", "key": "mytool", "command": "mytool"}],
    }
    assert detect_shapes(profile, []).primary.shape == "cli"


def test_detects_build_from_build_script_without_start():
    profile = {
        "languages": ["javascript"],
        "package_managers": ["npm"],
        "entrypoints": [{"type": "script", "key": "build", "command": "npm run build"}],
    }
    assert detect_shapes(profile, []).primary.shape == "build"


def test_no_runnable_evidence_is_docs():
    profile = {"languages": ["markdown"], "entrypoints": []}
    assert detect_shapes(profile, []).primary.shape == "docs"


def test_service_start_beats_a_test_script():
    # A repo with both start and test is a service; the test script is just its
    # suite, not a library shape (library only when there is no service start).
    profile = {
        "languages": ["javascript"],
        "frameworks": ["express"],
        "package_managers": ["npm"],
        "entrypoints": [
            {"type": "script", "key": "start", "command": "node index.js"},
            {"type": "script", "key": "test", "command": "npm test"},
        ],
    }
    hints = detect_shapes(profile, [])
    assert hints.primary.shape == "service"
    assert "library" not in [h.shape for h in hints.hints]


def test_compose_evidence_hints_multi_component_service():
    hints = detect_shapes({"entrypoints": []}, [{"kind": "compose_service"}])
    assert hints.primary.shape == "multi_component_service"
