"""Controlled compose import: a safe subset of a target repo's compose file
becomes a canonical RunPlan (plan Phase 2, Tasks 2.1/2.2).

Pure: fixture compose files are written to tmp_path; no Docker, no LLM. The
importer must never pass through unsafe compose (socket mounts, host
namespaces, absolute mounts) — those defer as ``unsafe-compose``; anything
beyond the supported subset defers as ``needs-compose``.
"""

import pytest
import yaml

from repo_pilot.compose_import import import_compose
from repo_pilot.run_shape import RunShape, normalize_plan


def _repo(tmp_path, compose, name="docker-compose.yml"):
    text = compose if isinstance(compose, str) else yaml.safe_dump(compose)
    (tmp_path / name).write_text(text)
    return tmp_path


def _safe_app_db():
    return {
        "services": {
            "web": {
                "build": {"context": "."},
                "command": "python app.py",
                "ports": ["8000:8000"],
                "environment": {"DATABASE_URL": "postgres://app:app@db/app"},
                "depends_on": ["db"],
            },
            "db": {
                "image": "postgres:16",
                "environment": {"POSTGRES_PASSWORD": "app"},
                "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U postgres"]},
            },
        }
    }


# --- the happy path: app + managed db -> multi-component plan ----------------


def test_app_plus_db_imports_as_multi_component_service(tmp_path):
    result = import_compose(_repo(tmp_path, _safe_app_db()))

    assert result.deferred_reason is None
    plan = result.plan
    assert plan is not None and plan.shape == RunShape.MULTI_COMPONENT_SERVICE
    normalize_plan(plan)  # imported plans must pass the canonical invariants

    web = next(c for c in plan.components if c.name == "web")
    db = next(c for c in plan.components if c.name == "db")

    # repo-code service: inferred runtime image, default workdir, http oracle on
    # the container port, depends_on preserved
    assert web.image == "python:3.11"
    assert web.workdir == "/workspace/repo"
    assert web.command == "python app.py"
    assert web.ports == [8000]
    assert web.depends_on == ["db"]
    assert web.oracle.type == "http" and web.oracle.port == 8000
    assert web.env["DATABASE_URL"] == "postgres://app:app@db/app"

    # managed dependency: keeps its image, no command, healthcheck -> native-cmd
    assert db.image == "postgres:16" and db.command is None
    assert db.oracle.type == "native-cmd"
    assert db.oracle.command == "pg_isready -U postgres"


def test_import_folds_install_step_when_manifest_present(tmp_path):
    repo = _repo(tmp_path, _safe_app_db())
    (repo / "requirements.txt").write_text("flask\n")
    result = import_compose(repo)
    web = next(c for c in result.plan.components if c.name == "web")
    assert web.command == "pip install -r requirements.txt && python app.py"


def test_image_plus_command_service_is_repo_code(tmp_path):
    compose = {
        "services": {
            "app": {
                "image": "python:3.12-slim",
                "command": ["python", "app.py"],
                "working_dir": "/srv/app",
                "ports": [{"target": 5000, "published": 8080}],
            }
        }
    }
    result = import_compose(_repo(tmp_path, compose))
    app = result.plan.components[0]
    assert result.plan.shape == RunShape.SERVICE  # single component
    assert app.image == "python:3.12-slim"  # explicit image wins over inference
    assert app.workdir == "/srv/app"  # declared working_dir is kept
    assert app.command == "python app.py"  # list command joined
    assert app.ports == [5000]  # container target, not published
    assert app.oracle.type == "http" and app.oracle.port == 5000


def test_repo_code_without_ports_gets_process_up_oracle(tmp_path):
    compose = {"services": {"worker": {"build": {"context": "."}, "command": "python worker.py"}}}
    result = import_compose(_repo(tmp_path, compose))
    assert result.plan.components[0].oracle.type == "process-up"


def test_unknown_image_only_service_is_managed_dependency_with_warning(tmp_path):
    compose = _safe_app_db()
    compose["services"]["proxy"] = {"image": "nginx:1.25"}
    result = import_compose(_repo(tmp_path, compose))
    proxy = next(c for c in result.plan.components if c.name == "proxy")
    assert proxy.command is None and proxy.oracle.type == "process-up"
    assert any("proxy" in w for w in result.warnings)


def test_dependency_without_healthcheck_gets_process_up(tmp_path):
    compose = _safe_app_db()
    del compose["services"]["db"]["healthcheck"]
    result = import_compose(_repo(tmp_path, compose))
    db = next(c for c in result.plan.components if c.name == "db")
    assert db.oracle.type == "process-up"


