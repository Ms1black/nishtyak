from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Event

import pytest
from fastapi.testclient import TestClient

from nishtyak_client import LogicClientError
from nishtyak_web.accounts import SESSION_COOKIE, AccountStore, token_hash
from nishtyak_web.api import create_app
from nishtyak_web.auth import WorkspaceIdentity
from nishtyak_web.config import WebSettings

ORIGIN = "https://accounts.example.test"
PASSWORD = "правильный длинный пароль"
TOKEN = "service-token-" + "x" * 40


@pytest.fixture
def settings(tmp_path):
    return WebSettings(
        data_dir=tmp_path / "web", service_token=TOKEN, mode="accounts", origins=(ORIGIN,)
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


def client_for(app):
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def register(client, email="author@example.test", name="Исследователь"):
    response = client.post(
        "/api/auth/register", json={"name": name, "email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


def team_for(client):
    response = client.post("/api/teams", json={"name": "Лаборатория"})
    assert response.status_code == 201, response.text
    return response.json()


def invite_for(client, team_id, role="member"):
    response = client.post(f"/api/teams/{team_id}/invites", json={"role": role})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["url"].startswith(ORIGIN + "/invite/team#token=")
    return data, data["url"].split("#token=")[1]


def accept(client, token):
    return client.post("/api/invites/accept", json={"token": token})


def test_registration_creates_personal_workspace_secure_session_and_restarts(app, settings):
    with client_for(app) as client:
        user = register(client, "Author@Example.test")
        assert user["email"] == "author@example.test" and user["avatarUrl"] is None
        cookie = client.cookies.get(SESSION_COOKIE)
        assert cookie
        session = client.get("/api/session").json()
        assert session["mode"] == "accounts" and session["user"] == user
        assert session["team"]["personal"] and session["team"]["myRole"] == "owner"
        assert session["capabilities"]["accountManagement"]
        assert len(client.get("/api/teams").json()) == 1
        assert client.delete(f"/api/teams/{session['team']['id']}").status_code == 409
        duplicate = client.post(
            "/api/auth/register",
            json={"name": "Other", "email": "AUTHOR@EXAMPLE.TEST", "password": PASSWORD},
        )
        assert duplicate.status_code == 409 and duplicate.json()["code"] == "EMAIL_EXISTS"
        updated = client.patch("/api/me", json={"name": "Новое имя"})
        assert updated.json()["name"] == "Новое имя"
    store = AccountStore(settings)
    assert store.authenticate(f"{SESSION_COOKIE}={cookie}") == user["id"]
    with store.connection() as db:
        saved = dict(db.execute("SELECT * FROM users").fetchone())
        tokens = [dict(row) for row in db.execute("SELECT * FROM sessions")]
    assert saved["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in json.dumps(saved)
    assert cookie not in json.dumps(tokens)
    assert tokens[0]["digest"] == token_hash(cookie)
    with client_for(create_app(settings)) as restarted:
        restarted.cookies.set(SESSION_COOKIE, cookie)
        assert restarted.get("/api/me").json()["name"] == "Новое имя"


def test_login_cookie_rotation_logout_revocation_and_expiry(app):
    with client_for(app) as client:
        registered = client.post(
            "/api/auth/register",
            json={"name": "Author", "email": "a@example.test", "password": PASSWORD},
        )
        assert registered.status_code == 201
        header = registered.headers["set-cookie"].lower()
        assert "secure" in header and "httponly" in header and "samesite=strict" in header
        assert "domain=" not in header and "path=/" in header
        old = client.cookies.get(SESSION_COOKIE)
        assert (
            client.post(
                "/api/auth/login", json={"email": "a@example.test", "password": PASSWORD}
            ).status_code
            == 200
        )
        current = client.cookies.get(SESSION_COOKIE)
        assert current != old
        assert app.state.accounts.authenticate(f"{SESSION_COOKIE}={old}") is None
        assert client.post("/api/auth/logout").status_code == 204
        assert not client.cookies.get(SESSION_COOKIE)
        assert app.state.accounts.authenticate(f"{SESSION_COOKIE}={current}") is None
        assert client.get("/api/me").status_code == 401
        assert client.post("/api/auth/logout").status_code == 204
        client.post("/api/auth/login", json={"email": "a@example.test", "password": PASSWORD})
        with app.state.accounts.connection() as db:
            db.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
        assert client.get("/api/session").status_code == 401


def test_bad_login_is_generic_and_validation_never_echoes_password(app):
    with client_for(app) as client:
        register(client)
        known = client.post(
            "/api/auth/login", json={"email": "author@example.test", "password": "wrong"}
        )
        unknown = client.post(
            "/api/auth/login", json={"email": "missing@example.test", "password": "wrong"}
        )
        assert known.status_code == unknown.status_code == 401
        assert known.json() == unknown.json()
        for password in ("shortsecret", "x" * 129, {"secret": "don't echo"}):
            response = client.post(
                "/api/auth/register",
                json={"name": "A", "email": "b@example.test", "password": password},
            )
            assert response.status_code == 422
            assert "shortsecret" not in response.text and "don't echo" not in response.text


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Origin": "https://evil.test"}, "ORIGIN_FORBIDDEN"),
        ({"Host": "evil.test"}, "HOST_FORBIDDEN"),
        ({"Sec-Fetch-Site": "cross-site"}, "ORIGIN_FORBIDDEN"),
    ],
)
def test_accounts_reject_cross_site_and_host_before_auth(app, headers, code):
    with client_for(app) as client:
        response = client.post(
            "/api/auth/login", json={"email": "a@b.test", "password": "any"}, headers=headers
        )
        assert response.status_code == 403 and response.json()["code"] == code


def test_origin_required_unknown_identity_headers_ignored_and_health_local(app):
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post("/api/auth/logout").json()["code"] == "ORIGIN_REQUIRED"
        assert (
            client.get(
                "/api/me", headers={"X-Nishtyak-User": "owner", "X-Nishtyak-Workspace": "local"}
            ).status_code
            == 401
        )
        assert client.get("/api/healthz", headers={"Host": "127.0.0.1:8082"}).status_code == 200
        assert (
            client.post(
                "/api/auth/login", content="x" * 9000, headers={"Origin": ORIGIN}
            ).status_code
            == 413
        )


def test_auth_rate_limits_persist_across_restart_and_hash_slots_bounded(app, settings):
    with client_for(app) as client:
        for _ in range(10):
            assert (
                client.post(
                    "/api/auth/login", json={"email": "none@example.test", "password": "any"}
                ).status_code
                == 401
            )
    with client_for(create_app(settings)) as client:
        assert (
            client.post(
                "/api/auth/login", json={"email": "none@example.test", "password": "any"}
            ).status_code
            == 429
        )
    accounts = app.state.accounts
    with (
        accounts.hashing(),
        accounts.hashing(),
        pytest.raises(LogicClientError) as caught,
        accounts.hashing(),
    ):
        pass
    assert caught.value.code == "AUTH_BUSY"


def test_invite_preview_accept_once_no_plain_tokens_and_roles(app):
    with client_for(app) as owner, client_for(app) as invited, client_for(app) as other:
        register(owner)
        user = register(invited, "invited@example.test")
        register(other, "other@example.test")
        team = team_for(owner)
        invitation, token = invite_for(owner, team["id"], "viewer")
        preview = other.post("/api/invites/preview", json={"token": token})
        assert preview.status_code == 200 and preview.json()["team"]["name"] == team["name"]
        listed = owner.get(f"/api/teams/{team['id']}/invites").json()
        assert listed[0]["url"] is None
        with app.state.accounts.connection() as db:
            stored = dict(db.execute("SELECT * FROM invites").fetchone())
        assert stored["digest"] == token_hash(token) and token not in json.dumps(stored)
        joined = accept(invited, token)
        assert joined.status_code == 200 and joined.json()["myRole"] == "viewer"
        assert accept(invited, token).json() == joined.json()
        assert accept(other, token).status_code == 404
        assert owner.get(f"/api/teams/{team['id']}/invites").json() == []
        assert len(invited.get(f"/api/teams/{team['id']}/members").json()) == 2
        assert invited.get(f"/api/teams/{team['id']}/invites").status_code == 403
        assert (
            invited.post(f"/api/teams/{team['id']}/invites", json={"role": "admin"}).status_code
            == 403
        )
        owner.delete(f"/api/teams/{team['id']}/members/{user['id']}")
        assert accept(invited, token).status_code == 404
        assert invited.get(f"/api/teams/{team['id']}/members").status_code == 404
        assert invitation["role"] == "viewer"


def test_invite_expiration_revocation_creator_demotion_and_existing_member_no_escalation(app):
    with client_for(app) as owner, client_for(app) as member, client_for(app) as admin:
        register(owner)
        member_user = register(member, "member@example.test")
        admin_user = register(admin, "admin@example.test")
        team = team_for(owner)
        revoked, token = invite_for(owner, team["id"])
        assert owner.delete(f"/api/teams/{team['id']}/invites/{revoked['id']}").status_code == 204
        assert accept(member, token).status_code == 404
        expired, token = invite_for(owner, team["id"])
        with app.state.accounts.connection() as db:
            db.execute(
                "UPDATE invites SET expires_at=? WHERE id=?", (time.time() - 1, expired["id"])
            )
        assert accept(member, token).status_code == 404
        _, token = invite_for(owner, team["id"], "member")
        assert accept(member, token).json()["myRole"] == "member"
        _, token = invite_for(owner, team["id"], "admin")
        assert accept(member, token).json()["myRole"] == "member"
        _, token = invite_for(owner, team["id"], "admin")
        assert accept(admin, token).json()["myRole"] == "admin"
        assert (
            admin.patch(
                f"/api/teams/{team['id']}/members/{member_user['id']}", json={"role": "admin"}
            ).status_code
            == 403
        )
        _, token = invite_for(admin, team["id"])
        assert (
            owner.patch(
                f"/api/teams/{team['id']}/members/{admin_user['id']}", json={"role": "viewer"}
            ).status_code
            == 200
        )
        assert accept(member, token).status_code == 404
        assert member.post("/api/invites/preview", json={"token": token}).status_code == 404


def test_team_owner_immutable_admin_limits_and_owner_deletion(app):
    with client_for(app) as owner, client_for(app) as admin, client_for(app) as member:
        owner_user = register(owner)
        admin_user = register(admin, "admin@example.test")
        member_user = register(member, "member@example.test")
        team = team_for(owner)
        for client, role in ((admin, "admin"), (member, "member")):
            _, token = invite_for(owner, team["id"], role)
            accept(client, token)
        path = f"/api/teams/{team['id']}"
        assert (
            owner.patch(path + f"/members/{owner_user['id']}", json={"role": "admin"}).status_code
            == 403
        )
        assert owner.delete(path + f"/members/{owner_user['id']}").status_code == 403
        assert (
            admin.patch(path + f"/members/{member_user['id']}", json={"role": "viewer"}).status_code
            == 200
        )
        assert admin.delete(path + f"/members/{admin_user['id']}").status_code == 403
        assert admin.delete(path).status_code == 403
        assert member.get(path + "/members").status_code == 200
        assert owner.delete(path).status_code == 204
        assert member.get(path + "/members").status_code == 404
        assert len(owner.get("/api/teams").json()) == 1


PROJECT_ROUTES = [
    ("GET", "analysis"),
    ("POST", "analysis"),
    ("GET", "files?path=main.py"),
    ("GET", "decisions"),
    ("PUT", "decisions"),
    ("POST", "plan"),
    ("GET", "preparation"),
    ("GET", "graph"),
    ("POST", "resolve"),
    ("GET", "lock"),
    ("POST", "advice"),
    ("GET", ""),
    ("GET", "readme"),
    ("GET", "verification"),
    ("PUT", "verification"),
    ("POST", "source"),
    ("GET", "archive"),
    ("POST", "archive"),
    ("DELETE", ""),
]


def add_project(app, identity):
    project_id = "a" * 32
    app.state.browser.store.create(
        identity.scope,
        {"project_id": project_id, "job_id": "b" * 32, "status": "queued"},
        "private",
        "py",
        8,
        b"archive",
    )
    return project_id


def test_project_authorization_all_routes_cross_team_legacy_and_removed_members(app):
    with client_for(app) as owner, client_for(app) as other:
        owner_user = register(owner)
        other_user = register(other, "other@example.test")
        team = team_for(owner)
        project_id = add_project(app, WorkspaceIdentity(owner_user["id"], team["id"], "accounts"))
        for method, endpoint in PROJECT_ROUTES:
            response = other.request(
                method,
                f"/api/projects/{project_id}/" + endpoint
                if endpoint
                else f"/api/projects/{project_id}",
                json={},
            )
            assert response.status_code == 404, (method, endpoint, response.text)
        _, token = invite_for(owner, team["id"])
        accept(other, token)
        assert other.get(f"/api/teams/{team['id']}/members").status_code == 200
        assert (
            owner.delete(f"/api/teams/{team['id']}/members/{other_user['id']}").status_code == 204
        )
        assert other.get(f"/api/projects/{project_id}/analysis").status_code == 404
        with app.state.browser.store.connection() as db:
            db.execute(
                "UPDATE projects SET scope=? WHERE id=?",
                (WorkspaceIdentity("reviewer", "preview", "preview").scope, project_id),
            )
        assert owner.get(f"/api/projects/{project_id}/analysis").status_code == 404


def test_viewer_can_read_plan_but_cannot_mutate_project_and_headers_cannot_switch_team(
    app, monkeypatch
):
    with client_for(app) as owner, client_for(app) as viewer:
        owner_user = register(owner)
        viewer_user = register(viewer, "viewer@example.test")
        team = team_for(owner)
        project_id = add_project(app, WorkspaceIdentity(owner_user["id"], team["id"], "accounts"))
        _, token = invite_for(owner, team["id"], "viewer")
        accept(viewer, token)
        for method, endpoint in PROJECT_ROUTES:
            if method in {"GET", "HEAD"} or endpoint == "plan":
                continue
            url = (
                f"/api/projects/{project_id}/" + endpoint
                if endpoint
                else f"/api/projects/{project_id}"
            )
            response = viewer.request(method, url, json={})
            assert response.status_code == 403, (method, endpoint, response.text)
        assert viewer.post(f"/api/teams/{team['id']}/projects").status_code == 403
        seen = []
        monkeypatch.setattr(
            app.state.browser,
            "plan",
            lambda identity, *args: seen.append(identity) or {"state": "draft"},
        )
        assert viewer.post(f"/api/projects/{project_id}/plan", json={}).status_code == 200
        assert seen[0].role == "viewer" and seen[0].workspace == team["id"]
        assert seen[0].user == viewer_user["id"]
        owner.patch(f"/api/teams/{team['id']}/members/{viewer_user['id']}", json={"role": "member"})
        viewer.post(
            f"/api/projects/{project_id}/plan",
            json={},
            headers={"X-Nishtyak-Workspace": "forged", "X-Nishtyak-User": owner_user["id"]},
        )
        assert seen[-1].role == "member" and seen[-1].user == viewer_user["id"]


def test_account_configuration_requires_https_and_valid_session_lifetime(settings):
    with pytest.raises(ValueError):
        replace(settings, origins=("http://localhost:8082",))
    with pytest.raises(ValueError):
        replace(settings, account_session_seconds=30)
    assert AccountStore.cookie_token(f"{SESSION_COOKIE}=malformed") is None


def test_concurrent_invitation_acceptance_has_only_one_winner(app):
    from concurrent.futures import ThreadPoolExecutor

    accounts = app.state.accounts
    owner, _ = accounts.register("Owner", "owner@example.test", PASSWORD, "test")
    first, _ = accounts.register("First", "first@example.test", PASSWORD, "test")
    second, _ = accounts.register("Second", "second@example.test", PASSWORD, "test")
    team = accounts.create_team(owner["id"], "Laboratory")
    invitation = accounts.create_invite(owner["id"], team["id"], "member", ORIGIN)
    token = invitation["url"].split("#token=")[1]

    def join(user):
        try:
            return accounts.accept_invite(user["id"], token)
        except LogicClientError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(join, [first, second]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert 404 in results
    assert accounts.team(owner["id"], team["id"])["membersCount"] == 2


def test_expired_records_cleanup_and_invite_retention_bound(app):
    accounts = app.state.accounts
    user, _ = accounts.register("Author", "author@example.test", PASSWORD, "test")
    team = accounts.create_team(user["id"], "Lab")
    invite = accounts.create_invite(user["id"], team["id"], "member", ORIGIN)
    with accounts.connection() as db:
        db.execute("UPDATE invites SET expires_at=?", (time.time() - 2 * 86400,))
        db.execute("UPDATE sessions SET expires_at=?", (time.time() - 1,))
        db.execute("UPDATE account_limits SET expires_at=?", (time.time() - 1,))
    accounts.cleanup()
    with accounts.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM account_limits").fetchone()[0] == 0
        now = time.time()
        db.executemany(
            "INSERT INTO invites VALUES(?,?,?,?,?,?,?,?,NULL,NULL)",
            [
                (
                    str(index),
                    token_hash(str(index)),
                    team["id"],
                    "member",
                    user["id"],
                    now,
                    now + 86400,
                    now,
                )
                for index in range(1000)
            ],
        )
    with pytest.raises(LogicClientError) as caught:
        accounts.create_invite(user["id"], team["id"], "member", ORIGIN)
    assert caught.value.code == "INVITE_LIMIT"
    assert invite["status"] == "active"


def test_sessions_per_user_are_bounded_and_auth_database_permissions(app):
    accounts = app.state.accounts
    user, first = accounts.register("Author", "author@example.test", PASSWORD, "test")
    with accounts.connection(write=True) as db:
        tokens = [accounts._new_session(db, user["id"]) for _ in range(12)]
        count = db.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=?", (user["id"],)
        ).fetchone()[0]
    assert count == 10
    assert accounts.authenticate(f"{SESSION_COOKIE}={first}") is None
    assert accounts.authenticate(f"{SESSION_COOKIE}={tokens[-1]}") == user["id"]
    assert accounts.path.stat().st_mode & 0o777 == 0o600


def test_explicit_team_delete_removes_projects_before_revoking_workspace(app, monkeypatch):
    calls = []

    class Core:
        def delete(self, project_id):
            calls.append(project_id)

    monkeypatch.setattr(app.state.browser, "client_factory", lambda scope: Core())
    with client_for(app) as owner:
        user = register(owner)
        team = team_for(owner)
        project_id = add_project(app, WorkspaceIdentity(user["id"], team["id"], "accounts"))
        assert (app.state.browser.store.root / project_id).is_dir()
        result = owner.delete(f"/api/teams/{team['id']}")
        assert result.status_code == 204, result.text
        assert calls == [project_id]
        assert not (app.state.browser.store.root / project_id).exists()
        assert owner.get(f"/api/projects/{project_id}").status_code == 404


def test_team_delete_failure_keeps_workspace_available_for_retry(app, monkeypatch):
    class Core:
        failing = True

        def delete(self, project_id):
            if self.failing:
                raise LogicClientError(503, "CORE_UNAVAILABLE", "Try deleting again")

    core = Core()
    monkeypatch.setattr(app.state.browser, "client_factory", lambda scope: core)
    with client_for(app) as owner:
        user = register(owner)
        team = team_for(owner)
        project_id = add_project(app, WorkspaceIdentity(user["id"], team["id"], "accounts"))
        result = owner.delete(f"/api/teams/{team['id']}")
        assert result.status_code == 503
        assert any(item["id"] == team["id"] for item in owner.get("/api/teams").json())
        assert (app.state.browser.store.root / project_id).exists()
        core.failing = False
        assert owner.delete(f"/api/teams/{team['id']}").status_code == 204
        assert not (app.state.browser.store.root / project_id).exists()


def test_upload_authorized_before_team_deletion_cannot_create_orphan(app, monkeypatch):
    deleting = Event()
    release_delete = Event()
    upload_waiting = Event()
    submissions = []
    browser = app.state.browser
    locked = browser.store.workspace_locked

    class Core:
        def delete(self, project_id):
            deleting.set()
            assert release_delete.wait(5)

        def submit(self, content, options):
            submissions.append(content)
            return {"project_id": "c" * 32, "job_id": "d" * 32, "status": "queued"}

    @contextmanager
    def observed_lock(scope):
        if deleting.is_set():
            upload_waiting.set()
        with locked(scope):
            yield

    monkeypatch.setattr(browser, "client_factory", lambda scope: Core())
    monkeypatch.setattr(browser.store, "workspace_locked", observed_lock)
    with client_for(app) as owner, ThreadPoolExecutor(max_workers=2) as pool:
        user = register(owner)
        team = team_for(owner)
        identity = WorkspaceIdentity(user["id"], team["id"], "accounts")
        project_id = add_project(app, identity)
        deletion = pool.submit(owner.delete, f"/api/teams/{team['id']}")
        try:
            assert deleting.wait(5)
            # The request already passed middleware authorization while the team existed.
            upload = pool.submit(browser.upload, identity, team["id"], "new.py", b"print(1)\n")
            assert upload_waiting.wait(5)
            assert not upload.done()
        finally:
            release_delete.set()
        assert deletion.result(timeout=5).status_code == 204
        with pytest.raises(LogicClientError) as caught:
            upload.result(timeout=5)
        assert caught.value.status == 404
        assert not submissions
        assert browser.store.list(identity.scope) == []
        assert not (browser.store.root / project_id).exists()
        assert all(item["id"] != team["id"] for item in owner.get("/api/teams").json())
