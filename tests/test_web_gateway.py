from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nishtyak_client import LogicClient, LogicClientError
from nishtyak_service.api import create_app as logic_app
from nishtyak_service.config import Settings
from nishtyak_service.dispatcher import Dispatcher
from nishtyak_service.execution import SubprocessExecutor
from nishtyak_service.service import LogicService
from nishtyak_service.storage import SQLiteJobRepository
from nishtyak_web.api import create_app
from nishtyak_web.auth import WorkspaceIdentity
from nishtyak_web.config import WebSettings
from nishtyak_web.service import BrowserService

TOKEN = "test-service-token-" + "x" * 32
PROXY_SECRET = "test-proxy-secret-" + "x" * 32


class InProcessClient(LogicClient):
    """Exercise the real internal HTTP boundary without requiring a listening socket."""

    def __init__(self, http: TestClient, owner: str):
        super().__init__("http://localhost", TOKEN, owner)
        self.http = http

    def _request(
        self, method, path, body=None, content_type="application/json", binary=False, timeout=None
    ):
        response = self.http.request(
            method,
            path,
            content=body,
            headers={
                "Authorization": "Bearer " + TOKEN,
                "X-Owner-ID": self.owner_id,
                "Content-Type": content_type,
            },
        )
        if response.status_code >= 400:
            error = response.json()
            raise LogicClientError(response.status_code, error["code"], error["message"])
        return response.content if binary else response.json() if response.content else None


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    settings = Settings(data_dir=tmp_path / "logic", api_token=TOKEN)
    repository = SQLiteJobRepository(settings)
    dispatcher = Dispatcher(repository, SubprocessExecutor(settings.job_policy), poll_interval=0.01)
    dispatcher.start()
    with TestClient(logic_app(settings, LogicService(settings, repository))) as http:
        web_settings = WebSettings(data_dir=tmp_path / "web", service_token=TOKEN)
        service = BrowserService(web_settings, lambda owner: InProcessClient(http, owner))
        with TestClient(
            create_app(web_settings, service),
            base_url="http://localhost",
            client=("127.0.0.1", 49152),
        ) as client:
            yield client, service, http
    dispatcher.stop()


