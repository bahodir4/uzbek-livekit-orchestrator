"""Operational CLI: edit runtime provider selection without editing Python code."""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from .configuration import active_spec, credential, load, save_atomic, validate


def redact(data: dict) -> dict:
    copy_data = copy.deepcopy(data)
    for category, providers in copy_data.get("providers", {}).items():
        for name, spec in providers.items():
            env_name = spec.get("api_key_env", "")
            spec["credentials_configured"] = bool(os.environ.get(env_name)) if env_name else False
    return copy_data


def set_active(category: str, name: str) -> None:
    config = load()
    if name not in config["providers"][category]:
        raise ValueError(f"Unknown {category} provider {name}. Add it first.")
    config["active"][category] = name
    save_atomic(config)
    print(f"Active {category} set to {name}. Takes effect for NEW sessions.")


def add_provider(category: str, name: str, kind: str, url: str, api_key_env: str,
                 model: str | None = None):
    config = load()
    config["providers"][category][name] = {
        "type": kind, "url": url, "api_key_env": api_key_env,
    }
    if model:
        config["providers"][category][name]["model"] = model
    # Temporarily select new provider for type/url validation; restore previous selection.
    previous = config["active"][category]
    config["active"][category] = name
    validate(config)
    config["active"][category] = previous
    save_atomic(config)
    print(f"Added {category}.{name}; use 'provider use {category} {name}' to activate")


PROVIDER_FIELDS = {
    "stt": {"url", "api_key_env", "timeout_seconds"},
    "llm": {"url", "api_key_env", "model", "temperature", "max_completion_tokens"},
    "tts": {"url", "api_key_env", "voice", "language", "task_type", "sample_rate", "max_new_tokens", "max_chars_per_request", "timeout_seconds", "max_audio_seconds", "seed", "trim_edge_silence", "silence_threshold", "streaming_mode"},
}


def set_provider_field(category: str, name: str, field: str, raw_value: str) -> None:
    if field not in PROVIDER_FIELDS[category]:
        raise ValueError(f"Unsupported field for {category}: {field}")
    data = load()
    if name not in data["providers"][category]:
        raise ValueError(f"Unknown provider: {category}.{name}")
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        value = raw_value  # URLs/model names are frequently bare strings.
    if field in {"url", "model", "api_key_env", "voice", "language", "task_type", "streaming_mode"} and not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    data["providers"][category][name][field] = value
    save_atomic(data)
    print(f"Updated {category}.{name}.{field}; applies to new sessions only.")


def tune(path: str, raw_value: str):
    # Only safe config sections are mutable via the terminal.
    if not path.startswith(("turn.", "vad.", "capacity.", "agent.", "knowledge.")):
        raise ValueError("Only turn.*, vad.*, capacity.*, agent.*, knowledge.* supported by 'tune'")
    config = load()
    parts = path.split(".")
    target = config
    for part in parts[:-1]:
        if part not in target or not isinstance(target[part], dict):
            raise ValueError(f"Unknown field: {path}")
        target = target[part]
    if parts[-1] not in target:
        raise ValueError(f"Unknown field: {path}")
    value = json.loads(raw_value)
    target[parts[-1]] = value
    save_atomic(config)
    print(f"Set {path}={json.dumps(value)}. New sessions read updated values.")


async def doctor() -> int:
    import aiohttp
    config = load()
    failed = False
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as client:
        for category in ("stt", "llm", "tts"):
            spec = active_spec(config, category)
            base = spec["url"].rstrip("/")
            if category == "stt":
                url = base + "/health/ready"
            elif category == "llm":
                url = base + "/models"
            else:
                url = base + "/health"
            headers = {}
            key = credential(spec)
            if key:
                headers["Authorization"] = "Bearer " + key
            try:
                async with client.get(url, headers=headers) as response:
                    print(f"{category:3} {spec['type']}: HTTP {response.status} ({url})")
                    if response.status >= 500:
                        failed = True
            except Exception as exc:
                print(f"{category:3} {spec['type']}: connection failed ({type(exc).__name__})")
                failed = True
    print("Note: doctor probes reachability, not audio quality or latency.")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="voice-config", description="Uzbek LiveKit orchestrator config CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("validate")
    sub.add_parser("doctor")
    provider = sub.add_parser("provider")
    psub = provider.add_subparsers(dest="operation", required=True)
    use = psub.add_parser("use")
    use.add_argument("category", choices=("stt", "llm", "tts"))
    use.add_argument("name")
    add = psub.add_parser("add")
    add.add_argument("category", choices=("stt", "llm", "tts"))
    add.add_argument("name")
    add.add_argument("--type", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--api-key-env", required=True)
    add.add_argument("--model")
    pset = psub.add_parser("set")
    pset.add_argument("category", choices=("stt", "llm", "tts"))
    pset.add_argument("name")
    pset.add_argument("field")
    pset.add_argument("value")
    tune_cmd = sub.add_parser("tune")
    tune_cmd.add_argument("path")
    tune_cmd.add_argument("json_value", help='JSON scalar, e.g. 0.3 or true or "vad"')
    args = parser.parse_args()
    try:
        if args.command == "show":
            print(json.dumps(redact(load()), indent=2, ensure_ascii=False))
        elif args.command == "validate":
            load()
            print("Configuration valid")
        elif args.command == "doctor":
            raise SystemExit(asyncio.run(doctor()))
        elif args.command == "provider" and args.operation == "use":
            set_active(args.category, args.name)
        elif args.command == "provider" and args.operation == "add":
            add_provider(args.category, args.name, args.type, args.url, args.api_key_env, args.model)
        elif args.command == "provider" and args.operation == "set":
            set_provider_field(args.category, args.name, args.field, args.value)
        elif args.command == "tune":
            tune(args.path, args.json_value)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Configuration error: {exc}\n")


if __name__ == "__main__":
    main()
