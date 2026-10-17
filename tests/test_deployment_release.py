from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

from nishtyak_service.config import Settings


def load_script(name, relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


configure = load_script("deployment_configure", "deploy/configure.py")
DeploymentConfig = configure.DeploymentConfig
EnvironmentWriter = configure.EnvironmentWriter
check_runtime = configure.check_runtime
ReleaseBuilder = load_script("deployment_release", "tools/build_release.py").ReleaseBuilder


@pytest.fixture
def release_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for relative in (
        ReleaseBuilder.ROOT_FILES | ReleaseBuilder.DEPLOY_FILES | ReleaseBuilder.PUBLIC_EXAMPLES
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime file\n")
    (source / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n')
    for relative in (
        "src/app.py",
        "src/web/App.vue",
        "src/web/site.css",
        "public/favicon.svg",
        "src/web/App.test.ts",
        "src/web/__tests__/test.ts",
        "src/secrets/secret.py",
        "src/.env",
        "src/private.key",
        "src/nishtyak_core.egg-info/PKG-INFO",
        "src/__pycache__/app.pyc",
        "public/credentials/account.json",
        "public/unexpected.zip",
        ".env",
        ".git/config",
        "docs/architecture.md",
        "tests/test_source.py",
        "notes.md",
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not a credential\n")
    return source


def test_release_is_deterministic_and_has_only_runtime_files(release_source, tmp_path):
    builder = ReleaseBuilder(release_source)
    archive, manifest = builder.build(tmp_path / "one")
    other_archive, other_manifest = builder.build(tmp_path / "two")
    assert archive.read_bytes() == other_archive.read_bytes()
    assert manifest.read_bytes() == other_manifest.read_bytes()
    proof = json.loads(manifest.read_text())
    assert proof["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        names = {item.name.removeprefix("nishtyak/") for item in members}
        assert names == (
            ReleaseBuilder.ROOT_FILES
            | ReleaseBuilder.DEPLOY_FILES
            | ReleaseBuilder.PUBLIC_EXAMPLES
            | {"src/app.py", "src/web/App.vue", "src/web/site.css", "public/favicon.svg"}
        )
        for member, item in zip(members, proof["files"], strict=True):
            assert member.name == "nishtyak/" + item["path"]
            assert member.uid == member.gid == 0
            assert 315_532_800 <= member.mtime < 1_389_274_624
            assert member.mode == 0o644
            assert member.isfile()
            assert hashlib.sha256(bundle.extractfile(member).read()).hexdigest() == item["sha256"]


def test_equal_size_source_edits_change_docker_context_metadata(release_source, tmp_path):
    source = release_source / "src/app.py"
    source.write_text('VERSION = "3.0"\n')
    first, _ = ReleaseBuilder(release_source).build(tmp_path / "before")
    with tarfile.open(first) as bundle:
        before = bundle.getmember("nishtyak/src/app.py")
    source.write_text('VERSION = "3.1"\n')
    second, _ = ReleaseBuilder(release_source).build(tmp_path / "after")
    with tarfile.open(second) as bundle:
        after = bundle.getmember("nishtyak/src/app.py")
    assert before.size == after.size
    assert before.mode == after.mode
    assert before.mtime != after.mtime
    assert first.read_bytes() != second.read_bytes()


@pytest.mark.parametrize("relative", ["src/link.py", "deploy/Dockerfile"])
def test_release_rejects_file_symlinks(release_source, relative):
    path = release_source / relative
    path.unlink(missing_ok=True)
    path.symlink_to(release_source / "README.md")
    with pytest.raises(ValueError, match="symbolic links"):
        ReleaseBuilder(release_source).collect()


def test_release_rejects_directory_symlinks_and_source_outputs(release_source, tmp_path):
    (release_source / "src" / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link directories"):
        ReleaseBuilder(release_source).collect()
    with pytest.raises(ValueError, match="outside"):
        ReleaseBuilder(release_source).build(release_source / "release")


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.org",
        "https://example.org/",
        "https://example.org/path",
        "https://alice:secret@example.org",
        "https://example.org?x=1",
        "https://example.org#fragment",
        "https://example.org\nNISHTYAK_SERVICE_TOKEN=other",
        "https://Example.org",
        "https://bad_hostname.org",
        "https://example.org:70000",
    ],
)
def test_configuration_rejects_ambiguous_origins(origin):
    with pytest.raises(ValueError):
        DeploymentConfig(origin, docker_gid=0).validate()


@pytest.mark.parametrize(
    ("origin", "domain"),
    [
        ("https://example.org", "other.org"),
        ("https://example.org:444", "example.org"),
        ("https://127.0.0.1", "127.0.0.1"),
        ("https://localhost", "localhost"),
    ],
)
def test_edge_requires_matching_public_dns_name(origin, domain):
    with pytest.raises(ValueError, match="Edge domain"):
        DeploymentConfig(origin, docker_gid=0, domain=domain).validate()


def test_configuration_generates_private_file_and_preserves_existing_credentials(tmp_path):
    config = DeploymentConfig("https://example.org", docker_gid=999, domain="example.org")
    content = config.render({"DEEPSEEK_API_KEY": "sk-example-private-token"})
    values = dict(line.split("=", 1) for line in content.splitlines())
    assert len(values["NISHTYAK_SERVICE_TOKEN"]) == 64
    assert values["NISHTYAK_AUTOMATIC_REVIEW"] == "1"
    assert values["NISHTYAK_DOCKER_GID"] == "999"
    assert values["NISHTYAK_MAX_STORAGE_BYTES"] == "2147483648"
    assert "DEEPSEEK_API_KEY=sk-example-private-token\n" in content
    destination = tmp_path / ".env"
    EnvironmentWriter().write(destination, content)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="already exists"):
        EnvironmentWriter().write(destination, "replacement")
    assert destination.read_text() == content
    assert list(tmp_path.iterdir()) == [destination]


def test_configured_global_storage_budget_reaches_logic_and_dispatcher(monkeypatch):
    budget = str(16 * 1024**3)
    content = DeploymentConfig("https://example.org", docker_gid=999).render(
        {"NISHTYAK_MAX_STORAGE_BYTES": budget}
    )
    values = dict(line.split("=", 1) for line in content.splitlines())
    assert values["NISHTYAK_MAX_STORAGE_BYTES"] == budget
    path = Path(__file__).resolve().parents[1] / "deploy" / "production-compose.yaml"
    services = yaml.safe_load(path.read_text())["services"]
    for name in ("logic", "dispatcher"):
        assert services[name]["environment"]["NISHTYAK_MAX_STORAGE_BYTES"] == (
            "${NISHTYAK_MAX_STORAGE_BYTES:-2147483648}"
        )
        with monkeypatch.context() as environment:
            environment.setenv("NISHTYAK_MAX_STORAGE_BYTES", values["NISHTYAK_MAX_STORAGE_BYTES"])
            assert Settings.from_env().max_storage_bytes == 16 * 1024**3


@pytest.mark.parametrize("budget", ["", "0", "-1", "1.5", "16GiB", "1\nOTHER=value"])
def test_configuration_rejects_invalid_storage_budget_or_env_injection(budget):
    with pytest.raises(ValueError, match="positive byte count"):
        DeploymentConfig("https://example.org", docker_gid=999).render(
            {"NISHTYAK_MAX_STORAGE_BYTES": budget}
        )


def test_configuration_does_not_follow_destination_symlink(tmp_path):
    existing = tmp_path / "existing"
    existing.write_text("keep")
    destination = tmp_path / ".env"
    destination.symlink_to(existing)
    with pytest.raises(ValueError, match="already exists"):
        EnvironmentWriter().write(destination, "replacement")
    assert existing.read_text() == "keep"


def test_configuration_detects_racing_destination_without_overwrite(tmp_path, monkeypatch):
    link = os.link
    destination = tmp_path / ".env"

    def raced(source, target):
        destination.write_text("concurrent credentials")
        return link(source, target)

    monkeypatch.setattr(os, "link", raced)
    with pytest.raises(ValueError, match="not replaced"):
        EnvironmentWriter().write(destination, "new credentials")
    assert destination.read_text() == "concurrent credentials"
    assert list(tmp_path.iterdir()) == [destination]


def test_configuration_cli_does_not_print_credentials(tmp_path):
    script = Path(__file__).resolve().parents[1] / "deploy" / "configure.py"
    secret = "sk-example-private-token"
    result = subprocess.run(
        [
            os.sys.executable,
            str(script),
            "--origin",
            "https://example.org",
            "--docker-gid",
            "999",
            "--skip-runtime-check",
            "--output",
            str(tmp_path / ".env"),
        ],
        env=dict(os.environ, DEEPSEEK_API_KEY=secret),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert secret not in result.stdout + result.stderr
    assert "0600" in result.stdout


def test_runtime_check_targets_local_socket_and_requires_linux_compose(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0, b"linux\n" if len(calls) == 1 else b"2.39.2\n"
        )

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("DOCKER_CONTEXT", "another-machine")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-example-private-token")
    check_runtime(Path("/var/run/docker.sock"))
    assert calls[0][0] == ["docker", "info", "--format", "{{.OSType}}"]
    assert calls[1][0] == ["docker", "compose", "version", "--short"]
    for _, parameters in calls:
        assert parameters["env"]["DOCKER_HOST"] == "unix:///var/run/docker.sock"
        assert "DOCKER_CONTEXT" not in parameters["env"]
        assert "DEEPSEEK_API_KEY" not in parameters["env"]


def test_production_separates_network_credentials_and_docker_privileges():
    path = Path(__file__).resolve().parents[1] / "deploy" / "production-compose.yaml"
    compose = yaml.safe_load(path.read_text())
    services = compose["services"]
    assert compose["volumes"]["jobs"]["name"] == "nishtyak-logic_jobs"
    assert compose["volumes"]["web"]["name"] == "nishtyak-preview_preview"
    assert services["web"]["command"] == ["--host", "127.0.0.1", "--port", "8082"]
    assert services["web"]["environment"]["NISHTYAK_WEB_MODE"] == "accounts"
    assert services["logic"]["ports"] == ["127.0.0.1:8010:8010"]
    assert services["dispatcher"]["network_mode"] == "none"
    assert any("docker.sock" in value for value in services["dispatcher"]["volumes"])
    for name in ("logic", "web", "edge"):
        assert not any("docker.sock" in value for value in services[name]["volumes"])
    for name in ("dispatcher", "web", "edge"):
        assert "DEEPSEEK_API_KEY" not in services[name]["environment"]
    for name in ("logic", "dispatcher", "web", "edge"):
        assert services[name]["read_only"]
        assert services[name]["user"] == "65532:65532"
        assert services[name]["security_opt"] == ["no-new-privileges:true"]
    assert services["edge"]["profiles"] == ["edge"]
