from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import replace
from pathlib import Path

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
        readme = package.read("README.md").decode()
        assert "ready_to_verify" in readme
        assert "Successful reproduction has not been verified" in readme


@pytest.mark.parametrize(
    "archive_name,expected_requirements,local_module",
    [
        ("scientific-measurements.zip", ["numpy", "pandas"], "calculations"),
        ("local-calibration.zip", [], "calibration"),
    ],
)
def test_raw_upload_examples_export_without_manual_answers(
    gateway, archive_name, expected_requirements, local_module
):
    client, _, _ = gateway
    content = (Path(__file__).resolve().parents[1] / "public/examples" / archive_name).read_bytes()
    with zipfile.ZipFile(io.BytesIO(content)) as source:
        sources = {name: source.read(name) for name in source.namelist()}
    assert len(sources) == 2
    assert all(name.endswith(".py") for name in sources)

    project = upload(client, name=archive_name, content=content)
    analysis = analyzed(client, project["id"])
    assert any(
        dependency["module"] == local_module and dependency["category"] == "local"
        for dependency in analysis["dependencies"]
    )
    prefix = f"/api/projects/{project['id']}"
    response = client.post(prefix + "/plan")
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["state"] == "draft"
    assert plan["runSpec"]["target"] == "experiment.py"
    assert plan["runSpecOrigin"] == "inferred"
    assert plan["requirements"] == expected_requirements
    codes = {finding["code"] for finding in plan["findings"]}
    assert "INPUT_PATH_UNVERIFIED" in codes
    assert "DATA_NOT_CONFIRMED" not in codes
    # This fixture keeps network resolution disabled; no historical version is invented.
    assert plan["pythonVersion"] is None

    built = client.post(prefix + "/archive")
    assert built.status_code == 200, built.text
    downloaded = client.get(prefix + "/archive")
    assert downloaded.status_code == 200, downloaded.text
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as package:
        assert {"README.md", "requirements.txt", "analysis-report.json"} <= set(package.namelist())
        assert not any(name.endswith("measurements.csv") for name in package.namelist())
        for name, source in sources.items():
            assert package.read("project/" + name) == source
        requirements = [
            line
            for line in package.read("requirements.txt").decode().splitlines()
            if line and not line.startswith("#")
        ]
        assert requirements == expected_requirements
        report = json.loads(package.read("analysis-report.json"))
        assert report["state"] == "draft"
        assert report["decisions"]["run_spec"] is None
        assert report["decisions"]["python_version"] is None
        assert report["decisions"]["data_confirmed"] is False
        assert "not been verified" in package.read("README.md").decode()
    document = client.get(prefix + "/decisions").json()
    assert document["revision"] == 0
    assert document["decisions"] is None


def test_dependency_findings_keep_import_module_in_analysis_and_plan(gateway):
    client, _, _ = gateway
    project = upload(client, {"main.py": "import unmapped_scientific_library\nimport numpy\n"})
    analysis = analyzed(client, project["id"])
    unknown = next(f for f in analysis["findings"] if f["code"] == "DEPENDENCY_UNRESOLVED")
    assert unknown["module"] == "unmapped_scientific_library"
    version = next(f for f in analysis["findings"] if f["code"] == "VERSION_UNKNOWN")
    assert version["module"] == "numpy"
    response = client.post(f"/api/projects/{project['id']}/plan")
    assert response.status_code == 200, response.text
    unresolved = next(
        f for f in response.json()["findings"] if f["code"] == "DEPENDENCY_UNRESOLVED"
    )
    assert unresolved["module"] == "unmapped_scientific_library"
    assert response.json()["state"] == "draft"


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
    client, service, _ = gateway
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
    # A valid stored lock is exported automatically; the UI need not supply its ID.
    assert client.post(prefix + "/archive", json={}).status_code == 200
    stored = service.store.get(WorkspaceIdentity("local", "local", "local").scope, project["id"])
    assert json.loads(stored["archive_tag"])["lock"] == lock.json()["id"]
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


