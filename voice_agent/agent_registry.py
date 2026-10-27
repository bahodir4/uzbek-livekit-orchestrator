"""Loads and validates everything about the agents from the agents/ folder.

Prompts, greetings, tool descriptions, spoken messages and business rules are data files, not code:
  agents/index.yaml        agent order, default agent, legacy ids, locale lines
  agents/<id>.yaml         one file per agent (name, role, greeting, tasks, tools, prompt)
  agents/shared_rules.md   rules appended to every agent prompt
  agents/tools.yaml        what the model is told about each tool
  agents/messages.yaml     everything the tools say back
  agents/settings.yaml     business rules (hours, capacity, limits, document lists)
Set AGENTS_DIR to use another folder. A broken file fails fast with a message naming the file and key.
"""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class AgentConfigError(RuntimeError):
    """A file in the agents folder is missing or invalid."""


def agents_dir() -> Path:
    return Path(os.getenv("AGENTS_DIR") or Path(__file__).resolve().parent.parent / "agents")


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    role: str
    voice_gender: str
    greeting: str
    tasks: tuple[str, ...]
    tools: tuple[str, ...]
    instructions: str


@dataclass(frozen=True)
class Registry:
    default: str
    agents: dict[str, AgentSpec]
    legacy_aliases: dict[str, str]
    locale_instructions: dict[str, str]
    guard_instruction: str
    tool_descriptions: dict[str, str]
    messages: dict[str, Any]
    settings: dict[str, Any]
    phrases: dict[str, list[str]]

    def text(self, path: str, **values: Any) -> str:
        """Message by dotted path, e.g. text('results.loan_annuity', monthly=...)."""
        node: Any = self.messages
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise AgentConfigError(f"messages.yaml has no '{path}'")
            node = node[part]
        if not isinstance(node, str):
            raise AgentConfigError(f"messages.yaml '{path}' is not text")
        try:
            return node.format(**values)
        except (KeyError, IndexError) as exc:
            raise AgentConfigError(f"messages.yaml '{path}' uses unknown placeholder {exc}") from exc


def _read(directory: Path, name: str) -> Any:
    path = directory / name
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) if name.endswith((".yaml", ".yml")) \
            else path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AgentConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise AgentConfigError(f"{path} is not valid YAML: {exc}") from exc


def _need(data: Any, key: str, kind: type, where: str):
    if not isinstance(data, dict) or not isinstance(data.get(key), kind) or data.get(key) in ("", [], {}):
        raise AgentConfigError(f"{where}: '{key}' is missing or not a {kind.__name__}")
    return data[key]


def _hhmm(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def work_hours_text(settings: dict, messages: dict) -> str:
    """e.g. 'dushanba-juma 09:00-17:00, 13:00-14:00 tanaffus', built from settings.yaml + messages.yaml."""
    cfg = settings["appointments"]
    names = messages["weekdays"]
    days = sorted(cfg["work_days"])
    if days == list(range(days[0], days[-1] + 1)) and len(days) > 1:
        label = f"{names[days[0]]}-{names[days[-1]]}"
    else:
        label = ", ".join(names[d] for d in days)
    breaks = "".join(f", {a}-{b} {messages['labels']['break']}" for a, b in cfg.get("breaks", []))
    return f"{label} {cfg['day_start']}-{cfg['day_end']}{breaks}"


def load(directory: Path | None = None) -> Registry:
    root = Path(directory) if directory else agents_dir()
    index = _read(root, "index.yaml")
    order = _need(index, "agents", list, "index.yaml")
    default = _need(index, "default", str, "index.yaml")
    if default not in order:
        raise AgentConfigError(f"index.yaml: default agent '{default}' is not in 'agents'")
    messages = _read(root, "messages.yaml")
    settings = _read(root, "settings.yaml")
    for key in ("results", "errors", "validation", "fields", "weekdays", "labels", "money"):
        if not isinstance(messages, dict) or key not in messages:
            raise AgentConfigError(f"messages.yaml: '{key}' is missing")
    for key in ("limits", "appointments", "records", "tickets", "loan_documents", "timezone_offset_hours"):
        if not isinstance(settings, dict) or key not in settings:
            raise AgentConfigError(f"settings.yaml: '{key}' is missing")

    values_common = {"work_hours": work_hours_text(settings, messages)}
    tools = _read(root, "tools.yaml")
    tool_descriptions: dict[str, str] = {}
    for name, body in (tools or {}).items():
        description = _need(body, "description", str, f"tools.yaml[{name}]")
        try:
            tool_descriptions[name] = " ".join(description.format(**values_common).split())
        except KeyError as exc:
            raise AgentConfigError(f"tools.yaml[{name}] uses unknown placeholder {exc}") from exc

    agents: dict[str, AgentSpec] = {}
    for agent_id in order:
        where = f"{agent_id}.yaml"
        data = _read(root, where)
        if _need(data, "id", str, where) != agent_id:
            raise AgentConfigError(f"{where}: 'id' must be '{agent_id}'")
        gender = _need(data, "voice_gender", str, where)
        if gender not in ("female", "male"):
            raise AgentConfigError(f"{where}: voice_gender must be female or male")
        agent_tools = tuple(_need(data, "tools", list, where))
        for tool in agent_tools:
            if tool not in tool_descriptions:
                raise AgentConfigError(f"{where}: tool '{tool}' has no entry in tools.yaml")
        values = {**values_common, "name": _need(data, "name", str, where), "role": _need(data, "role", str, where)}
        try:
            greeting = _need(data, "greeting", str, where).format(**values)
            prompt = _need(data, "prompt", str, where).format(**values)
        except KeyError as exc:
            raise AgentConfigError(f"{where}: unknown placeholder {exc}") from exc
        agents[agent_id] = AgentSpec(id=agent_id, name=values["name"], role=values["role"], voice_gender=gender,
                                     greeting=greeting, tasks=tuple(_need(data, "tasks", list, where)),
                                     tools=agent_tools, instructions=prompt.strip())

    directory_lines = "\n".join(f"- {a.name}: {a.role} ({'; '.join(a.tasks)})" for a in agents.values())
    phrases = {}
    if (root / "phrases.yaml").exists():
        raw = _read(root, "phrases.yaml") or {}
        phrases = {k: [str(x).strip() for x in v if str(x).strip()] for k, v in raw.items() if isinstance(v, list)}
    fillers_text = "; ".join(f'"{x}"' for x in phrases.get("fillers", [])) or '"Bir soniya."'
    shared = _read(root, "shared_rules.md").format(directory=directory_lines, fillers=fillers_text).strip()
    agents = {k: AgentSpec(**{**a.__dict__, "instructions": f"{a.instructions}\n{shared}"}) for k, a in agents.items()}

    aliases = index.get("legacy_aliases") or {}
    for old, new in aliases.items():
        if new not in agents:
            raise AgentConfigError(f"index.yaml: legacy alias '{old}' points to unknown agent '{new}'")
    locales = _need(index, "locale_instructions", dict, "index.yaml")
    return Registry(default=default, agents=agents, legacy_aliases=dict(aliases), locale_instructions=locales,
                    guard_instruction=_need(index, "guard_instruction", str, "index.yaml"),
                    tool_descriptions=tool_descriptions, messages=messages, settings=settings, phrases=phrases)


@functools.lru_cache(maxsize=1)
def get() -> Registry:
    return load()
