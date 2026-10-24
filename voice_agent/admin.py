"""Password-protected operational API and Swagger UI.

This API is deliberately separate from the real-time worker. Run it behind TLS.
It has no endpoint that executes user-submitted Python or arbitrary HTTP URLs.
"""
from __future__ import annotations
import os
import secrets
import time
import asyncio
import json
import logging
import uuid
from typing import Literal
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, status, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .configuration import load, save_atomic, active_spec, credential, validate
from .cli import redact
import httpx

security = HTTPBasic(auto_error=False)
_sessions: dict[str, float] = {}
SESSION_COOKIE = "voice_console_session"
SESSION_SECONDS = 8 * 3600
log = logging.getLogger(__name__)
app = FastAPI(title="Uzbek Voice Agent Control API", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url=None)


def require_admin(request: Request, credentials: HTTPBasicCredentials | None = Depends(security)) -> str:
    token = request.cookies.get(SESSION_COOKIE, "")
    if token and _sessions.get(token, 0) > time.time():
        return "console-admin"
    if credentials is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})
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


def mutation_auth(request: Request, user: str = Depends(require_admin)) -> str:
    """Browser cookie writes require custom header; cross-origin forms cannot add it."""
    if request.cookies.get(SESSION_COOKIE) in _sessions:
        if request.headers.get("X-Voice-UI") != "1":
            raise HTTPException(403, "Missing UI CSRF header")
    return user

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
async def activate(choice: ProviderChoice, _: str = Depends(mutation_auth)):
    data = load()
    if choice.provider not in data["providers"][choice.category]:
        raise HTTPException(400, "Unknown provider")
    data["active"][choice.category] = choice.provider
    save_atomic(data)
    return {"active": data["active"], "applies_to": "new_sessions_only"}

# --- NEUROTECH operator console (same-origin UI). ---
# The UI uses short-lived, HttpOnly sessions; backend API keys never reach JS.
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


@app.post("/api/login")
async def login(body: LoginRequest, response: Response):
    expected_user = os.getenv("ADMIN_USERNAME", "")
    expected_pass = os.getenv("ADMIN_PASSWORD", "")
    if not expected_user or len(expected_pass) < 16 or expected_pass == "CHANGE_TO_LONG_RANDOM_PASSWORD":
        raise HTTPException(503, "Configure secure admin credentials first")
    good_user = secrets.compare_digest(body.username.encode(), expected_user.encode())
    good_pass = secrets.compare_digest(body.password.encode(), expected_pass.encode())
    if not good_user or not good_pass:
        raise HTTPException(401, "Invalid username or password")
    # Single-process in-memory sessions; admin Docker service deliberately runs one worker.
    now = time.time()
    for token, exp in list(_sessions.items()):
        if exp <= now:
            del _sessions[token]
    token = secrets.token_urlsafe(32)
    _sessions[token] = now + SESSION_SECONDS
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                        secure=os.getenv("UI_SECURE_COOKIE", "true").lower() != "false",
                        samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True, "username": expected_user}


@app.post("/api/logout")
async def logout(request: Request, response: Response, _: str = Depends(mutation_auth)):
    _sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}


@app.get("/api/session")
async def session_info(request: Request):
    tok = request.cookies.get(SESSION_COOKIE, "")
    return {"authenticated": bool(tok and _sessions.get(tok, 0) > time.time())}


@app.get("/api/config")
async def ui_config(_: str = Depends(require_admin)):
    return redact(load())


class UISettings(BaseModel):
    auto_greet: bool | None = None
    greeting: str | None = Field(default=None, min_length=1, max_length=240)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_completion_tokens: int | None = Field(default=None, ge=16, le=4096)
    endpointing_min_delay: float | None = Field(default=None, ge=0, le=10)
    endpointing_max_delay: float | None = Field(default=None, ge=0, le=10)
    interruption_enabled: bool | None = None
    interruption_min_duration: float | None = Field(default=None, ge=0, le=5)
    preemptive_generation: bool | None = None
    tts_model: Literal["sayro", "navoiy"] | None = None
    tts_emotion: str | None = Field(default=None, min_length=1, max_length=48, pattern=r"^[a-zA-Z_]+$")
    tts_max_chars_per_request: int | None = Field(default=None, ge=32, le=350)


