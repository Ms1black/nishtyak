from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from nishtyak_client import LogicClientError
from nishtyak_core.archives import ArchiveError
from nishtyak_core.snapshot import SnapshotError
from nishtyak_service.dto import AuthorEnvironmentDTO, ResolutionTargetDTO

from .auth import GatewayBoundary
from .config import WebSettings
from .preview import COOKIE_NAME, PreviewAccess, PreviewLoginError, access_page, safe_next
from .service import BrowserService

ProjectId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]
TeamId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]


class StrictBody(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class SaveBody(StrictBody):
    expectedRevision: Annotated[int, Field(ge=0, le=2**53 - 1)]
    decisions: dict


class ArchiveBody(StrictBody):
    includeDockerfile: bool = False
    lockId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None


class AnalysisBody(StrictBody):
    options: dict
    authorEnvironment: AuthorEnvironmentDTO | None = None


class TargetBody(StrictBody):
    pythonVersion: str
    platform: str


class ResolveBody(StrictBody):
    target: TargetBody
    decisions: dict | None = None


class AdviceBody(StrictBody):
    feature: Literal["explain", "readme", "packages", "entrypoint"] = "explain"
    description: Annotated[str, Field(max_length=8192)] = ""
    shareSource: Literal[True]


def create_app(
    settings: WebSettings, service: BrowserService | None = None, static_dir: FilePath | None = None
) -> FastAPI:
    browser = service or BrowserService(settings)
    preview = PreviewAccess(settings) if settings.mode == "preview" else None
    workspace_started = datetime.now(UTC).isoformat()

    @asynccontextmanager
    async def lifespan(app):
        async def expire_projects():
            while True:
                await run_in_threadpool(browser.store.cleanup)
                await asyncio.sleep(30)

        cleanup = asyncio.create_task(expire_projects())
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup

    app = FastAPI(
        title="Nishtyak browser gateway",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.browser = browser
    app.add_middleware(GatewayBoundary, settings=settings, preview=preview)

    @app.exception_handler(LogicClientError)
    async def client_error(request: Request, error: LogicClientError):
        payload = {"code": error.code, "message": error.args[0]}
        if error.field:
            payload["field"] = error.field
        return JSONResponse(payload, error.status)

    @app.exception_handler(ArchiveError)
    async def archive_error(request: Request, error: ArchiveError):
        return JSONResponse({"code": error.code, "message": str(error)}, 422)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    @app.exception_handler(SnapshotError)
    @app.exception_handler(ValueError)
    async def invalid_request(request: Request, error: Exception):
        return JSONResponse({"code": "INVALID_REQUEST", "message": "Invalid request fields"}, 422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):
        return JSONResponse(
            {"code": "GATEWAY_ERROR", "message": "Browser gateway failed"},
            500,
            {"Cache-Control": "no-store"},
        )

    @app.get("/api/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/api/capabilities")
    def capabilities(request: Request):
        return browser.capabilities(request.state.identity)

    if preview is not None:

        @app.get("/preview/access", response_class=HTMLResponse, include_in_schema=False)
        def preview_form(next: str = "/team/projects"):
            return HTMLResponse(
                access_page(next),
                headers={
                    "Cache-Control": "no-store",
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                },
            )

        @app.post("/preview/access", include_in_schema=False)
        async def preview_login(request: Request):
            if request.headers.get("content-type", "").split(";", 1)[0] != (
                "application/x-www-form-urlencoded"
            ):
                return JSONResponse({"code": "INVALID_LOGIN_FORM"}, 422)
            async with request.form(max_files=0, max_fields=2) as form:
                if set(form) != {"passphrase", "next"} or any(
                    len(form.getlist(key)) != 1 or not isinstance(form[key], str) for key in form
                ):
                    return JSONResponse({"code": "INVALID_LOGIN_FORM"}, 422)
                try:
                    cookie = await run_in_threadpool(preview.authenticate, form["passphrase"])
                except PreviewLoginError as error:
                    return JSONResponse(
                        {"code": error.code, "message": "Не удалось войти в режим проверки"},
                        error.status,
                        {"Retry-After": "60"} if error.status == 429 else None,
                    )
                response = RedirectResponse(safe_next(form["next"]), 303)
                response.set_cookie(
                    COOKIE_NAME,
                    cookie,
                    max_age=settings.preview_session_seconds,
                    secure=True,
                    httponly=True,
                    samesite="strict",
                    path="/",
                )
                return response

        @app.post("/preview/logout", include_in_schema=False)
        def preview_logout():
            response = RedirectResponse("/preview/access", 303)
            response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="strict")
            return response

    def workspace_metadata(identity):
        local = identity.mode == "local"
        review = identity.mode == "preview"
        user = {
            "id": identity.user,
            "name": "Участник проверки"
            if review
            else "Локальная интеграция"
            if local
            else identity.user,
            "email": "local@localhost" if local else "",
            "avatarUrl": None,
        }
        team = {
            "id": identity.workspace,
            "name": "Общая проверка"
            if review
            else "Локальный проект"
            if local
            else identity.workspace,
            "myRole": "owner",
            "membersCount": 1,
            "avatarUrl": None,
        }
        return user, team

    @app.get("/api/session")
    def session(request: Request):
        identity = request.state.identity
        user, team = workspace_metadata(identity)
        return {
            "mode": identity.mode,
            "user": user,
            "team": team,
            "capabilities": {
                "accountManagement": False,
                "teamManagement": False,
                "sharedWorkspace": identity.mode == "preview",
            },
        }

    @app.get("/api/me")
    def me(request: Request):
        return workspace_metadata(request.state.identity)[0]

    @app.get("/api/teams")
    def teams(request: Request):
        return [workspace_metadata(request.state.identity)[1]]

    @app.get("/api/teams/{team_id}/members")
    def members(request: Request, team_id: TeamId):
        browser._team(request.state.identity, team_id)
        user, _ = workspace_metadata(request.state.identity)
        return [{**user, "role": "owner", "joinedAt": workspace_started, "isYou": True}]

    @app.get("/api/teams/{team_id}/invites")
    def invites(request: Request, team_id: TeamId):
        browser._team(request.state.identity, team_id)
        return []

    def account_unavailable():
        return JSONResponse(
            {
                "code": "ACCOUNT_BACKEND_UNAVAILABLE",
                "message": "Регистрацию, команды и приглашения разрабатываем отдельно",
            },
            501,
        )

    app.add_api_route("/api/me", account_unavailable, methods=["PATCH", "PUT"])
    app.add_api_route("/api/teams", account_unavailable, methods=["POST"])

    @app.delete("/api/teams/{team_id}")
    @app.post("/api/teams/{team_id}/invites")
    @app.delete("/api/teams/{team_id}/invites/{item_id}")
    @app.patch("/api/teams/{team_id}/members/{item_id}")
    @app.delete("/api/teams/{team_id}/members/{item_id}")
    def team_mutation(request: Request, team_id: TeamId, item_id: str = ""):
        browser._team(request.state.identity, team_id)
        return account_unavailable()

    @app.post("/api/auth/logout")
    def logout():
        if preview is None:
            return account_unavailable()
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/api/teams/{team_id}/projects")
    def list_projects(request: Request, team_id: TeamId):
        return browser.list(request.state.identity, team_id)

    @app.post("/api/teams/{team_id}/projects", status_code=202)
    async def upload(request: Request, team_id: TeamId):
        async with request.form(max_files=1, max_fields=0, max_part_size=10 * 1024**2) as form:
            if set(form) != {"file"} or len(form.getlist("file")) != 1:
                raise LogicClientError(422, "INVALID_UPLOAD", "Expected one .py or .zip file")
            file = form["file"]
            if not isinstance(file, UploadFile):
                raise LogicClientError(422, "INVALID_UPLOAD", "Expected a file upload")
            content = await file.read(10 * 1024**2 + 1)
            return await run_in_threadpool(
                browser.upload, request.state.identity, team_id, file.filename or "", content
            )

    @app.get("/api/projects/{project_id}/analysis")
    def analysis(request: Request, project_id: ProjectId):
        return browser.analysis(request.state.identity, project_id)

    @app.post("/api/projects/{project_id}/analysis", status_code=202)
    def reanalyze(request: Request, project_id: ProjectId, body: AnalysisBody):
        environment = body.authorEnvironment.model_dump() if body.authorEnvironment else None
        return browser.reanalyze(request.state.identity, project_id, body.options, environment)

    @app.get("/api/projects/{project_id}/files")
    def file_content(request: Request, project_id: ProjectId, path: str):
        return browser.file(request.state.identity, project_id, path)

    @app.get("/api/projects/{project_id}/decisions")
    def decisions(request: Request, project_id: ProjectId):
        return browser.decisions(request.state.identity, project_id)

    @app.put("/api/projects/{project_id}/decisions")
    def save(request: Request, project_id: ProjectId, body: SaveBody):
        return browser.save_decisions(
            request.state.identity, project_id, body.decisions, body.expectedRevision
        )

    @app.post("/api/projects/{project_id}/plan")
    def plan(request: Request, project_id: ProjectId, body: dict | None = None):
        return browser.plan(request.state.identity, project_id, body)

    @app.get("/api/projects/{project_id}/preparation")
    def preparation(request: Request, project_id: ProjectId):
        return browser.preparation(request.state.identity, project_id)

    @app.get("/api/projects/{project_id}/graph")
    def graph(request: Request, project_id: ProjectId):
        return browser.forward(request.state.identity, project_id, "graph")

    @app.post("/api/projects/{project_id}/resolve")
    def resolve(request: Request, project_id: ProjectId, body: ResolveBody):
        target = ResolutionTargetDTO.model_validate(
            {
                "python_version": body.target.pythonVersion,
                "platform": body.target.platform,
            }
        )
        return browser.resolve(
            request.state.identity,
            project_id,
            target.python_version,
            target.platform,
            body.decisions,
        )

    @app.get("/api/projects/{project_id}/lock")
    def lock(request: Request, project_id: ProjectId):
        return browser.forward(request.state.identity, project_id, "lock")

    @app.post("/api/projects/{project_id}/advice")
    def advice(request: Request, project_id: ProjectId, body: AdviceBody):
        return browser.forward(
            request.state.identity,
            project_id,
            "project_advice",
            feature=body.feature,
            description=body.description,
            share_source=True,
        )

    @app.post("/api/projects/{project_id}/archive")
    def build_archive(request: Request, project_id: ProjectId, body: ArchiveBody | None = None):
        options = body or ArchiveBody()
        return browser.build(
            request.state.identity, project_id, options.includeDockerfile, options.lockId
        )

    @app.get("/api/projects/{project_id}/archive")
    def download(request: Request, project_id: ProjectId):
        return Response(
            browser.download(request.state.identity, project_id),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="reproducible-project.zip"'},
        )

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete(request: Request, project_id: ProjectId):
        browser.delete(request.state.identity, project_id)
        return Response(status_code=204)

    if static_dir is not None:
        root = static_dir.resolve(strict=True)
        index = (root / "index.html").resolve(strict=True)
        if not root.is_dir() or not index.is_file() or not index.is_relative_to(root):
            raise ValueError("Static directory must contain the built application index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path == "api" or path.startswith("api/") or "\\" in path or ".." in path.split("/"):
                return Response(status_code=404)
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                return Response(status_code=404)
            if not candidate.is_file():
                if "." in FilePath(path).name:
                    return Response(status_code=404)
                candidate = index
            return FileResponse(
                candidate,
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": (
                        "default-src 'self'; script-src 'self'; "
                        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                        "font-src 'self' https://fonts.gstatic.com; "
                        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                        "base-uri 'self'; form-action 'self'"
                    ),
                    "Referrer-Policy": "same-origin",
                },
            )

    return app
