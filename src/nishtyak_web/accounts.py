from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from nishtyak_client import LogicClientError

from .config import WebSettings

SESSION_COOKIE = "__Host-nishtyak_session"
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
ROLES = {"admin", "member", "viewer"}


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def workspace_scope(team_id: str) -> str:
    return "web:" + hashlib.sha256(("accounts:" + team_id).encode()).hexdigest()


def clean_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 80 or any(ord(char) < 32 for char in value):
        raise LogicClientError(422, "INVALID_NAME", "Name must contain 1 to 80 characters")
    return value


def clean_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}",
        value,
    ):
        raise LogicClientError(422, "INVALID_EMAIL", "Enter a valid email address")
    return value


class AccountStore:
    """Durable accounts and team permissions. Tokens are stored only as SHA-256 digests."""

    def __init__(self, settings: WebSettings):
        self.settings = settings
        settings.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = settings.data_dir / "accounts.sqlite3"
        self.hasher = PasswordHasher()
        self._hash_slots = threading.BoundedSemaphore(2)
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS teams (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    owner_id TEXT NOT NULL REFERENCES users(id), personal INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL,
                    joined_at REAL NOT NULL, PRIMARY KEY(team_id,user_id)
                );
                CREATE INDEX IF NOT EXISTS memberships_user ON memberships(user_id);
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL, expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS invites (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
                    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id),
                    created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    revoked_at REAL, accepted_by TEXT REFERENCES users(id), accepted_at REAL
                );
                CREATE INDEX IF NOT EXISTS invites_team ON invites(team_id);
                CREATE TABLE IF NOT EXISTS account_limits (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL
                );
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self, write: bool = False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def hashing(self):
        if not self._hash_slots.acquire(blocking=False):
            raise LogicClientError(429, "AUTH_BUSY", "Try signing in again shortly")
        try:
            yield
        finally:
            self._hash_slots.release()

    def rate_limit(self, action: str, email: str, address: str) -> None:
        now = time.time()
        limits = [
            ("all", 120, 60),
            (action + ":email:" + token_hash(email.casefold()), 10, 900),
        ]
        if action == "register":
            limits.extend([("registrations", 100, 3600), ("ip:" + token_hash(address), 30, 3600)])
        with self.connection(write=True) as db:
            db.execute("DELETE FROM account_limits WHERE expires_at<=?", (now,))
            for key, maximum, _duration in limits:
                row = db.execute("SELECT count FROM account_limits WHERE key=?", (key,)).fetchone()
                if row and row[0] >= maximum:
                    raise LogicClientError(429, "AUTH_RATE_LIMIT", "Try again later")
            if db.execute("SELECT COUNT(*) FROM account_limits").fetchone()[0] >= 10000:
                raise LogicClientError(429, "AUTH_RATE_LIMIT", "Try again later")
            for key, _, duration in limits:
                db.execute(
                    "INSERT INTO account_limits VALUES(?,1,?) ON CONFLICT(key) "
                    "DO UPDATE SET count=count+1",
                    (key, now + duration),
                )

    def register(self, name: str, email: str, password: str, address: str) -> tuple[dict, str]:
        name, email = clean_name(name), clean_email(email)
        if not 12 <= len(password) <= 128 or "\x00" in password:
            raise LogicClientError(
                422, "INVALID_PASSWORD", "Password must contain 12 to 128 characters"
            )
        self.rate_limit("register", email, address)
        with self.hashing():
            encoded = self.hasher.hash(password)
        now, user_id = time.time(), uuid.uuid4().hex
        with self.connection(write=True) as db:
            if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 10000:
                raise LogicClientError(429, "ACCOUNT_LIMIT", "Account capacity reached")
            try:
                db.execute(
                    "INSERT INTO users VALUES(?,?,?,?,?)", (user_id, name, email, encoded, now)
                )
            except sqlite3.IntegrityError as error:
                raise LogicClientError(
                    409, "EMAIL_EXISTS", "An account with this email exists"
                ) from error
            self._create_team(db, user_id, "Личное пространство", personal=True)
            token = self._new_session(db, user_id)
            return self._user(db, user_id), token

    def login(self, email: str, password: str, address: str) -> tuple[dict, str]:
        email = email.strip().casefold()
        self.rate_limit("login", email, address)
        with self.connection() as db:
            row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        with self.hashing():
            try:
                self.hasher.verify(row["password_hash"] if row else self._dummy_hash, password)
            except (VerificationError, InvalidHashError):
                raise LogicClientError(
                    401, "INVALID_CREDENTIALS", "Incorrect email or password"
                ) from None
            if row is None:
                raise LogicClientError(401, "INVALID_CREDENTIALS", "Incorrect email or password")
            updated = (
                self.hasher.hash(password)
                if self.hasher.check_needs_rehash(row["password_hash"])
                else None
            )
        with self.connection(write=True) as db:
            if updated:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (updated, row["id"]))
            return self._user(db, row["id"]), self._new_session(db, row["id"])

    def _new_session(self, db, user_id: str) -> str:
        now, token = time.time(), secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        db.execute(
            "DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions WHERE user_id=? "
            "ORDER BY created_at DESC LIMIT -1 OFFSET 9)",
            (user_id,),
        )
        db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)",
            (token_hash(token), user_id, now, now + self.settings.account_session_seconds),
        )
        return token

    @staticmethod
    def cookie_token(header: str) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except CookieError:
            return None
        value = cookie.get(SESSION_COOKIE)
        return value.value if value and TOKEN_PATTERN.fullmatch(value.value) else None

    def authenticate(self, header: str) -> str | None:
        token = self.cookie_token(header)
        if token is None:
            return None
        with self.connection() as db:
            row = db.execute(
                "SELECT user_id FROM sessions WHERE digest=? AND expires_at>?",
                (token_hash(token), time.time()),
            ).fetchone()
            return row[0] if row else None

    def logout(self, header: str) -> None:
        token = self.cookie_token(header)
        if token:
            with self.connection(write=True) as db:
                db.execute("DELETE FROM sessions WHERE digest=?", (token_hash(token),))

    @staticmethod
    def _user(db, user_id: str) -> dict:
        row = db.execute("SELECT id,name,email FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise LogicClientError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue")
        return {**dict(row), "avatarUrl": None}

    def user(self, user_id: str) -> dict:
        with self.connection() as db:
            return self._user(db, user_id)

    def update_user(self, user_id: str, name: str) -> dict:
        name = clean_name(name)
        with self.connection(write=True) as db:
            db.execute("UPDATE users SET name=? WHERE id=?", (name, user_id))
            return self._user(db, user_id)

    @staticmethod
    def _membership(db, user_id: str, team_id: str):
        row = db.execute(
            "SELECT * FROM memberships WHERE user_id=? AND team_id=?", (user_id, team_id)
        ).fetchone()
        if row is None:
            raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Workspace was not found")
        return row

    def workspace(self, user_id: str, team_id: str | None = None, scope: str | None = None) -> dict:
        with self.connection() as db:
            if scope:
                row = db.execute("SELECT id FROM teams WHERE scope=?", (scope,)).fetchone()
                if row is None:
                    raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Workspace was not found")
                team_id = row[0]
            if team_id is None:
                row = db.execute(
                    "SELECT id FROM teams WHERE owner_id=? AND personal=1", (user_id,)
                ).fetchone()
                if row is None:
                    raise LogicClientError(401, "AUTHENTICATION_REQUIRED", "Sign in to continue")
                team_id = row[0]
            return dict(self._membership(db, user_id, team_id))

    @staticmethod
    def _team(db, user_id: str, team_id: str) -> dict:
        row = db.execute(
            "SELECT t.id,t.name,t.personal,m.role FROM teams t "
            "JOIN memberships m ON m.team_id=t.id WHERE t.id=? AND m.user_id=?",
            (team_id, user_id),
        ).fetchone()
        if row is None:
            raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Workspace was not found")
        count = db.execute(
            "SELECT COUNT(*) FROM memberships WHERE team_id=?", (team_id,)
        ).fetchone()[0]
        return {
            "id": row["id"],
            "name": row["name"],
            "personal": bool(row["personal"]),
            "myRole": row["role"],
            "membersCount": count,
            "avatarUrl": None,
        }

    def teams(self, user_id: str) -> list[dict]:
        with self.connection() as db:
            ids = db.execute(
                "SELECT team_id FROM memberships WHERE user_id=? ORDER BY joined_at", (user_id,)
            ).fetchall()
            return [self._team(db, user_id, row[0]) for row in ids]

    def team(self, user_id: str, team_id: str) -> dict:
        with self.connection() as db:
            return self._team(db, user_id, team_id)

    @staticmethod
    def _create_team(db, user_id: str, name: str, personal: bool = False) -> str:
        if (
            db.execute("SELECT COUNT(*) FROM teams WHERE owner_id=?", (user_id,)).fetchone()[0]
            >= 20
        ):
            raise LogicClientError(429, "TEAM_LIMIT", "Team limit reached")
        team_id, now = uuid.uuid4().hex, time.time()
        db.execute(
            "INSERT INTO teams VALUES(?,?,?,?,?,?)",
            (team_id, workspace_scope(team_id), name, user_id, int(personal), now),
        )
        db.execute("INSERT INTO memberships VALUES(?,?,?,?)", (team_id, user_id, "owner", now))
        return team_id

    def create_team(self, user_id: str, name: str) -> dict:
        name = clean_name(name)
        with self.connection(write=True) as db:
            return self._team(db, user_id, self._create_team(db, user_id, name))

    def delete_team(self, user_id: str, team_id: str, before_delete=None) -> None:
        with self.connection(write=True) as db:
            team = self._team(db, user_id, team_id)
            if team["myRole"] != "owner":
                raise LogicClientError(403, "ROLE_FORBIDDEN", "Only the owner can delete a team")
            if team["personal"]:
                raise LogicClientError(
                    409, "PERSONAL_WORKSPACE_REQUIRED", "The personal workspace cannot be deleted"
                )
            if before_delete is not None:
                before_delete()
            db.execute("DELETE FROM teams WHERE id=?", (team_id,))

    def members(self, user_id: str, team_id: str) -> list[dict]:
        with self.connection() as db:
            self._membership(db, user_id, team_id)
            return [
                self._member(db, user_id, team_id, row[0])
                for row in db.execute(
                    "SELECT user_id FROM memberships WHERE team_id=? ORDER BY joined_at", (team_id,)
                ).fetchall()
            ]

    @staticmethod
    def _member(db, actor_id: str, team_id: str, user_id: str) -> dict:
        membership = AccountStore._membership(db, user_id, team_id)
        return {
            **AccountStore._user(db, user_id),
            "role": membership["role"],
            "joinedAt": timestamp(membership["joined_at"]),
            "isYou": actor_id == user_id,
        }

    @staticmethod
    def _manage(
        db, actor_id: str, team_id: str, target_role: str | None = None, new_role: str | None = None
    ):
        role = AccountStore._membership(db, actor_id, team_id)["role"]
        if (
            role not in {"owner", "admin"}
            or target_role == "owner"
            or (role == "admin" and "admin" in {target_role, new_role})
        ):
            raise LogicClientError(403, "ROLE_FORBIDDEN", "Insufficient team permissions")

    def change_member(
        self, actor_id: str, team_id: str, user_id: str, role: str | None
    ) -> dict | None:
        with self.connection(write=True) as db:
            target = self._membership(db, user_id, team_id)
            self._manage(db, actor_id, team_id, target["role"], role)
            if role is None:
                db.execute(
                    "DELETE FROM memberships WHERE team_id=? AND user_id=?", (team_id, user_id)
                )
                return None
            if role not in ROLES:
                raise LogicClientError(422, "INVALID_ROLE", "Unknown team role")
            db.execute(
                "UPDATE memberships SET role=? WHERE team_id=? AND user_id=?",
                (role, team_id, user_id),
            )
            return self._member(db, actor_id, team_id, user_id)

    @staticmethod
    def _invite(row, url: str | None = None) -> dict:
        status = (
            "revoked"
            if row["revoked_at"]
            else "accepted"
            if row["accepted_by"]
            else "expired"
            if row["expires_at"] <= time.time()
            else "active"
        )
        return {
            "id": row["id"],
            "url": url,
            "role": row["role"],
            "expiresAt": timestamp(row["expires_at"]),
            "createdAt": timestamp(row["created_at"]),
            "status": status,
        }

    def invites(self, user_id: str, team_id: str) -> list[dict]:
        with self.connection() as db:
            self._manage(db, user_id, team_id)
            return [
                self._invite(row)
                for row in db.execute(
                    "SELECT * FROM invites WHERE team_id=? AND expires_at>? "
                    "AND revoked_at IS NULL AND accepted_by IS NULL ORDER BY created_at DESC",
                    (team_id, time.time()),
                )
            ]

    def create_invite(self, user_id: str, team_id: str, role: str, origin: str) -> dict:
        if role not in ROLES:
            raise LogicClientError(422, "INVALID_ROLE", "Unknown team role")
        with self.connection(write=True) as db:
            self._manage(db, user_id, team_id, new_role=role)
            if (
                db.execute(
                    "SELECT COUNT(*) FROM invites WHERE team_id=? AND expires_at>? "
                    "AND revoked_at IS NULL AND accepted_by IS NULL",
                    (team_id, time.time()),
                ).fetchone()[0]
                >= 100
            ):
                raise LogicClientError(429, "INVITE_LIMIT", "Active invitation limit reached")
            if (
                db.execute("SELECT COUNT(*) FROM invites WHERE team_id=?", (team_id,)).fetchone()[0]
                >= 1000
            ):
                raise LogicClientError(429, "INVITE_LIMIT", "Invitation limit reached")
            token, invite_id, now = secrets.token_urlsafe(32), uuid.uuid4().hex, time.time()
            db.execute(
                "INSERT INTO invites VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)",
                (invite_id, token_hash(token), team_id, role, user_id, now, now + 7 * 86400),
            )
            row = db.execute("SELECT * FROM invites WHERE id=?", (invite_id,)).fetchone()
            return self._invite(row, origin + "/invite/team#token=" + token)

    def revoke_invite(self, user_id: str, team_id: str, invite_id: str) -> None:
        with self.connection(write=True) as db:
            self._manage(db, user_id, team_id)
            row = db.execute(
                "SELECT * FROM invites WHERE id=? AND team_id=?", (invite_id, team_id)
            ).fetchone()
            if row is None:
                raise LogicClientError(404, "RESOURCE_NOT_FOUND", "Invitation was not found")
            self._manage(db, user_id, team_id, new_role=row["role"])
            db.execute("UPDATE invites SET revoked_at=? WHERE id=?", (time.time(), invite_id))

    @staticmethod
    def _valid_invite(db, token: str, user_id: str | None = None):
        if not TOKEN_PATTERN.fullmatch(token):
            raise LogicClientError(404, "INVITE_UNAVAILABLE", "Invitation is unavailable")
        row = db.execute(
            "SELECT i.*,t.name FROM invites i JOIN teams t ON t.id=i.team_id WHERE digest=?",
            (token_hash(token),),
        ).fetchone()
        if (
            row is None
            or row["revoked_at"]
            or row["expires_at"] <= time.time()
            or (row["accepted_by"] and row["accepted_by"] != user_id)
        ):
            raise LogicClientError(404, "INVITE_UNAVAILABLE", "Invitation is unavailable")
        if not row["accepted_by"]:
            creator = db.execute(
                "SELECT role FROM memberships WHERE user_id=? AND team_id=?",
                (row["created_by"], row["team_id"]),
            ).fetchone()
            if (
                creator is None
                or creator[0] not in {"owner", "admin"}
                or (row["role"] == "admin" and creator[0] != "owner")
            ):
                raise LogicClientError(404, "INVITE_UNAVAILABLE", "Invitation is unavailable")
        return row

    def preview_invite(self, token: str) -> dict:
        with self.connection() as db:
            row = self._valid_invite(db, token)
            return {
                "team": {"id": row["team_id"], "name": row["name"]},
                "role": row["role"],
                "expiresAt": timestamp(row["expires_at"]),
                "status": "active",
            }

    def accept_invite(self, user_id: str, token: str) -> dict:
        with self.connection(write=True) as db:
            row = self._valid_invite(db, token, user_id)
            existing = db.execute(
                "SELECT role FROM memberships WHERE user_id=? AND team_id=?",
                (user_id, row["team_id"]),
            ).fetchone()
            if row["accepted_by"]:
                if not existing:
                    raise LogicClientError(404, "INVITE_UNAVAILABLE", "Invitation is unavailable")
                return self._team(db, user_id, row["team_id"])
            if not existing:
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM memberships WHERE team_id=?", (row["team_id"],)
                    ).fetchone()[0]
                    >= 200
                ):
                    raise LogicClientError(429, "MEMBER_LIMIT", "Team membership limit reached")
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM memberships WHERE user_id=?", (user_id,)
                    ).fetchone()[0]
                    >= 100
                ):
                    raise LogicClientError(429, "TEAM_LIMIT", "Team membership limit reached")
                db.execute(
                    "INSERT INTO memberships VALUES(?,?,?,?)",
                    (row["team_id"], user_id, row["role"], time.time()),
                )
            db.execute(
                "UPDATE invites SET accepted_by=?,accepted_at=? WHERE id=?",
                (user_id, time.time(), row["id"]),
            )
            return self._team(db, user_id, row["team_id"])

    def cleanup(self) -> None:
        with self.connection(write=True) as db:
            now = time.time()
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM account_limits WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM invites WHERE expires_at<=?", (now - 86400,))
