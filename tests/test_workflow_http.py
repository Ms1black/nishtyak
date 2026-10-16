from __future__ import annotations

import io
import json
import socket
import threading
import time
import zipfile

import pytest
import uvicorn

from nishtyak_client import LogicClient, LogicClientError
from nishtyak_core.models import AnalysisOptions
from nishtyak_core.report_codec import AnalysisReportCodec
from nishtyak_service.api import create_app
from nishtyak_service.config import Settings
from nishtyak_service.dispatcher import Dispatcher
from nishtyak_service.execution import SubprocessExecutor
from nishtyak_service.storage import SQLiteJobRepository


@pytest.fixture
def workflow_client(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", api_token="workflow-test-" + "a" * 32)
    app = create_app(settings)
    dispatcher = Dispatcher(
        app.state.logic.repository, SubprocessExecutor(settings.job_policy), 0.01
    )
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    dispatcher.start()
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        client = LogicClient(
            f"http://127.0.0.1:{listener.getsockname()[1]}", settings.api_token, "owner-one"
        )
        yield client, settings
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        dispatcher.stop()
        listener.close()
        assert not thread.is_alive()


def project_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in {
            "example-main/pyproject.toml": (
                '[project]\nname="example"\ndependencies=["numpy>=1"]\n'
                'requires-python=">=3.11"\n[project.optional-dependencies]\n'
                'plot=["matplotlib>=3"]\n[project.scripts]\nexample="example:main"\n'
            ),
            "example-main/src/example/__init__.py": (
                'import numpy\n\ndef main():\n    print("example result")\n'
            ),
            "example-main/README.md": "Example",
        }.items():
            archive.writestr(name, content)
    return output.getvalue()


def test_backend_sdk_full_saved_workflow_and_reanalysis(workflow_client):
    client, settings = workflow_client
    submitted = client.submit(
        project_zip(),
        author_environment={
            "python_version": "3.11.9",
            "platform": "Linux-x86_64",
            "distributions": [{"name": "numpy", "version": "1.26.4", "modules": ["numpy"]}],
        },
    )
    job = client.wait(submitted["job_id"], timeout=10, interval=0.1)
    assert job["status"] == "succeeded", job
    report = client.project(submitted["project_id"])["analysis"]
    assert report["layout"]["root"] == "example-main"
    assert "example-main/src" in report["layout"]["source_roots"]
    assert report["author_python_version"] == "3.11.9"
    assert report["author_environment_id"]
    assert report["optional_groups"] == ["plot"]
    declaration = next(item for item in report["declarations"] if item["group"] is None)
    decisions = {
        "analysis_id": report["analysis_id"],
        "run_spec": {
            "kind": "callable",
            "target": "example:main",
            "working_directory": "example-main",
        },
        "python_version": "3.11.9",
        "data_confirmed": True,
        "selected_extras": ["plot"],
        "declaration_actions": {
            declaration["id"]: {"action": "replace", "requirement": "numpy==1.26.4"}
        },
        "description": "Reviewed experiment",
        "input_description": "No external inputs",
        "expected_output": "example result",
    }
    identity = submitted["project_id"]
    empty = client.decisions(identity)
    assert empty["revision"] == 0 and empty["decisions"] is None
    saved = client.save_decisions(identity, decisions, empty["revision"])
    assert saved["revision"] == 1
    reopened = SQLiteJobRepository(settings).read_decisions(client.owner_id, identity)
    assert reopened["decisions"]["selected_extras"] == ["plot"]
    assert client.decisions(identity) == saved
    with pytest.raises(LogicClientError) as conflict:
        client.save_decisions(identity, decisions, empty["revision"])
    assert conflict.value.status == 409
    assert conflict.value.code == "DECISIONS_REVISION_CONFLICT"
    plan = client.plan(identity, saved["decisions"])
    assert plan["state"] == "ready_to_verify", plan
    assert "numpy==1.26.4" in plan["requirements"]
    assert "matplotlib>=3" in plan["requirements"]
    package = client.bundle(identity, saved["decisions"], include_dockerfile=True)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert "run_project.py" in archive.namelist()
        assert "Dockerfile" in archive.namelist()
        assert json.loads(archive.read("analysis-report.json"))["decisions"]["selected_extras"] == [
            "plot"
        ]
    bad = {**decisions, "selected_extras": ["nonexistent"]}
    with pytest.raises(LogicClientError) as invalid:
        client.save_decisions(identity, bad, saved["revision"])
    assert invalid.value.status == 422
    assert invalid.value.field == "selected_extras"
    new = client.reanalyze(identity, {"project_root": "example-main", "source_roots": ["src"]})
    new_job = client.wait(new["job_id"], timeout=10, interval=0.1)
    assert new_job["status"] == "succeeded", new_job
    assert new_job["analysis"]["analysis_id"] != report["analysis_id"]
    assert new_job["analysis"]["author_environment_id"] == report["author_environment_id"]
    assert client.decisions(identity)["decisions"] is None
    assert client.decisions(identity)["revision"] > saved["revision"]
    with pytest.raises(LogicClientError) as stale:
        client.save_decisions(identity, decisions, saved["revision"])
    assert stale.value.status == 409
    other = LogicClient(client.base_url, settings.api_token, "another-owner")
    with pytest.raises(LogicClientError) as forbidden:
        other.decisions(identity)
    assert forbidden.value.status == 404
    client.delete(identity)
    with pytest.raises(LogicClientError) as deleted:
        client.decisions(identity)
    assert deleted.value.status == 404


def test_legacy_options_and_report_upgrade_without_losing_fields():
    from nishtyak_core import ReproducibilityService, SnapshotBuilder

    report = ReproducibilityService().analyze(SnapshotBuilder().from_mapping({"main.py": "pass"}))
    encoded = AnalysisReportCodec().encode(report)
    additions = {
        "layout",
        "declarations",
        "optional_groups",
        "author_environment_id",
        "author_python_version",
        "author_platform",
    }
    legacy = {key: value for key, value in encoded.items() if key not in additions}
    legacy["schema_version"] = 1
    legacy["options"].pop("project_root")
    decoded = AnalysisReportCodec().decode(legacy)
    assert decoded.schema_version == 1 and decoded.layout is None
    assert decoded.analysis_id == report.analysis_id
    assert decoded.options == AnalysisOptions()