def source_zip(files: dict[str, str | bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def upload(client, files=None, *, name="experiment.zip", content=None, headers=None):
    content = content if content is not None else source_zip(files or {"main.py": "print(4)\n"})
    response = client.post(
        "/api/teams/local/projects", files={"file": (name, content)}, headers=headers
    )
    assert response.status_code == 202, response.text
    return response.json()


def analyzed(client, project_id, headers=None):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/projects/{project_id}/analysis", headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] != "analyzing":
            assert result["status"] != "failed", result
            return result
        time.sleep(0.04)
    raise AssertionError("Static analysis did not complete")


def decisions(analysis):
    return {
        "analysisId": analysis["analysisId"],
        "packages": {},
        "versions": {},
        "excludedModules": [],
        "excludedFiles": [],
        "runSpec": None,
        "pythonVersion": "3.11",
        "description": "Mean of three measurements",
        "inputDescription": "Embedded measurements",
        "expectedOutput": "4.0",
        "dataConfirmed": True,
        "selectedExtras": [],
        "declarationActions": {},
    }


def test_real_browser_workflow_upload_analyze_save_export_restart(gateway):
    client, service, _ = gateway
    session = client.get("/api/session").json()
    assert session["mode"] == "local" and session["team"]["id"] == "local"
    assert TOKEN not in json.dumps(session)
    project = upload(
        client,
        {
            "experiment.py": "from helpers import mean\nprint(mean([2,4,6]))\n",
            "helpers.py": "def mean(values):\n    return sum(values)/len(values)\n",
        },
    )
    project_id = project["id"]
    analysis = analyzed(client, project_id)
    assert analysis["suggestedRunSpec"]["target"] == "experiment.py"
    assert any(
        item["module"] == "helpers" and item["category"] == "local"
        for item in analysis["dependencies"]
    )
    assert analysis["launchCandidates"]
    assert (
        client.get(f"/api/projects/{project_id}/files", params={"path": "helpers.py"})
        .json()["content"]
        .startswith("def mean")
    )
    doc = client.put(
        f"/api/projects/{project_id}/decisions",
        json={"expectedRevision": 0, "decisions": decisions(analysis)},
    )
    assert doc.status_code == 200, doc.text
    assert doc.json()["revision"] == 1
    conflict = client.put(
        f"/api/projects/{project_id}/decisions",
        json={"expectedRevision": 0, "decisions": decisions(analysis)},
    )
    assert conflict.status_code == 409
    plan = client.post(f"/api/projects/{project_id}/plan", json=decisions(analysis)).json()
    assert plan["state"] == "ready_to_verify", plan
    assert plan["runSpec"]["target"] == "experiment.py"
    built = client.post(f"/api/projects/{project_id}/archive", json={"includeDockerfile": True})
    assert built.status_code == 200, built.text
    assert built.json()["archive"]["status"] == "ready"
    archive = client.get(f"/api/projects/{project_id}/archive")
    assert archive.status_code == 200, archive.text
    assert archive.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        assert built.json()["archive"]["files"] == package.namelist()
        assert b"Mean of three measurements" in package.read("README.md")
        assert package.read("project/experiment.py").startswith(b"from helpers")
        assert "Dockerfile" in package.namelist()
    restarted = BrowserService(service.settings, service.client_factory)
    identity = WorkspaceIdentity("local", "local", "local")
    assert restarted.download(identity, project_id) == archive.content
    assert restarted.list(identity, "local")[0]["archive"]["status"] == "ready"
    saved = client.put(
        f"/api/projects/{project_id}/decisions",
        json={"expectedRevision": 1, "decisions": decisions(analysis)},
    )
    assert saved.status_code == 200
    assert client.get(f"/api/projects/{project_id}/archive").status_code == 409


def test_single_python_upload_and_draft_export(gateway):
    client, _, _ = gateway
    project = upload(
        client, name="calculate.py", content=b"import numpy\nprint(numpy.mean([2,4,6]))\n"
    )
    analysis = analyzed(client, project["id"])
    assert project["sourceType"] == "py"
    assert analysis["files"] == [{"path": "calculate.py", "size": 40}]
    assert analysis["suggestedRunSpec"]["target"] == "calculate.py"
    built = client.post(f"/api/projects/{project['id']}/archive")
    assert built.status_code == 200, built.text
    archive = client.get(f"/api/projects/{project['id']}/archive")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        assert b"numpy" in package.read("requirements.txt").splitlines()
        assert "draft" in package.read("README.md").decode().lower()


def test_source_not_executed_secrets_hidden_binary_and_traversal(gateway, tmp_path):
    client, _, _ = gateway
    marker = tmp_path / "never-executed"
    credential = "sk-" + "a" * 32
    project = upload(
        client,
        {
            "main.py": f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
            "config.py": f"api_key = {credential!r}\n",
            "data.bin": b"\x00\xff",
            ".env": "ordinary=content\n",
        },
    )
    analysis = analyzed(client, project["id"])
    assert not marker.exists()
    assert credential not in json.dumps(analysis)
    prefix = f"/api/projects/{project['id']}"
    assert client.get(prefix + "/files", params={"path": "config.py"}).json() == {"kind": "hidden"}
    assert client.get(prefix + "/files", params={"path": ".env"}).json() == {"kind": "hidden"}
    assert client.get(prefix + "/files", params={"path": "data.bin"}).json() == {"kind": "binary"}
    assert client.get(prefix + "/files", params={"path": "../web.sqlite3"}).status_code == 422
    assert client.post(prefix + "/archive").status_code == 409
    assert not marker.exists()


@pytest.mark.parametrize(
    "name,content",
    [
        ("bad.zip", b"not ZIP"),
        ("../main.py", b"print(4)"),
        ("main.py.exe", b"x"),
        ("bad.zip", source_zip({"../outside.py": "print(4)"})),
    ],
)
def test_reject_unsafe_uploads(gateway, name, content):
    client, _, _ = gateway
    response = client.post("/api/teams/local/projects", files={"file": (name, content)})
    assert response.status_code == 422, response.text
    assert client.get("/api/teams/local/projects").json() == []


def test_proxy_workspace_identity_and_cross_workspace_isolation(gateway):
    _, service, _ = gateway
    settings = replace(service.settings, mode="proxy", proxy_secret=PROXY_SECRET)
    app = create_app(settings, service)
    one = {
        "X-Nishtyak-Proxy-Secret": PROXY_SECRET,
        "X-Nishtyak-User": "alice",
        "X-Nishtyak-Workspace": "local",
    }
    two = {**one, "X-Nishtyak-User": "bob", "X-Nishtyak-Workspace": "other"}
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/session").status_code == 401
        assert client.get("/api/session", headers={"X-Owner-ID": "alice"}).status_code == 401
        assert (
            client.get(
                "/api/session", headers={**one, "X-Nishtyak-Proxy-Secret": "wrong"}
            ).status_code
            == 401
        )
        assert client.get("/api/teams/other/projects", headers=one).status_code == 404
        project = upload(client, headers=one)
        analysis = analyzed(client, project["id"], headers=one)
        project_url = f"/api/projects/{project['id']}"
        for suffix in (
            "analysis",
            "decisions",
            "archive",
            "files?path=main.py",
            "graph",
            "preparation",
            "lock",
        ):
            assert client.get(project_url + "/" + suffix, headers=two).status_code == 404
        for suffix, body in (
            ("plan", decisions(analysis)),
            ("archive", {}),
            ("advice", {"shareSource": True}),
        ):
            assert (
                client.post(project_url + "/" + suffix, json=body, headers=two).status_code == 404
            )
        assert (
            client.put(
                project_url + "/decisions",
                headers=two,
                json={"expectedRevision": 0, "decisions": decisions(analysis)},
            ).status_code
            == 404
        )
        assert client.get("/api/teams/other/projects", headers=two).json() == []
        same_workspace = {**one, "X-Nishtyak-User": "bob"}
        assert client.get(project_url + "/analysis", headers=same_workspace).status_code == 200


def test_local_auth_origin_host_and_untrusted_forwarded_headers(gateway):
    client, service, _ = gateway
    assert (
        client.get("/api/session", headers={"Origin": "https://attacker.example"}).status_code
        == 403
    )
    assert client.get("/api/session", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/session", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    with TestClient(
        create_app(service.settings, service),
        base_url="http://localhost",
        client=("198.51.100.1", 49152),
    ) as remote:
        assert (
            remote.get("/api/session", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401
        )
    assert (
        client.get("/api/teams/spoofed/projects", headers={"X-Owner-ID": "spoofed"}).status_code
        == 404
    )


def test_reanalysis_invalidates_artifact_and_old_decisions(gateway):
    client, _, _ = gateway
    project = upload(client)
    analysis = analyzed(client, project["id"])
    prefix = f"/api/projects/{project['id']}"
    assert client.post(prefix + "/archive").status_code == 200
    response = client.post(
        prefix + "/analysis", json={"options": {"pythonVersion": "3.12", "sourceRoots": ["."]}}
    )
    assert response.status_code == 202, response.text
    next_analysis = analyzed(client, project["id"])
    assert next_analysis["analysisId"] != analysis["analysisId"]
    assert client.post(prefix + "/plan", json=decisions(analysis)).status_code == 409
    assert client.get(prefix + "/archive").status_code == 409


def test_static_spa_fallback_never_serves_api_or_outside_files(gateway, tmp_path):
    _, service, _ = gateway
    static = tmp_path / "dist"
    static.mkdir()
    (static / "index.html").write_text("<html>application</html>")
    (static / "app.js").write_text("console.log('hello')")
    private = tmp_path / "private.txt"
    private.write_text("secret")
    (static / "linked.txt").symlink_to(private)
    with TestClient(
        create_app(service.settings, service, static),
        base_url="http://localhost",
        client=("127.0.0.1", 12345),
    ) as client:
        assert client.get("/projects/example").text == "<html>application</html>"
        assert client.get("/app.js").status_code == 200
        assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]
        assert client.get("/api/missing").status_code == 404
        assert client.get("/missing.js").status_code == 404
        assert client.get("/linked.txt").status_code == 404


def test_unknown_mutation_ids_do_not_accumulate_locks(gateway):
    client, service, _ = gateway
    for index in range(10):
        assert client.post(f"/api/projects/{index:032x}/archive").status_code == 404
    assert service.store._locks == {}


def test_ttl_hides_and_removes_source_and_artifact(gateway):
    client, service, _ = gateway
    project = upload(client)
    analyzed(client, project["id"])
    assert client.post(f"/api/projects/{project['id']}/archive").status_code == 200
    directory = service.store.root / project["id"]
    assert (directory / "source.zip").is_file()
    assert (directory / "bundle.zip").is_file()
    with service.store.connection() as db:
        db.execute("UPDATE projects SET expires_at=? WHERE id=?", (time.time() - 1, project["id"]))
    # An active operation delays physical cleanup, but expired projects are already inaccessible.
    with service.store.locked(project["id"]):
        assert client.get("/api/teams/local/projects").json() == []
        assert directory.exists()
        assert client.get(f"/api/projects/{project['id']}/analysis").status_code == 404
    service.store.cleanup()
    assert not directory.exists()


def test_extras_round_trip_and_package_plan(gateway):
    client, _, _ = gateway
    project = upload(
        client,
        {
            "wrapped/experiment.py": "print(4)\n",
            "wrapped/pyproject.toml": '[project]\nname="example"\nversion="0.1"\n'
            '[project.optional-dependencies]\nplot=["matplotlib>=3"]\n',
        },
    )
    analysis = analyzed(client, project["id"])
    assert analysis["optionalGroups"] == ["plot"]
    assert analysis["layout"]["root"] == "wrapped"
    assert (
        client.get(
            f"/api/projects/{project['id']}/files", params={"path": "wrapped/experiment.py"}
        ).json()["content"]
        == "print(4)\n"
    )
    value = {**decisions(analysis), "selectedExtras": ["plot"]}
    prefix = f"/api/projects/{project['id']}"
    response = client.put(prefix + "/decisions", json={"expectedRevision": 0, "decisions": value})
    assert response.status_code == 200, response.text
    assert response.json()["decisions"]["selectedExtras"] == ["plot"]
    plan = client.post(prefix + "/plan", json=value)
    assert plan.status_code == 200, plan.text
    assert "matplotlib>=3" in plan.json()["requirements"]
    assert client.get(prefix + "/decisions").json()["decisions"]["selectedExtras"] == ["plot"]


def test_lock_graph_and_explicit_ai_source_consent(gateway):
    client, _, _ = gateway
    project = upload(client)
    analyzed(client, project["id"])
    prefix = f"/api/projects/{project['id']}"
    graph = client.get(prefix + "/graph")
    assert graph.status_code == 200, graph.text
    assert "nodes" in graph.json() and "edges" in graph.json()
    lock = client.post(
        prefix + "/resolve",
        json={
            "target": {
                "pythonVersion": "3.11",
                "platform": "linux-x86_64",
            }
        },
    )
    assert lock.status_code == 200, lock.text
    assert lock.json()["packages"] == []
    assert client.get(prefix + "/lock").json()["id"] == lock.json()["id"]
    assert client.post(prefix + "/archive", json={"lockId": lock.json()["id"]}).status_code == 200
    archive = client.get(prefix + "/archive")
    with zipfile.ZipFile(io.BytesIO(archive.content)) as package:
        assert "requirements.lock.txt" in package.namelist()
        assert "dependency-lock.json" in package.namelist()
    assert client.post(prefix + "/advice", json={"feature": "readme"}).status_code == 422
    assert client.post(prefix + "/advice", json={"shareSource": False}).status_code == 422
    unavailable = client.post(prefix + "/advice", json={"feature": "readme", "shareSource": True})
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["status"] == "unavailable"


def test_preview_checks_retained_source_fingerprint(gateway):
    client, service, _ = gateway
    project = upload(client)
    analyzed(client, project["id"])
    (service.store.root / project["id"] / "source.zip").write_bytes(
        source_zip({"main.py": "print(5)\n"})
    )
    response = client.get(f"/api/projects/{project['id']}/files", params={"path": "main.py"})
    assert response.status_code == 409
    assert response.json()["code"] == "SNAPSHOT_CHANGED"
