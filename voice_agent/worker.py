"""One independent LiveKit AgentSession per room job.

LiveKit owns media transport, audio buffering, dialogue turn-taking and barge-in.
Providers are resolved at job start, so new conversations see config edits.
"""
from __future__ import annotations

import datetime
import logging
import json
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(override=False)

from livekit.agents import Agent, AgentServer, AgentSession, JobContext, JobProcess, TurnHandlingOptions, cli, function_tool, RunContext, room_io
from livekit.plugins import silero

from .configuration import load, resolve_project_path
from .knowledge import LocalKnowledgeStore
from .providers import build_llm, build_stt, build_tts

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

startup_config = load()
max_jobs = int(startup_config["capacity"]["max_concurrent_jobs"])
server = AgentServer(load_threshold=0.9)


def compute_load(server_instance: AgentServer) -> float:
    return min(len(server_instance.active_jobs) / max_jobs, 1.0)


server.load_fnc = compute_load


def prewarm(proc: JobProcess) -> None:
    config = load()
    vad = config.get("vad", {})
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=float(vad.get("min_speech_duration", 0.09)),
        min_silence_duration=float(vad.get("min_silence_duration", 0.48)),
        prefix_padding_duration=float(vad.get("prefix_padding_duration", 0.25)),
    )


server.setup_fnc = prewarm


def build_tools(config: dict):
    knowledge = config.get("knowledge", {})
    allowed = set(knowledge.get("enabled_tools", []))
    result = []

    if "search_knowledge" in allowed:
        store = LocalKnowledgeStore(
            resolve_project_path(knowledge.get("directory", "knowledge/docs")),
            max_results=int(knowledge.get("max_results", 3)),
            max_chars_per_result=int(knowledge.get("max_chars_per_result", 1100)),
            max_files=int(knowledge.get("max_files", 100)),
        )

        @function_tool()
        async def search_knowledge(context: RunContext, question: str) -> str:
            """Search verified local company knowledge for the user's question; use for policies and company facts."""
            return await store.search(question)

        result.append(search_knowledge)

    if "current_date" in allowed:
        @function_tool()
        async def current_date(context: RunContext, timezone_name: str = "Asia/Tashkent") -> str:
            """Return the current date/time in a valid time zone; use for time-sensitive requests."""
            try:
                local = datetime.datetime.now(ZoneInfo(timezone_name))
            except (KeyError, ValueError):
                return "Unknown time zone; use Asia/Tashkent."
            return local.isoformat(timespec="seconds")

        result.append(current_date)
    return result


@server.rtc_session(agent_name=startup_config["agent"]["name"])
async def entrypoint(ctx: JobContext) -> None:
    config = load()  # dynamic provider selection for each new session
    turn = config.get("turn", {})
    stt_provider = build_stt(config)
    llm_provider = build_llm(config)
    # Dispatch metadata is set by our authenticated room-creation API. The
    # selected TTS model is pinned to this session, not changed on the fly.
    override = None
    try:
        metadata = json.loads(getattr(ctx.job, "metadata", "") or "{}")
        if isinstance(metadata, dict):
            candidate = metadata.get("tts_model")
            if candidate in ("sayro", "navoiy"):
                override = candidate
    except (ValueError, TypeError):
        log.warning("Ignoring malformed dispatch metadata")
    tts_provider = build_tts(config, model_override=override)
    instructions_file = resolve_project_path(config["agent"]["instructions_file"])
    instructions = instructions_file.read_text(encoding="utf-8")
    agent = Agent(instructions=instructions, tools=build_tools(config))
    session = AgentSession(
        stt=stt_provider, llm=llm_provider, tts=tts_provider,
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(
            turn_detection=turn.get("mode", "stt"),
            endpointing={
                "min_delay": float(turn.get("endpointing_min_delay", 0.18)),
                "max_delay": float(turn.get("endpointing_max_delay", 1.6)),
            },
            interruption={
                "enabled": bool(turn.get("interruption_enabled", True)),
                "mode": turn.get("interruption_mode", "vad"),
                "min_duration": float(turn.get("interruption_min_duration", 0.35)),
            },
            preemptive_generation={"enabled": bool(turn.get("preemptive_generation", False))},
        ),
    )
    # Per-provider metrics, unlike deprecated session-level metrics event.
    def attach_metrics(stage: str, provider) -> None:
        @provider.on("metrics_collected")
        def on_metrics(event) -> None:
            fields = ("ttfb", "ttft", "duration", "audio_duration", "inference_duration")
            selected = {key: getattr(event, key) for key in fields if getattr(event, key, None) is not None}
            if selected:
                log.info("Voice metric room=%s stage=%s %s", ctx.room.name, stage, selected)

    for stage, provider in (("stt", stt_provider), ("llm", llm_provider), ("tts", tts_provider)):
        attach_metrics(stage, provider)

    log.info("Starting voice session room=%s stt=%s llm=%s tts=%s",
             ctx.room.name, *(config["active"][name] for name in ("stt", "llm", "tts")))
    try:
        await session.start(
            room=ctx.room, agent=agent,
            room_options=room_io.RoomOptions(text_input=True, text_output=True),
        )
        if config["agent"].get("auto_greet", True):
            await session.say(config["agent"].get("greeting", "Assalomu alaykum!"), allow_interruptions=True)
    except Exception:
        log.exception("LiveKit room session failed")
        raise


if __name__ == "__main__":
    cli.run_app(server)
