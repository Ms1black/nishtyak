from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response

from .accounts import SESSION_COOKIE, AccountStore
from .config import WebSettings

AccountId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]
Role = Literal["admin", "member", "viewer"]


class AccountBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class LoginBody(AccountBody):
    email: Annotated[str, Field(min_length=1, max_length=254)]
    password: Annotated[str, Field(min_length=1, max_length=128)]


class RegisterBody(LoginBody):
    name: Annotated[str, Field(min_length=1, max_length=80)]


class NameBody(AccountBody):
    name: Annotated[str, Field(min_length=1, max_length=80)]


class RoleBody(AccountBody):
    role: Role


class InviteBody(AccountBody):
    token: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]


def account_router(accounts: AccountStore, settings: WebSettings) -> APIRouter:
    router = APIRouter(prefix="/api")

    def signed_in(user: dict, token: str, status: int = 200):
        response = JSONResponse(user, status)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=settings.account_session_seconds,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @router.post("/auth/register", status_code=201)
    def register(request: Request, body: RegisterBody):
        user, token = accounts.register(
            body.name,
            body.email,
            body.password,
            request.client.host if request.client else "unknown",
        )
        return signed_in(user, token, 201)

    @router.post("/auth/login")
    def login(request: Request, body: LoginBody):
        user, token = accounts.login(
            body.email, body.password, request.client.host if request.client else "unknown"
        )
        accounts.logout(request.headers.get("cookie", ""))
        return signed_in(user, token)

    @router.post("/auth/logout", status_code=204)
    def logout(request: Request):
        accounts.logout(request.headers.get("cookie", ""))
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE, secure=True, httponly=True, samesite="strict", path="/"
        )
        return response

    @router.get("/session")
    def session(request: Request):
        identity = request.state.identity
        return {
            "mode": "accounts",
            "user": accounts.user(identity.user),
            "team": accounts.team(identity.user, identity.workspace),
            "capabilities": {
                "accountManagement": True,
                "teamManagement": True,
                "sharedWorkspace": False,
            },
        }

    @router.get("/me")
    def me(request: Request):
        return accounts.user(request.state.identity.user)

    @router.patch("/me")
    def update_me(request: Request, body: NameBody):
        return accounts.update_user(request.state.identity.user, body.name)

    @router.get("/teams")
    def teams(request: Request):
        return accounts.teams(request.state.identity.user)

    @router.post("/teams", status_code=201)
    def create_team(request: Request, body: NameBody):
        return accounts.create_team(request.state.identity.user, body.name)

    @router.delete("/teams/{team_id}", status_code=204)
    def delete_team(request: Request, team_id: AccountId):
        accounts.delete_team(request.state.identity.user, team_id)
        return Response(status_code=204)

    @router.get("/teams/{team_id}/members")
    def members(request: Request, team_id: AccountId):
        return accounts.members(request.state.identity.user, team_id)

    @router.patch("/teams/{team_id}/members/{member_id}")
    def update_member(request: Request, team_id: AccountId, member_id: AccountId, body: RoleBody):
        return accounts.change_member(request.state.identity.user, team_id, member_id, body.role)

    @router.delete("/teams/{team_id}/members/{member_id}", status_code=204)
    def delete_member(request: Request, team_id: AccountId, member_id: AccountId):
        accounts.change_member(request.state.identity.user, team_id, member_id, None)
        return Response(status_code=204)

    @router.get("/teams/{team_id}/invites")
    def invites(request: Request, team_id: AccountId):
        return accounts.invites(request.state.identity.user, team_id)

    @router.post("/teams/{team_id}/invites", status_code=201)
    def create_invite(request: Request, team_id: AccountId, body: RoleBody):
        host = request.headers.get("host", "").lower()
        origin = next(value for value in settings.origins if urlsplit(value).netloc.lower() == host)
        return accounts.create_invite(request.state.identity.user, team_id, body.role, origin)

    @router.delete("/teams/{team_id}/invites/{invite_id}", status_code=204)
    def revoke_invite(request: Request, team_id: AccountId, invite_id: AccountId):
        accounts.revoke_invite(request.state.identity.user, team_id, invite_id)
        return Response(status_code=204)

    @router.post("/invites/preview")
    def preview_invite(body: InviteBody):
        return accounts.preview_invite(body.token)

    @router.post("/invites/accept")
    def accept_invite(request: Request, body: InviteBody):
        return accounts.accept_invite(request.state.identity.user, body.token)

    return router
