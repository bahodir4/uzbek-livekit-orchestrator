"""Single source of truth for provider selection and validation.

Secrets are sourced from environment variables, not agent.yaml. A fresh snapshot
is loaded for each LiveKit job: CLI changes affect new calls; existing calls are pinned.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
from typing import Any
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "agent.yaml"
SUPPORTED = {
    "stt": {"gigaam_ws"},
    "llm": {"openai_compatible"},
    "tts": {"sayro_http", "dual_gateway_stream"},
}


def config_path() -> Path:
    raw = os.environ.get("AGENT_CONFIG_PATH", "")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_CONFIG


def safe_config(data: dict[str, Any]) -> dict[str, Any]:
    """Remove credential values from accidentally hand-authored config files."""
    result = copy.deepcopy(data)
    for category in SUPPORTED:
        for spec in result.get("providers", {}).get(category, {}).values():
            if isinstance(spec, dict):
                for key in ("api_key", "token", "password", "secret"):
                    spec.pop(key, None)
    return result


PLACEHOLDER_MARKERS = ("_POD_ID", "YOUR-")   # copied from the docs without filling in the real pod id


def validate(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Expected agent configuration version: 1")
    for category, kinds in SUPPORTED.items():
        selected = data.get("active", {}).get(category)
        spec = data.get("providers", {}).get(category, {}).get(selected)
        if not selected or not isinstance(spec, dict):
            raise ValueError(f"Missing active {category} provider: {selected}")
        if spec.get("type") not in kinds:
            raise ValueError(f"Unsupported {category} type: {spec.get('type')}")
        if not str(spec.get("url", "")).startswith(("http://", "https://")):
            raise ValueError(f"{category}.{selected} url must be http(s)")
        if any(marker in str(spec["url"]) for marker in PLACEHOLDER_MARKERS):
            raise ValueError(f"{category}.{selected} url still contains a placeholder: {spec['url']}")
        if not spec.get("api_key_env"):
            raise ValueError(f"{category}.{selected} missing api_key_env")
    turn = data.get("turn", {})
    if turn.get("mode") not in ("stt", "vad"):
        raise ValueError("turn.mode must be stt or vad; audio turn detector not validated for Uzbek")
    if turn.get("interruption_mode") not in ("vad",):
        raise ValueError("For this Uzbek baseline, interruption_mode must be vad")
    low, high = float(turn.get("endpointing_min_delay", 0.15)), float(turn.get("endpointing_max_delay", 2))
    if not 0 <= low <= high <= 10:
        raise ValueError("Invalid endpointing delays")
    limit = int(data.get("capacity", {}).get("max_concurrent_jobs", 10))
    if not 1 <= limit <= 1000:
        raise ValueError("max_concurrent_jobs must be 1..1000")
    for name, tts_spec in data.get("providers", {}).get("tts", {}).items():
        mode = tts_spec.get("streaming_mode", "sentence_http")
        if tts_spec.get("type") == "dual_gateway_stream":
            if mode != "pcm_http":
                raise ValueError("Dual gateway supports verified pcm_http streaming only")
            if tts_spec.get("model", "sayro") not in ("sayro", "navoiy"):
                raise ValueError("Dual TTS model must be sayro or navoiy")
            if int(tts_spec.get("sample_rate", 24000)) != 24000:
                raise ValueError("Dual gateway requires 24000 Hz PCM")
            if not 32 <= int(tts_spec.get("max_chars_per_request", 180)) <= 350:
                raise ValueError("Gateway max_chars_per_request must be 32..350")
        elif tts_spec.get("type") == "sayro_http":
            if mode != "sentence_http":
                raise ValueError("Legacy Sayro requires sentence_http")
            if not 32 <= int(tts_spec.get("max_chars_per_request", 115)) <= 400:
                raise ValueError("Legacy Sayro max_chars_per_request must be 32..400")
        else:
            raise ValueError(f"Unsupported tts.{name} type")
    for category in SUPPORTED:
        for name, spec in data["providers"][category].items():
            if any(k in spec for k in ("api_key", "token", "password", "secret")):
                raise ValueError(f"Do not store secrets in YAML ({category}.{name}); use api_key_env")
    return data


def active_spec(data: dict[str, Any], category: str) -> dict[str, Any]:
    return data["providers"][category][data["active"][category]]


def load(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    validate(data)
    # The gateway is an INDEPENDENT deployment. An env URL override avoids
    # rebuilding or modifying its source and keeps the image portable.
    gateway_url = os.environ.get("TTS_GATEWAY_URL", "").strip()
    if gateway_url:
        if not gateway_url.startswith(("http://", "https://")):
            raise ValueError("TTS_GATEWAY_URL must use http:// or https://")
        if "dual_gateway" in data.get("providers", {}).get("tts", {}):
            data["providers"]["tts"]["dual_gateway"]["url"] = gateway_url.rstrip("/")
    return data


def resolve_project_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def credential(spec: dict[str, Any], *, required: bool = False) -> str:
    key_name = spec["api_key_env"]
    value = os.environ.get(key_name, "")
    if required and not value:
        raise ValueError(f"Missing required environment variable: {key_name}")
    return value


def save_atomic(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or config_path()
    validate(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=".agent-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            yaml.safe_dump(data, file, sort_keys=False, allow_unicode=True)
            file.flush()
            os.fsync(file.fileno())
        # mkstemp creates 0600; the containers run as another uid and must still read the file
        os.chmod(temp_path, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