def test_upload_automatically_prepares_python_and_pins_without_author_answers(gateway, monkeypatch):
    import hashlib

    from nishtyak_core.locking import DependencyLock, resolution_input_id

    client, service, http = gateway
    logic = http.app.state.logic
    configured = replace(logic.settings, automatic_resolution=True)
    logic.settings = configured
    logic.repository.settings = configured
    calls = []

    def resolve(_resolver, plan, target):
        calls.append(target)
        packages = ("numpy==2.2.0", "pandas==2.2.3")
        text = "".join(package + " --hash=sha256:" + "a" * 64 + "\n" for package in packages)
        identifier = resolution_input_id(plan, target)
        return DependencyLock(
            hashlib.sha256((identifier + "\0" + text).encode()).hexdigest(),
            plan.analysis_id,
            identifier,
            target,
            text,
            packages,
        )

    monkeypatch.setattr("nishtyak_service.automatic_resolution.VersionResolver.resolve", resolve)
    project = upload(
        client,
        {
            "experiment.py": "import numpy\nimport pandas\nprint(numpy.mean([1,2]))\n",
            "pyproject.toml": (
                '[project]\nname="measurements"\nversion="0.1"\n'
                'requires-python=">=3.12"\ndependencies=["numpy>=2", "pandas>=2"]\n'
            ),
        },
    )
    scope = WorkspaceIdentity("local", "local", "local").scope
    deadline = time.monotonic() + 10
    while logic.repository.get_project(scope, project["id"]).status != "succeeded":
        assert time.monotonic() < deadline
        time.sleep(0.04)
    prefix = f"/api/projects/{project['id']}"
    assert client.get(prefix + "/analysis").json()["status"] == "analyzing"
    logic.review_pending()
    result = analyzed(client, project["id"])
    assert result["automaticResolution"]["status"] == "ready"
    assert result["options"]["pythonVersion"] == "3.12"
    assert calls[0].python_version == "3.12"
    plan = client.post(prefix + "/plan").json()
    assert plan["pythonVersionOrigin"] == "inferred"
    assert plan["automaticResolution"]["packages"] == ["numpy==2.2.0", "pandas==2.2.3"]
    assert plan["decisions"]["pythonVersion"] is None
    assert plan["decisions"]["versions"] == {}
    assert plan["decisions"]["dataConfirmed"] is False
    assert client.post(prefix + "/archive", json={}).status_code == 200
    result = client.get(prefix + "/archive")
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert "pandas==2.2.3" in archive.read("requirements.lock.txt").decode()
        assert "--require-hashes -r requirements.lock.txt" in archive.read("README.md").decode()
    tag = json.loads(service.store.get(scope, project["id"])["archive_tag"])
    assert tag["lock"] == plan["automaticResolution"]["lockId"]
    assert client.get(prefix + "/decisions").json()["decisions"] is None


def test_one_clarification_batch_crosses_gateway_and_finishes_once(gateway, monkeypatch):
    client, browser, http = gateway
    logic = http.app.state.logic
    logic.settings = replace(logic.settings, automatic_resolution=True)
    logic.repository.settings = logic.settings
    calls = []

    def review(record, report, *, plan, aspects, pass_id):
        calls.append(("prepare", pass_id))
        return {"attempted": True, "status": "unavailable", "reason": "no_grounded_answer"}

    def finalize(record, report, *, plan, aspects, pass_id):
        calls.append(("finalize", pass_id))
        return {"attempted": True, "status": "unavailable", "reason": "no_grounded_answer"}

    monkeypatch.setattr(logic.automatic_review, "needs_description", lambda *args: False)
    monkeypatch.setattr(logic.automatic_review, "prepare_pass", review)
    monkeypatch.setattr(logic.automatic_review, "finalize", finalize)
    project = upload(client, {"main.py": "import unknown_first\nimport unknown_second\nprint(1)\n"})
    identifier = project["id"]
    owner = WorkspaceIdentity("local", "local", "local").scope
    deadline = time.monotonic() + 5
    while logic.repository.get_project(owner, identifier).status in {"queued", "running"}:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    logic.review_pending()
    prefix = "/api/projects/" + identifier
    plan = client.post(prefix + "/plan").json()
    flow = plan["automation"]
    assert flow["phase"] == "needs_input" and flow["attempt"] == 2
    assert {question["module"] for question in flow["questions"]} == {
        "unknown_first",
        "unknown_second",
    }
    body = {
        "questionnaireId": flow["questionnaireId"],
        "expectedRevision": 0,
        "decisions": plan["decisions"],
        "notes": "Не знаю названий этих библиотек, сохраните ограничения.",
    }
    assert (
        client.post(prefix + "/clarifications", json={**body, "notes": "x" * 6001}).status_code
        == 422
    )
    stale = client.post(prefix + "/clarifications", json={**body, "expectedRevision": 9})
    assert stale.status_code == 409 and stale.json()["code"] == "DECISIONS_REVISION_CONFLICT"
    assert client.get(prefix + "/decisions").json()["revision"] == 0
    assert client.post(prefix + "/plan").json()["automation"]["humanRound"] == 0
    response = client.post(prefix + "/clarifications", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    progress = client.get(prefix + "/analysis").json()
    assert progress["status"] == "analyzing" and progress["automation"]["phase"] == "finalizing"
    assert client.post(prefix + "/archive", json={}).status_code == 409
    repeated = client.post(prefix + "/clarifications", json={**body, "expectedRevision": 1})
    assert repeated.status_code == 409 and repeated.json()["code"] == "CLARIFICATIONS_STALE"
    logic.review_pending()
    completed = client.post(prefix + "/plan").json()
    assert completed["automation"]["phase"] == "draft"
    assert completed["automation"]["stage"] == "complete"
    assert completed["automation"]["humanRound"] == completed["automation"]["finalAttempt"] == 1
    assert completed["automation"]["questions"] == []
    assert body["notes"] not in json.dumps(completed)
    assert client.post(prefix + "/archive", json={}).status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(client.get(prefix + "/archive").content))
    for _ in range(2):
        logic.review_pending()
        assert client.post(prefix + "/plan").json()["automation"]["phase"] == "draft"
    assert [call[0] for call in calls] == ["prepare", "prepare", "finalize"]
