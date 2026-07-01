"""with_repo_mount binds the cloned repo into the app service's workdir.

The host path is a runtime binding, kept out of the pure Runbook->compose lowering
(ADR-0003) and applied by/for the executor instead.
"""

from repo_pilot.compose import with_repo_build, with_repo_mount


def test_binds_repo_to_app_working_dir():
    compose = {
        "services": {
            "app": {
                "image": "node:20-bookworm",
                "working_dir": "/workspace/repo",
                "command": ["sh", "-c", "npm start"],
            }
        }
    }
    out = with_repo_mount(compose, "/host/clone")
    assert out["services"]["app"]["volumes"] == ["/host/clone:/workspace/repo"]
    # pure: original untouched
    assert "volumes" not in compose["services"]["app"]


def test_no_op_without_app_service():
    compose = {"services": {"postgres": {"image": "postgres:16"}}}
    assert with_repo_mount(compose, "/host/clone") == compose


def test_with_repo_build_replaces_image_with_build_context():
    compose = {
        "services": {
            "app": {
                "image": "node:20-bookworm",
                "working_dir": "/workspace/repo",
                "command": ["sh", "-c", "npm start"],
            }
        }
    }
    out = with_repo_build(compose, "/host/clone", "Dockerfile.repopilot")
    app = out["services"]["app"]
    assert app["build"] == {"context": "/host/clone", "dockerfile": "Dockerfile.repopilot"}
    assert "image" not in app
    # pure: original untouched
    assert "build" not in compose["services"]["app"]
