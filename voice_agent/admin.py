"""Password-protected operational API and Swagger UI.

This API is deliberately separate from the real-time worker. Run it behind TLS.
It has no endpoint that executes user-submitted Python or arbitrary HTTP URLs.
"""
from __future__ import annotations
import copy
import os
import secrets
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .configuration import config_path, load, save_atomic
from .cli import redact

security = HTTPBasic(auto_error=True)
app = FastAPI(title="Uzbek Voice Agent Control API", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url=None)


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    expected_user = os.getenv("ADMIN_USERNAME", "")
    expected_pass = os.getenv("ADMIN_PASSWORD", "")
    if not expected_user or len(expected_pass) < 16 or expected_pass == "CHANGE_TO_LONG_RANDOM_PASSWORD":
        raise HTTPException(status_code=503, detail="Admin credentials not configured securely")
    valid = secrets.compare_digest(credentials.username.encode(), expected_user.encode()) and \
            secrets.compare_digest(credentials.password.encode(), expected_pass.encode())
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin credentials",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/health/live", include_in_schema=False)
async def live():
    return {"status": "alive"}


@app.get("/docs", include_in_schema=False)
async def swagger(_: str = Depends(require_admin)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title="Voice Agent Admin — Swagger")


@app.get("/openapi.json", include_in_schema=False)
async def openapi(_: str = Depends(require_admin)):
    return JSONResponse(app.openapi())


@app.get("/admin/config")
async def get_config(_: str = Depends(require_admin)):
    return redact(load())


@app.get("/admin/status")
async def status_info(_: str = Depends(require_admin)):
    config = load()
    return {"active": config["active"], "capacity": config["capacity"],
            "note": "Worker job counts come from the LiveKit worker, not this admin API."}


class ProviderChoice(BaseModel):
    category: str = Field(pattern="^(stt|llm|tts)$")
    provider: str = Field(min_length=1, max_length=100)


@app.post("/admin/providers/activate")
async def activate(choice: ProviderChoice, _: str = Depends(require_admin)):
    data = load()
    if choice.provider not in data["providers"][choice.category]:
        raise HTTPException(400, "Unknown provider")
    data["active"][choice.category] = choice.provider
    save_atomic(data)
    return {"active": data["active"], "applies_to": "new_sessions_only"}
