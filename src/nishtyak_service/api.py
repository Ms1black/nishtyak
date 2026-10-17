from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from typing import Annotated, Any

from fastapi import FastAPI, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from nishtyak_core.advisors import ProjectAdvisoryResult
from nishtyak_core.advisors.models import AdvisoryResult
from nishtyak_core.locking import DependencyLock, ResolutionTarget
from nishtyak_core.models import PackagePlan

from .config import Settings
from .dto import (
    AdviceDTO,
    AnalysisOptionsDTO,
    AuthorEnvironmentDTO,
    BundleDTO,
    ClarificationsDTO,
    DecisionDocumentDTO,
    DecisionsDTO,
    ErrorDTO,
    ImportGraphDTO,
    JobDTO,
    PreparationDTO,
    ProjectAdviceDTO,
    ProjectReportDTO,
    ReanalyzeDTO,
    ResolveDTO,
    SaveDecisionsDTO,
    SubmittedProjectDTO,
)
from .errors import ServiceError
from .middleware import RequestBoundaryMiddleware
from .service import LogicService

ProjectId = Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")]
ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    status: {"model": ErrorDTO}
    for status in (400, 401, 404, 408, 409, 413, 422, 429, 500, 503, 507)
}
for _retry_status in (429, 503):
    ERROR_RESPONSES[_retry_status]["headers"] = {
        "Retry-After": {"schema": {"type": "string"}, "description": "Retry delay in seconds"}
    }


