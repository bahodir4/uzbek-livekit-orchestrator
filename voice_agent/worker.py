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

from livekit.agents import Agent, AgentServer, AgentSession, ChatContext, JobContext, JobProcess, TurnHandlingOptions, cli, function_tool, RunContext, room_io
from livekit.plugins import silero

from .configuration import load, resolve_project_path
from .knowledge import LocalKnowledgeStore
from .providers import build_llm, build_stt, build_tts
from .experience import agent_instructions, canonical_persona, DEFAULT_PERSONA, PERSONAS, LANGUAGES

log = logging.getLogger(__name__)
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

startup_config = load()
max_jobs = int(startup_config["capacity"]["max_concurrent_jobs"])
server = AgentServer(load_threshold=0.9)


def compute_load(server_instance: AgentServer) -> float:
    return min(len(server_instance.active_jobs) / max_jobs, 1.0)


server.load_fnc = compute_load


def prewarm(proc: JobProcess) -> None:
    # Import heavy modules here, once per job process, instead of on the first turn: these
    # imports blocked the event loop for 0.4-0.9s and could time out the first job assignment.
    import numpy.fft  # noqa: F401  (used by LiveKit's speaking-rate estimator)
    import openai.lib.streaming.chat  # noqa: F401  (first chat.completions call)
    from livekit.plugins import openai as _openai  # noqa: F401  (realtime/types pydantic models)
    config = load()
    vad = config.get("vad", {})
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=float(vad.get("min_speech_duration", 0.09)),
        min_silence_duration=float(vad.get("min_silence_duration", 0.48)),
        prefix_padding_duration=float(vad.get("prefix_padding_duration", 0.25)),
    )


server.setup_fnc = prewarm


def build_tools(config: dict, persona: str = DEFAULT_PERSONA):
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
    try:
        from .agent_tools import tools_for
        result.extend(tools_for(canonical_persona(persona)))
    except Exception:  # a tool-registration problem must never stop the session from starting
        log.exception("Could not register agent tools for %s", persona)
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
    persona = DEFAULT_PERSONA
    language = 'uz'
    text_only = False
    speak_responses = True
    previous_messages = []
    try:
        metadata = json.loads(getattr(ctx.job, "metadata", "") or "{}")
        if isinstance(metadata, dict):
            persona = canonical_persona(metadata.get('persona'))
            language = metadata.get('language') if metadata.get('language') in LANGUAGES else 'uz'
            text_only = metadata.get('text_only') is True
            speak_responses = metadata.get('speak_responses') is not False
            previous_messages = metadata.get('previous_messages') if isinstance(metadata.get('previous_messages'), list) else []
            candidate = metadata.get("tts_model")
            if candidate in ("sayro", "navoiy"):
                override = candidate
    except (ValueError, TypeError):
        log.warning("Ignoring malformed dispatch metadata")
    tts_provider = build_tts(config, model_override=override)
    instructions_file = resolve_project_path(config["agent"]["instructions_file"])
    instructions = agent_instructions(persona, language, instructions_file.read_text(encoding="utf-8"))
    # Construct a real LiveKit ChatContext; past user utterances never become
    # system instructions. See docs.livekit.io/agents/logic/chat-context/.
    initial_ctx = ChatContext()
    for m in previous_messages[:12]:
        if isinstance(m, dict) and m.get('role') in ('user','agent') and isinstance(m.get('text'), str):
            initial_ctx.add_message(role='assistant' if m['role']=='agent' else 'user', content=m['text'][:400])
    agent_tools = build_tools(config, persona)
    log.info("Agent %s ready: tools=%s text_only=%s", persona,
             [getattr(t, 'info', None).name if getattr(t, 'info', None) else getattr(t, '__name__', '?') for t in agent_tools],
             text_only)
    agent = Agent(instructions=instructions, chat_ctx=initial_ctx, tools=agent_tools)
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
    # The UI interrupt control travels as a small LiveKit data packet.
    # Commands are scoped to this room; text streams remain on lk.chat.
    @ctx.room.on('data_received')
    def on_ui_data(packet):
        try:
            event = json.loads(packet.data.decode('utf-8'))
            if isinstance(event, dict) and event.get('type') == 'neurotech.interrupt':
                session.interrupt()
        except (ValueError, UnicodeDecodeError, RuntimeError):
            log.debug('Ignoring unknown UI data packet')

    try:
        await session.start(
            room=ctx.room, agent=agent,
            room_options=room_io.RoomOptions(text_input=True, text_output=True,
                audio_input=not text_only, audio_output=speak_responses and not text_only),
        )
        if config["agent"].get("auto_greet", True) and not text_only:
            await session.say(PERSONAS[persona]["greeting"], allow_interruptions=True)
    except Exception:
        log.exception("LiveKit room session failed")
        raise


if __name__ == "__main__":
    cli.run_app(server)
