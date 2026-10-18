from __future__ import annotations

from dataclasses import replace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from test_web_gateway import analyzed
from test_web_gateway import gateway as gateway_fixture

from nishtyak_web.api import create_app
from nishtyak_web.config import WebSettings
from nishtyak_web.preview import (
    COOKIE_NAME,
    PreviewAccess,
    PreviewLoginError,
    hash_passphrase,
    safe_next,
)

gateway = gateway_fixture

ORIGIN = "https://review.example"
PASSPHRASE = "test-review-passphrase-with-enough-entropy"


@pytest.fixture
def preview_settings(tmp_path):
    return WebSettings(
        data_dir=tmp_path,
        service_token="test-service-token-" + "x" * 32,
        mode="preview",
        origins=(ORIGIN,),
        preview_password_hash=hash_passphrase(PASSPHRASE),
        preview_cookie_secret="cookie-signature-secret-" + "x" * 32,
    )


def login(client, passphrase=PASSPHRASE, next_path="/team/projects", **kwargs):
    return client.post(
        "/preview/access",
        content=urlencode({"passphrase": passphrase, "next": next_path}),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": ORIGIN},
        follow_redirects=False,
        **kwargs,
    )


def test_preview_login_cookie_and_real_project_boundary(gateway, preview_settings):
    _, service, _ = gateway
    with TestClient(create_app(preview_settings, service), base_url=ORIGIN) as client:
        assert client.get("/api/session").json()["code"] == "PREVIEW_AUTH_REQUIRED"
        assert client.get("/api/teams/preview/projects").status_code == 401
        response = client.get("/preview/access", params={"next": '//evil.example/"<script>'})
        assert response.status_code == 200
        assert "<script>" not in response.text
        assert 'value="/team/projects"' in response.text
        assert login(client, "wrong").status_code == 401
        response = login(client)
        assert response.status_code == 303
        assert response.headers["location"] == "/team/projects"
        cookie = response.headers["set-cookie"]
        assert all(value in cookie for value in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"))
        assert "Domain=" not in cookie
        session = client.get("/api/session").json()
        assert session["mode"] == "preview"
        assert session["team"]["id"] == "preview"
        assert session["capabilities"]["sharedWorkspace"] is True
        assert session["capabilities"]["accountManagement"] is False
        assert PASSPHRASE not in str(session) and preview_settings.service_token not in str(session)
        client.headers["Origin"] = ORIGIN
        project = client.post(
            "/api/teams/preview/projects", files={"file": ("main.py", b"print(4)\n")}
        )
        assert project.status_code == 202, project.text
        project_id = project.json()["id"]
        analyzed(client, project_id)
        assert client.post(f"/api/projects/{project_id}/archive").status_code == 200
        assert client.get(f"/api/projects/{project_id}/archive").content.startswith(b"PK")
        assert client.get("/api/teams/spoofed/projects").status_code == 404
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/session").status_code == 401
        assert client.get(f"/api/projects/{project_id}/archive").status_code == 401


def test_preview_rejects_csrf_untrusted_owner_headers_and_bad_hosts(gateway, preview_settings):
    _, service, _ = gateway
    with TestClient(create_app(preview_settings, service), base_url=ORIGIN) as client:
        assert client.get("/api/healthz", headers={"Host": "localhost"}).status_code == 200
        response = client.get(
            "/api/session",
            headers={
                "X-Nishtyak-Proxy-Secret": "x" * 64,
                "X-Nishtyak-User": "admin",
                "X-Nishtyak-Workspace": "preview",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        assert response.status_code == 401
        assert client.post("/preview/access", data={"passphrase": PASSPHRASE}).status_code == 403
        assert (
            client.post(
                "/preview/access",
                data={"passphrase": PASSPHRASE},
                headers={"Origin": "https://attacker.example"},
            ).status_code
            == 403
        )
        assert login(client).status_code == 303
        assert client.get("/api/session", headers={"Host": "attacker.example"}).status_code == 403
        assert client.post("/api/teams/preview/projects").status_code == 403
        assert (
            client.post(
                "/api/auth/logout",
                headers={
                    "Origin": ORIGIN,
                    "Sec-Fetch-Site": "cross-site",
                },
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/session",
                headers=[
                    ("Cookie", f"{COOKIE_NAME}=bad"),
                    ("Cookie", f"{COOKIE_NAME}=bad"),
                ],
            ).status_code
            == 400
        )


def test_preview_metadata_and_explicit_unavailable_account_mutations(gateway, preview_settings):
    _, service, _ = gateway
    with TestClient(create_app(preview_settings, service), base_url=ORIGIN) as client:
        login(client)
        assert client.get("/api/me").json()["id"] == "reviewer"
        assert client.get("/api/teams").json()[0]["membersCount"] == 1
        members = client.get("/api/teams/preview/members").json()
        assert len(members) == 1 and members[0]["isYou"] is True
        assert client.get("/api/teams/preview/invites").json() == []
        assert client.get("/api/teams/other/members").status_code == 404
        client.headers["Origin"] = ORIGIN
        for method, path in (
            ("PATCH", "/api/me"),
            ("POST", "/api/teams"),
            ("DELETE", "/api/teams/preview"),
            ("POST", "/api/teams/preview/invites"),
            ("PATCH", "/api/teams/preview/members/reviewer"),
        ):
            response = client.request(method, path, json={})
            assert response.status_code == 501, response.text
            assert response.json()["code"] == "ACCOUNT_BACKEND_UNAVAILABLE"


def test_signed_preview_cookie_tamper_expiration_and_wrong_password_limit(
    preview_settings, monkeypatch
):
    access = PreviewAccess(preview_settings)
    token = access.authenticate(PASSPHRASE)
    assert access.valid_cookie(f"{COOKIE_NAME}={token}")
    bad = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert not access.valid_cookie(f"{COOKIE_NAME}={bad}")
    assert not access.valid_cookie(f"{COOKIE_NAME}={token}; {COOKIE_NAME}={token}")
    assert not PreviewAccess(
        replace(preview_settings, preview_cookie_secret="y" * 64)
    ).valid_cookie(f"{COOKIE_NAME}={token}")
    import nishtyak_web.preview as module

    now = module.time.time()
    monkeypatch.setattr(
        module.time, "time", lambda: now + preview_settings.preview_session_seconds + 1
    )
    assert not access.valid_cookie(f"{COOKIE_NAME}={token}")
    for _ in range(9):
        with pytest.raises(PreviewLoginError) as error:
            access.authenticate("wrong")
        assert error.value.status == 401
    with pytest.raises(PreviewLoginError) as error:
        access.authenticate(PASSPHRASE)
    assert error.value.status == 429


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example",
        "//evil.example",
        "/\\evil",
        "/api/session",
        "/preview/access",
        "/x\nlocation:bad",
    ],
)
def test_preview_redirect_cannot_leave_the_application(value):
    assert safe_next(value) == "/team/projects"


def test_preview_config_requires_hash_secret_and_https_origin(preview_settings):
    for changes in (
        {"preview_password_hash": "plaintext"},
        {"preview_cookie_secret": "short"},
        {"origins": ("http://localhost",)},
        {"origins": ()},
        {"preview_session_seconds": 1},
    ):
        with pytest.raises(ValueError):
            replace(preview_settings, **changes)