def create_app(settings: Settings, service: LogicService | None = None) -> FastAPI:
    token = settings.api_token
    if not isinstance(token, str) or not 32 <= len(token) <= 512 or not token.isascii():
        raise ValueError("A service API token of 32 to 512 ASCII characters is required")
    logic = service if service is not None else LogicService(settings)

    @asynccontextmanager
    async def lifespan(app):
        async def review_projects():
            while True:
                try:
                    await run_in_threadpool(logic.review_pending)
                except Exception:
                    # Static analysis stays available when optional enrichment fails.
                    pass
                await asyncio.sleep(0.25)

        tasks = (
            [asyncio.create_task(review_projects()) for _ in range(settings.max_running)]
            if (settings.automatic_review or settings.automatic_resolution)
            and hasattr(logic, "review_pending")
            else []
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(
        title="Nishtyak logic service",
        version="1.2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.logic = logic
    app.add_middleware(
        RequestBoundaryMiddleware,
        api_token=token,
        max_archive_bytes=settings.max_archive_bytes,
        max_options_bytes=settings.max_options_bytes,
        json_request_bytes=settings.json_request_bytes,
        max_http_requests=settings.max_http_requests,
    )

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError) -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if error.status in (429, 503):
            headers["Retry-After"] = "2"
        payload = {"code": error.code, "message": error.message}
        if error.field is not None:
            payload["field"] = error.field
        return JSONResponse(payload, error.status, headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        location = error.errors()[0].get("loc", ()) if error.errors() else ()
        field = ".".join(str(part) for part in location if part != "body")
        return JSONResponse(
            {
                "code": "INVALID_REQUEST",
                "message": "Request does not match the API schema",
                "field": field or None,
            },
            422,
            {"Cache-Control": "no-store"},
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        return JSONResponse(
            {"code": "INTERNAL_ERROR", "message": "Internal service error"},
            500,
            {"Cache-Control": "no-store"},
        )

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def ready() -> JSONResponse:
        is_ready = await run_in_threadpool(logic.ready)
        return JSONResponse(
            {"status": "ready" if is_ready else "unavailable"},
            200 if is_ready else 503,
            {"Cache-Control": "no-store", **({"Retry-After": "2"} if not is_ready else {})},
        )

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi() -> JSONResponse:
        return JSONResponse(app.openapi(), headers={"Cache-Control": "no-store"})

    @app.get("/v1/capabilities")
    def capabilities() -> dict:
        return logic.capabilities()

    @app.post(
        "/v1/projects",
        status_code=202,
        response_model=SubmittedProjectDTO,
        responses=ERROR_RESPONSES,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "multipart/form-data": {
                        "schema": {
                            "type": "object",
                            "required": ["file"],
                            "additionalProperties": False,
                            "properties": {
                                "file": {"type": "string", "format": "binary"},
                                "options": {
                                    "type": "string",
                                    "description": "AnalysisOptions JSON",
                                    "maxLength": settings.max_options_bytes,
                                    "contentMediaType": "application/json",
                                    "contentSchema": {
                                        "$ref": "#/components/schemas/AnalysisOptionsDTO"
                                    },
                                },
                                "author_environment": {
                                    "type": "string",
                                    "description": (
                                        "Author's local Python/package metadata JSON; "
                                        "at most 256 KiB"
                                    ),
                                    "maxLength": 262144,
                                    "contentMediaType": "application/json",
                                    "contentSchema": {
                                        "$ref": "#/components/schemas/AuthorEnvironmentDTO"
                                    },
                                },
                            },
                        }
                    }
                },
            }
        },
    )
    async def submit(request: Request) -> dict[str, Any]:
        try:
            async with request.form(max_files=1, max_fields=2, max_part_size=262144) as form:
                if set(form) - {"file", "options", "author_environment"} or any(
                    len(form.getlist(name)) != 1 for name in form
                ):
                    raise ServiceError(
                        422, "INVALID_UPLOAD", "Expected one file and optional options"
                    )
                upload = form.get("file")
                raw_options = form.get("options", "{}")
                if not isinstance(upload, UploadFile) or not isinstance(raw_options, str):
                    raise ServiceError(
                        422, "INVALID_UPLOAD", "Expected one file and optional options"
                    )
                if len(raw_options.encode("utf-8")) > settings.max_options_bytes:
                    raise ServiceError(413, "OPTIONS_TOO_LARGE", "Analysis options too large")
                requested_options = AnalysisOptionsDTO.model_validate_json(raw_options)
                options = requested_options.to_core()
                raw_environment = form.get("author_environment")
                author_environment = None
                if raw_environment is not None:
                    if not isinstance(raw_environment, str):
                        raise ServiceError(
                            422,
                            "AUTHOR_ENVIRONMENT_INVALID",
                            "Author metadata must be JSON text",
                            "author_environment",
                        )
                    if len(raw_environment.encode("utf-8")) > 262144:
                        raise ServiceError(
                            413,
                            "AUTHOR_ENVIRONMENT_LIMIT",
                            "Author metadata exceeds 256 KiB",
                            "author_environment",
                        )
                    try:
                        author_environment = AuthorEnvironmentDTO.model_validate_json(
                            raw_environment
                        ).to_core()
                    except ValueError:
                        raise ServiceError(
                            422,
                            "AUTHOR_ENVIRONMENT_INVALID",
                            "Invalid author environment metadata",
                            "author_environment",
                        ) from None
                    if "python_version" not in requested_options.model_fields_set:
                        options = replace(
                            options,
                            python_version=".".join(
                                author_environment.python_version.split(".")[:2]
                            ),
                        )
                archive = await upload.read(settings.max_archive_bytes + 1)
                if len(archive) > settings.max_archive_bytes:
                    raise ServiceError(413, "ARCHIVE_TOO_LARGE", "Archive too large")
        except ValidationError as error:
            raise ServiceError(422, "INVALID_OPTIONS", "Invalid analysis options") from error
        except HTTPException as error:
            raise ServiceError(400, "INVALID_MULTIPART", "Invalid multipart upload") from error
        if author_environment is None:
            return await run_in_threadpool(logic.submit, request.state.owner_id, archive, options)
        return await run_in_threadpool(
            logic.submit, request.state.owner_id, archive, options, author_environment
        )

    @app.get("/v1/jobs/{job_id}", response_model=JobDTO, responses=ERROR_RESPONSES)
    async def get_job(request: Request, job_id: ProjectId) -> dict[str, Any]:
        return await run_in_threadpool(logic.get_job, request.state.owner_id, job_id)

    @app.get(
        "/v1/projects/{project_id}", response_model=ProjectReportDTO, responses=ERROR_RESPONSES
    )
    async def get_project(request: Request, project_id: ProjectId) -> dict[str, Any]:
        return await run_in_threadpool(logic.get_project, request.state.owner_id, project_id)

    @app.delete("/v1/projects/{project_id}", status_code=204, responses=ERROR_RESPONSES)
    async def delete_project(request: Request, project_id: ProjectId) -> Response:
        await run_in_threadpool(logic.delete, request.state.owner_id, project_id)
        return Response(status_code=204)

    @app.post(
        "/v1/projects/{project_id}/analysis",
        status_code=202,
        response_model=SubmittedProjectDTO,
        responses=ERROR_RESPONSES,
    )
    async def reanalyze(
        request: Request, project_id: ProjectId, body: ReanalyzeDTO
    ) -> dict[str, Any]:
        try:
            environment = body.author_environment.to_core() if body.author_environment else None
        except ValueError:
            raise ServiceError(
                422,
                "AUTHOR_ENVIRONMENT_INVALID",
                "Invalid author environment metadata",
                "author_environment",
            ) from None
        return await run_in_threadpool(
            logic.reanalyze, request.state.owner_id, project_id, body.options.to_core(), environment
        )

    @app.get(
        "/v1/projects/{project_id}/decisions",
        response_model=DecisionDocumentDTO,
        responses=ERROR_RESPONSES,
    )
    async def get_decisions(request: Request, project_id: ProjectId) -> dict[str, Any]:
        return await run_in_threadpool(logic.get_decisions, request.state.owner_id, project_id)

    @app.put(
        "/v1/projects/{project_id}/decisions",
        response_model=DecisionDocumentDTO,
        responses=ERROR_RESPONSES,
    )
    async def save_decisions(
        request: Request, project_id: ProjectId, body: SaveDecisionsDTO
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            logic.save_decisions,
            request.state.owner_id,
            project_id,
            body.decisions.to_core(),
            body.expected_revision,
        )

    @app.post(
        "/v1/projects/{project_id}/clarifications",
        response_model=DecisionDocumentDTO,
        responses=ERROR_RESPONSES,
    )
    async def submit_clarifications(
        request: Request, project_id: ProjectId, body: ClarificationsDTO
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            logic.submit_clarifications,
            request.state.owner_id,
            project_id,
            body.decisions.to_core(),
            body.expected_revision,
            body.questionnaire_id,
            body.notes,
        )

    @app.post(
        "/v1/projects/{project_id}/plan", response_model=PackagePlan, responses=ERROR_RESPONSES
    )
    async def plan(
        request: Request, project_id: ProjectId, decisions: DecisionsDTO | None = None
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            logic.plan,
            request.state.owner_id,
            project_id,
            decisions.to_core() if decisions else None,
        )

    @app.get(
        "/v1/projects/{project_id}/preparation",
        response_model=PreparationDTO,
        responses=ERROR_RESPONSES,
    )
    async def preparation(request: Request, project_id: ProjectId) -> dict[str, Any]:
        return await run_in_threadpool(logic.preparation, request.state.owner_id, project_id)

    @app.get(
        "/v1/projects/{project_id}/graph", response_model=ImportGraphDTO, responses=ERROR_RESPONSES
    )
    async def graph(request: Request, project_id: ProjectId) -> dict[str, Any]:
        return await run_in_threadpool(logic.graph, request.state.owner_id, project_id)

    @app.post(
        "/v1/projects/{project_id}/project-advice",
        response_model=ProjectAdvisoryResult,
        responses=ERROR_RESPONSES,
    )
    async def project_advice(
        request: Request, project_id: ProjectId, body: ProjectAdviceDTO
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            logic.project_advice, request.state.owner_id, project_id, body.feature, body.description
        )

    @app.post(
        "/v1/projects/{project_id}/resolve",
        response_model=DependencyLock,
        responses=ERROR_RESPONSES,
    )
    async def resolve(request: Request, project_id: ProjectId, body: ResolveDTO) -> dict[str, Any]:
        return await run_in_threadpool(
            logic.resolve,
            request.state.owner_id,
            project_id,
            ResolutionTarget(**body.target.model_dump()),
            body.decisions.to_core() if body.decisions else None,
        )

    @app.get(
        "/v1/projects/{project_id}/lock", response_model=DependencyLock, responses=ERROR_RESPONSES
    )
    async def get_lock(request: Request, project_id: ProjectId) -> dict[str, Any]:
        return await run_in_threadpool(logic.get_lock, request.state.owner_id, project_id)

    @app.post(
        "/v1/projects/{project_id}/bundle",
        response_class=Response,
        responses={
            200: {
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}
            },
            **ERROR_RESPONSES,
        },
    )
    async def bundle(request: Request, project_id: ProjectId, body: BundleDTO) -> Response:
        lock_options = {"lock_id": body.lock_id} if body.lock_id is not None else {}
        content = await run_in_threadpool(
            logic.bundle,
            request.state.owner_id,
            project_id,
            body.decisions.to_core() if body.decisions else None,
            body.include_dockerfile,
            **lock_options,
        )
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="reproducible-project.zip"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post(
        "/v1/projects/{project_id}/advice", response_model=AdvisoryResult, responses=ERROR_RESPONSES
    )
    async def advice(request: Request, project_id: ProjectId, body: AdviceDTO) -> dict[str, Any]:
        return await run_in_threadpool(
            logic.advice, request.state.owner_id, project_id, body.feature, body.description
        )

    def build_schema() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            schema.setdefault("components", {})["securitySchemes"] = {
                "ServiceToken": {"type": "http", "scheme": "bearer"}
            }
            schema["components"]["schemas"]["AnalysisOptionsDTO"] = (
                AnalysisOptionsDTO.model_json_schema()
            )
            for path, operations in schema["paths"].items():
                for operation in operations.values():
                    operation["security"] = [{"ServiceToken": []}]
                    if path.startswith("/v1/"):
                        operation.setdefault("parameters", []).append(
                            {
                                "name": "X-Owner-ID",
                                "in": "header",
                                "required": True,
                                "schema": {
                                    "type": "string",
                                    "pattern": r"^[A-Za-z0-9_.:-]{1,128}$",
                                },
                            }
                        )
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = build_schema
    return app