@app.patch("/api/settings")
async def update_settings(body: UISettings, _: str = Depends(mutation_auth)):
    data = load()
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    mapping = {
        "auto_greet": ("agent", "auto_greet"),
        "greeting": ("agent", "greeting"),
        "temperature": ("providers", "llm", data["active"]["llm"], "temperature"),
        "max_completion_tokens": ("providers", "llm", data["active"]["llm"], "max_completion_tokens"),
        "endpointing_min_delay": ("turn", "endpointing_min_delay"),
        "endpointing_max_delay": ("turn", "endpointing_max_delay"),
        "interruption_enabled": ("turn", "interruption_enabled"),
        "interruption_min_duration": ("turn", "interruption_min_duration"),
        "preemptive_generation": ("turn", "preemptive_generation"),
        "tts_model": ("providers", "tts", "dual_gateway", "model"),
        "tts_emotion": ("providers", "tts", "dual_gateway", "emotion"),
        "tts_max_chars_per_request": ("providers", "tts", "dual_gateway", "max_chars_per_request"),
    }
    for key, value in fields.items():
        target = data
        for segment in mapping[key][:-1]:
            target = target[segment]
        target[mapping[key][-1]] = value
    try:
        validate(data)
    except (ValueError, KeyError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    save_atomic(data)
    return {"config": redact(data), "applies_to": "new_sessions_only"}


class ActiveProvider(BaseModel):
    category: Literal["stt", "llm", "tts"]
    provider: str = Field(min_length=1, max_length=100)


@app.post("/api/providers/activate")
async def ui_activate(body: ActiveProvider, _: str = Depends(mutation_auth)):
    data = load()
    if body.provider not in data["providers"][body.category]:
        raise HTTPException(400, "Unknown provider")
    data["active"][body.category] = body.provider
    try:
        save_atomic(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"active": data["active"], "applies_to": "new_sessions_only"}


async def _ping(client: httpx.AsyncClient, name: str, url: str, key: str) -> dict:
    try:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        result = await client.get(url, headers=headers)
        data = result.json() if result.headers.get("content-type", "").startswith("application/json") else {}
        online = result.status_code == 200
        if name == "tts" and isinstance(data, dict) and "sayro" in data and "navoiy" in data:
            online = online and any(data.get(m, {}).get("reachable") for m in ("sayro", "navoiy"))
        return {"state": "online" if online else "error", "status": result.status_code,
                "details": data if name == "tts" and isinstance(data, dict) else None}
    except (httpx.HTTPError, ValueError) as exc:
        return {"state": "offline", "error": type(exc).__name__}


@app.get("/api/overview")
async def overview(_: str = Depends(require_admin)):
    data = load()
    checks = {}
    async with httpx.AsyncClient(timeout=httpx.Timeout(4.0), follow_redirects=False) as client:
        coroutines = []
        for stage in ("stt", "llm", "tts"):
            spec = active_spec(data, stage)
            suffix = "/health/ready" if stage in ("stt", "tts") and spec["type"] != "sayro_http" else ("/models" if stage == "llm" else "/health")
            coroutines.append(_ping(client, stage, spec["url"].rstrip("/") + suffix, credential(spec)))
        results = await asyncio.gather(*coroutines)
        checks = dict(zip(("stt", "llm", "tts"), results))
    return {"services": checks, "active": data["active"], "agent": data["agent"]["name"],
            "tts_models": ["sayro", "navoiy"], "note": "Checks indicate reachability, not verified GPU audio streaming."}


@app.get("/api/capabilities")
async def capabilities(_: str = Depends(require_admin)):
    """Authoritative interface contract for the operator UI (not a readiness claim)."""
    config = load()
    dual = config.get("providers", {}).get("tts", {}).get("dual_gateway", {})
    return {
        "voice_call": True,
        "text_chat": {"enabled": True, "topic": "lk.chat", "transport": "livekit_text_stream"},
        "transcription": {"enabled": True, "topic": "lk.transcription", "transport": "livekit_text_stream"},
        "agent_state": {"source": "participant_attribute", "key": "lk.agent.state"},
        "tts": {"enabled": dual.get("type") == "dual_gateway_stream",
                "models": ["sayro", "navoiy"], "format": "pcm_s16le",
                "sample_rate": 24000, "channels": 1, "preview_max_chars": 350},
        "settings_scope": "new_sessions_only",
        "per_room_model_selection": active_spec(config, "tts")["type"] == "dual_gateway_stream",
    }


class PreviewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=350)
    model: Literal["sayro", "navoiy"] = "sayro"
    emotion: str = Field(default="calm", min_length=1, max_length=48)