# --- normalization of compose field variants ---------------------------------


def test_field_variants_normalize(tmp_path):
    compose = {
        "services": {
            "web": {
                "build": {"context": ".", "dockerfile": "Dockerfile"},
                "command": "npm start",
                "ports": ["127.0.0.1:8080:3000"],
                "environment": ["NODE_ENV=production", "EMPTY"],
                "depends_on": {"cache": {"condition": "service_healthy"}},
            },
            "cache": {
                "image": "redis:7",
                "healthcheck": {"test": ["CMD", "redis-cli", "ping"]},
            },
        }
    }
    result = import_compose(_repo(tmp_path, compose))
    web = next(c for c in result.plan.components if c.name == "web")
    cache = next(c for c in result.plan.components if c.name == "cache")

    assert web.image == "node:20-bookworm"  # inferred from npm
    assert web.ports == [3000]  # host-interface prefix stripped
    assert web.env == {"NODE_ENV": "production", "EMPTY": ""}  # list form
    assert web.depends_on == ["cache"]  # dict form
    assert cache.oracle.command == "redis-cli ping"  # CMD list joined


def test_benign_fields_are_stripped_with_warning(tmp_path):
    compose = _safe_app_db()
    compose["services"]["web"]["restart"] = "unless-stopped"
    compose["services"]["db"]["volumes"] = ["pgdata:/var/lib/postgresql/data"]
    compose["volumes"] = {"pgdata": None}
    result = import_compose(_repo(tmp_path, compose))
    assert result.plan is not None  # still imports
    assert any("volumes" in w for w in result.warnings)


@pytest.mark.parametrize("name", ["docker-compose.yaml", "compose.yml", "compose.yaml"])
def test_alternate_compose_filenames_are_found(tmp_path, name):
    result = import_compose(_repo(tmp_path, _safe_app_db(), name=name))
    assert result.plan is not None


# --- unsafe compose is deferred, never executed ------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["web"].__setitem__("privileged", True),
        lambda s: s["web"].__setitem__("network_mode", "host"),
        lambda s: s["web"].__setitem__("pid", "host"),
        lambda s: s["web"].__setitem__("ipc", "host"),
        lambda s: s["web"].__setitem__(
            "volumes", ["/var/run/docker.sock:/var/run/docker.sock"]
        ),
        lambda s: s["web"].__setitem__("volumes", ["/etc:/host-etc"]),
        lambda s: s["web"].__setitem__("build", {"context": "../outside"}),
    ],
    ids=["privileged", "net-host", "pid-host", "ipc-host", "docker-sock", "abs-mount", "context-escape"],
)
def test_unsafe_compose_defers_as_unsafe(tmp_path, mutate):
    compose = _safe_app_db()
    mutate(compose["services"])
    result = import_compose(_repo(tmp_path, compose))
    assert result.plan is None
    assert result.deferred_reason == "unsafe-compose"


# --- beyond the supported subset -> needs-compose ----------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["web"].__setitem__("extends", {"service": "base"}),
        lambda s: s["web"].__setitem__("deploy", {"replicas": 2}),
        lambda s: s["web"].__setitem__("env_file", ".env"),
        lambda s: s["web"]["environment"].__setitem__("SECRET", "${SECRET_KEY}"),
        lambda s: s["web"].pop("command"),  # build-only: runtime lives in Dockerfile (Phase 3)
    ],
    ids=["extends", "deploy", "env-file", "interpolation", "no-command"],
)
def test_unsupported_compose_defers_as_needs_compose(tmp_path, mutate):
    compose = _safe_app_db()
    mutate(compose["services"])
    result = import_compose(_repo(tmp_path, compose))
    assert result.plan is None
    assert result.deferred_reason == "needs-compose"
    assert result.warnings  # says why


def test_compose_with_only_database_services_returns_no_plan(tmp_path):
    compose = {
        "services": {
            "db": {"image": "postgres:16"},
            "cache": {"image": "redis:7"},
        }
    }
    result = import_compose(_repo(tmp_path, compose))
    assert result.plan is None
    assert result.deferred_reason == "needs-compose"


def test_unparseable_compose_defers(tmp_path):
    result = import_compose(_repo(tmp_path, "services: [unclosed"))
    assert result.plan is None and result.deferred_reason == "needs-compose"


def test_missing_compose_file_returns_empty_result(tmp_path):
    result = import_compose(tmp_path)
    assert result.plan is None and result.deferred_reason is None
