"""Format d'erreur unique de l'écosystème (DiddiFreeID_Contrat_API.md §0).

    {"error": {"code": "...", "message": "...", "details": null}}
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Erreur métier portant son propre code HTTP et son code applicatif."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    message: str = "Erreur serveur."

    def __init__(
        self,
        message: str | None = None,
        details: Any = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


# --- Erreurs transverses -----------------------------------------------------


class Unauthenticated(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    message = "Token absent ou invalide."


class TokenExpired(AppError):
    # DiddiFreeID_Contrat_API.md §2 : c'est au frontend d'appeler POST /auth/refresh.
    status_code = 401
    code = "TOKEN_EXPIRED"
    message = "Token expiré."


class StepUpProofInvalid(AppError):
    status_code = 403
    code = "STEP_UP_PROOF_INVALID"
    message = "Preuve de ré-authentification invalide."


class StepUpProofExpired(AppError):
    status_code = 410
    code = "STEP_UP_PROOF_EXPIRED"
    message = "La preuve de ré-authentification a expiré."


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "Non autorisé."


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Ressource inexistante."


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Conflit d'état."


class UnprocessableEntity(AppError):
    status_code = 422
    code = "UNPROCESSABLE_ENTITY"
    message = "Validation de champs échouée."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "Validation de champs échouée.",
                [
                    {"field": ".".join(str(p) for p in e["loc"][1:]), "reason": e["msg"]}
                    for e in exc.errors()
                ],
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)
            ),
        )