@app.post("/api/tts/preview")
async def preview(body: PreviewRequest, _: str = Depends(mutation_auth)):
    """Authenticated PCM passthrough; browser receives chunks at the same time as LiveKit."""
    config = load()
    spec = config["providers"]["tts"].get("dual_gateway")
    if not spec or spec["type"] != "dual_gateway_stream":
        raise HTTPException(503, "Dual TTS gateway is not configured")
    api_key = credential(spec)
    if not api_key:
        raise HTTPException(503, "TTS_API_KEY is not configured")
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=120, write=10, pool=10),
                               follow_redirects=False)
    try:
        request = client.build_request("POST", spec["url"].rstrip("/") + "/v1/tts/stream",
                                       headers={"Authorization": f"Bearer {api_key}", "Accept": "application/octet-stream"},
                                       json={"text": body.text, "model": body.model, "emotion": body.emotion})
        upstream = await client.send(request, stream=True)
        if upstream.status_code != 200:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(502, "TTS gateway rejected the synthesis request")
        if (upstream.headers.get("X-Audio-Sample-Rate") != "24000" or
            upstream.headers.get("X-Audio-Format") != "pcm_s16le" or
            upstream.headers.get("X-Audio-Channels") != "1" or
            upstream.headers.get("content-type", "").split(";")[0] != "application/octet-stream"):
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(502, "TTS gateway returned invalid audio format")
        iterator = upstream.aiter_bytes(chunk_size=3840)
        first = await anext(iterator, b"")
        if not first:
            await upstream.aclose()
            await client.aclose()
            raise HTTPException(502, "TTS gateway returned empty audio")
    except HTTPException:
        raise
    except (httpx.HTTPError, OSError) as exc:
        if 'upstream' in locals():
            await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, f"TTS gateway unavailable ({type(exc).__name__})") from exc

    async def audio():
        total = 0
        partial = b""
        try:
            # First prefetched packet must be delivered immediately; do not wait
            # for the second packet or stream closure before streaming begins.
            async def chunks():
                yield first
                async for chunk in iterator:
                    yield chunk
            async for chunk in chunks():
                partial += chunk
                n = len(partial) & ~1
                if n:
                    total += n
                    if total > 24000 * 2 * 35:
                        raise ValueError("Preview exceeded 35 seconds")
                    yield partial[:n]
                    partial = partial[n:]
            if partial:
                raise ValueError("Gateway sent an odd PCM byte")
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(audio(), media_type="application/octet-stream", headers={
        "X-Audio-Format": "pcm_s16le", "X-Audio-Sample-Rate": "24000",
        "X-Audio-Channels": "1", "Cache-Control": "no-store",
        "X-TTS-Model": body.model,
    })


class RoomRequest(BaseModel):
    model: Literal["sayro", "navoiy"] = "sayro"


@app.post("/api/rooms")
async def create_room(body: RoomRequest, _: str = Depends(mutation_auth)):
    config = load()
    if active_spec(config, "tts")["type"] != "dual_gateway_stream":
        raise HTTPException(409, "Select dual_gateway as the active TTS provider for per-call routing")
    from livekit import api
    url = os.getenv("LIVEKIT_URL", "")
    key = os.getenv("LIVEKIT_API_KEY", "")
    secret = os.getenv("LIVEKIT_API_SECRET", "")
    if not url or not key or not secret or any(s.startswith("YOUR_") for s in (url, key, secret)):
        raise HTTPException(503, "LiveKit credentials are not configured")
    room_name = "studio-" + uuid.uuid4().hex[:20]
    try:
        async with api.LiveKitAPI(url=url, api_key=key, api_secret=secret) as lk:
            await lk.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
                room=room_name, agent_name=config["agent"]["name"],
                metadata=json.dumps({"tts_model": body.model}),
            ))
        token = (api.AccessToken(key, secret)
                 .with_identity("operator-" + uuid.uuid4().hex[:16])
                 .with_name("NEUROTECH Studio")
                 .with_grants(api.VideoGrants(room_join=True, room=room_name,
                                              can_publish=True, can_subscribe=True,
                                              can_publish_data=True, can_update_own_metadata=False))
                 .with_ttl(__import__("datetime").timedelta(minutes=30)).to_jwt())
    except Exception as exc:
        log.exception("LiveKit dispatch failed")
        raise HTTPException(502, f"LiveKit dispatch failed ({type(exc).__name__})") from exc
    return {"room": room_name, "url": url, "token": token, "model": body.model}
