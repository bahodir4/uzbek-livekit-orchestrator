# Uzbek LiveKit Voice Orchestrator (prototype)

A **separate** LiveKit Agents Python worker connecting your existing GigaAM STT WebSocket service, OpenAI-compatible **vLLM** server, and **Sayro vLLM-Omni** HTTP TTS. Contains a password-protected FastAPI admin + Swagger UI, YAML configuration, terminal configuration CLI, local read-only knowledge tools, and Docker Compose.

**Status:** Source built and 14 local tests passed (configuration, auth, audio helpers and knowledge retrieval) at packaging time. The complete AgentSession / GPU / remote LiveKit / Sayro pipeline has **not** been executed here; validate in your environment before production use. Python 3.11 recommended; LiveKit SDK version `~=1.8.0` is targeted. Source checked against official LiveKit docs dated October 2026.

## Structure

```text
voice_agent/
  worker.py              LiveKit AgentServer + per-room AgentSession
  providers.py           finite STT/LLM/TTS registry
  adapters/gigaam.py     GigaAM websocket SpeechStream -> LiveKit SpeechEvent
  adapters/sayro.py      HTTP WAV phrase synthesis -> LiveKit AudioEmitter
  audio_utils.py         WAV decode, silence trim, phrase splitting
  configuration.py       validated YAML snapshots, safe atomic saves
  cli.py                 dynamic terminal configuration
  knowledge.py           bounded, read-only keyword search
  admin.py               HTTP Basic admin API + protected Swagger
config/agent.yaml        non-secret runtime config
knowledge/system_prompt.txt
knowledge/docs/         Markdown/Text reference documents
scripts/dispatch_room.py
Dockerfile
  docker-compose.yml
```

## How media streams

```text
Caller microphone -> LiveKit RTC room
                   -> GigaAM STT WebSocket (16kHz PCM16)
                   -> interim/final transcripts
                   -> LiveKit AgentSession endpointing + Silero VAD
                   -> local vLLM /v1/chat/completions (streaming)
                   -> LiveKit StreamAdapter (incremental completed phrases)
                   -> Sayro POST /v1/audio/speech (WAV per phrase)
                   -> LiveKit audio output (interruptible)
```

**Important limitation:** The Sayro interface verified in your prior RunPod tests returns a **complete WAV** after each HTTP request. It is **not native audio-token streaming**. LiveKit receives LLM token streams and sends each completed sentence (or a bounded subphrase) as soon as possible. Sayro must finish synthesizing that phrase before any audio from it can play. Native Sayro streaming can be added as another TTS provider when its actual stream protocol has been verified. Don't set `streaming_mode` to arbitrary values.

GigaAM simulates streaming by periodically redecoding a growing utterance. Interim transcripts may change; only final transcripts should be treated as committed. Its server already performs VAD segmentation. LiveKit Silero VAD runs locally for barge-in; **STT endpointing delay + LiveKit endpointing delay** can be additive. Start with `turn.mode=stt`, `endpointing_min_delay=0.18`, and tune from measurements.

## First-time setup (direct Python / RunPod)

```bash
cd /workspace
# Upload or git clone this project to /workspace/uzbek-livekit-orchestrator
cd uzbek-livekit-orchestrator
python3 --version           # Python 3.11 recommended
bash scripts/install_runpod_no_docker.sh
cp .env.example .env
nano .env
nano config/agent.yaml
source .venv/bin/activate
python -m voice_agent.cli validate
python -m voice_agent.cli doctor
bash scripts/run_local.sh dev
```

Set `LIVEKIT_URL=wss://...`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `STT_API_KEY` (the **rotated** key from your GigaAM pod), `VLLM_API_KEY` (can be `EMPTY` for vLLM installations without auth), and optional `SAYRO_API_KEY`. Set `ADMIN_USERNAME` and a distinct random `ADMIN_PASSWORD` of at least 16 characters. Do not commit `.env` or share it in logs.

Edit `config/agent.yaml` URLs and vLLM **exact model ID**. STT API URL example `https://YOUR_STT_POD-8000.proxy.runpod.net`, LLM `http://YOUR_VLLM:8000/v1`, Sayro `http://YOUR_TTS:8000` (without `/v1/audio/speech`). Both ports/servers must be reachable **from the orchestration machine**; `127.0.0.1` in Docker points to the container itself.

**Production:** Run `bash scripts/run_local.sh start` instead of `dev`. The server registers a named agent. It **will not automatically join every room**; dispatch the agent explicitly:

```bash
# In another terminal, with .env environment loaded:
set -a; source .env; set +a
source .venv/bin/activate
python scripts/dispatch_room.py --room demo-uzbek-room --agent-name uzbek-voice-agent
```

A browser/telephone client must also join that LiveKit room with its **own proper LiveKit join token**. The worker handles media after LiveKit dispatch; this repository does not include a public unprotected token-minting route. For local testing you can use LiveKit's Agents Playground or your existing LiveKit frontend.

## Docker Compose

```bash
cp .env.example .env
nano .env
nano config/agent.yaml
docker compose build
docker compose up -d
docker compose logs -f agent
```

The `agent` process connects **outbound** to your LiveKit server and models. A GPU is **not** needed on this orchestration host when STT, LLM and TTS run on remote GPU pods. The separate `admin` service is bound to host loopback at port 8090. The agent's health server is bound to loopback port 8081. Admin data in `config/` must be writable by container UID 10001; on Linux run `sudo chown -R 10001:10001 config` if you want to use admin config changes, or use the CLI on the host instead. Make backups before granting write access.

**Important:** Because this is a persistent LiveKit worker, do not deploy it on scale-to-zero serverless HTTP inference without an appropriate always-on worker lifecycle. RunPod CPU/GPU Pod or ordinary VM is suitable; Docker can run there if permitted.

## Terminal configuration — dynamic switching

Run in the project root with your venv activated (or `docker compose exec agent python -m voice_agent.cli ...` for read-only commands):

```bash
# Print safe config (only boolean credential-configured flags, never secret values)
python -m voice_agent.cli show
python -m voice_agent.cli validate
python -m voice_agent.cli doctor

# Add another vLLM instance; never put secrets in YAML
python -m voice_agent.cli provider add llm backup --type openai_compatible \
  --url https://OTHER_LLM_DOMAIN/v1 --api-key-env BACKUP_LLM_KEY --model MY_OTHER_MODEL

# Change the active LLM for NEW calls
python -m voice_agent.cli provider use llm backup
python -m voice_agent.cli provider use stt gigaam_primary
python -m voice_agent.cli provider use tts sayro_primary
# Update a provider URL / model / voice / token limit without touching Python:
python -m voice_agent.cli provider set llm local_vllm model "YOUR_NEW_MODEL_ID"
python -m voice_agent.cli provider set llm local_vllm url "http://YOUR_SERVER:8000/v1"
python -m voice_agent.cli provider set tts sayro_primary max_new_tokens 160
python -m voice_agent.cli provider set tts sayro_primary voice "sayro"

# Tune turn-taking without editing Python (JSON values required)
python -m voice_agent.cli tune turn.endpointing_min_delay 0.25
python -m voice_agent.cli tune turn.interruption_min_duration 0.4
python -m voice_agent.cli tune agent.auto_greet false
python -m voice_agent.cli tune knowledge.enabled_tools '["search_knowledge","current_date"]'
```

To add STT: `provider add stt backup --type gigaam_ws --url https://... --api-key-env ALT_STT_API_KEY`. To add TTS: `provider add tts backup --type sayro_http --url https://... --api-key-env ALT_SAYRO_API_KEY`. **Currently implemented types:** `gigaam_ws`, `openai_compatible`, `sayro_http`. Registry rejects unimplemented types rather than pretending any vendor protocol is interoperable. Adding a genuinely different STT/TTS interface requires writing its adapter and registering the type.

The agent loads the YAML snapshot at each new **room job**. **Existing calls are pinned** to their original provider instances; this avoids switching models mid-utterance. Updating credentials (`.env`), `max_concurrent_jobs`, dispatch name or prewarmed Silero parameters requires restarting the worker. `provider add/use` and basic endpoint timing changes take effect for new sessions immediately, assuming mounted YAML is shared across workers. In Docker, run configuration-changing commands on the **host** because the worker's config mount is read-only, or use the admin API.

## Protected admin Swagger + API

Run without Docker:

```bash
source .venv/bin/activate
set -a; source .env; set +a
uvicorn voice_agent.admin:app --host 127.0.0.1 --port 8090
```

Browse to `http://127.0.0.1:8090/docs` and log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`. **`/docs`, `/openapi.json`, `/admin/config`, `/admin/status`, `/admin/providers/activate` all enforce HTTP Basic.** Only `/health/live` is public. Expose admin to other computers only behind HTTPS plus firewall/VPN; HTTP Basic must never be used over public plain HTTP. Docker Compose defaults to loopback-bound admin.

`POST /admin/providers/activate` body: `{"category":"llm","provider":"backup"}`. New calls pick the selection; existing calls are not forcibly migrated.

## Add verified knowledge

Place `.md` or `.txt` files in `knowledge/docs/` and update `knowledge/system_prompt.txt` if needed. The `search_knowledge` tool performs local paragraph keyword retrieval and includes filenames in the result. It is **not embeddings-based RAG** and may miss semantically similar wording. `current_date` is a separate safe tool. For a production knowledge system, add a vetted retrieval provider with tenant-aware authorization and citations. **Do not put personal secrets in the knowledge directory.**

## Latency and concurrent call tuning

1. STT: tune GigaAM backend `PARTIAL_INTERVAL_MS` and `END_SILENCE_MS` while avoiding too many repeated inference requests. For STT end-of-utterance, LiveKit waits **in addition** to the GigaAM VAD delay.
2. LLM: run OpenAI-compatible vLLM with SSE streaming enabled, a low enough maximum output token count, and tool-calling/chat-template support if tools are enabled. Test that your model emits valid tool calls; not all vLLM checkpoints do.
3. TTS: `max_chars_per_request=115`, short Uzbek sentences, `max_new_tokens=180`, cached HTTP connection. Listen to spoken output; a trimmed WAV can still contain hallucinated speech.
4. Interruptions: `mode=vad`, min duration ~0.35s. LiveKit cancels playback and streams when interrupted. Provider-side computation might not stop instantaneously; budget for that.
5. Concurrency: worker `max_concurrent_jobs=10` is an **admission limit**, not a guaranteed GPU capacity. GigaAM/TTS/vLLM each have their own independent queues. Benchmark p50/p95 time-to-first-audio, final STT latency, error rate, GPU OOM and abandoned jobs at 1/5/10 sessions. Load cap can be tuned only after measurements.
6. Security: keep API keys out of URLs, logs, browser clients, knowledge files, and YAML. Rotate previously exposed credentials; do not run public model endpoints without auth/TLS.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q voice_agent scripts
# Integration tests (requires live credentials):
python -m voice_agent.cli doctor
```

These unit tests do not exercise the live LiveKit SDK, real audio devices, GPU output or provider streams. Full end-to-end acceptance: (1) dispatch agent, (2) verify STT partial/final, (3) vLLM begins streaming, (4) Sayro responds phrase-by-phrase, (5) barge-in cancels audible playback, (6) tool lookup retrieves correct knowledge, (7) five simultaneous rooms remain responsive and independent.

## Official LiveKit references used

- https://docs.livekit.io/agents/logic/sessions/
- https://docs.livekit.io/reference/agents/turn-handling-options/
- https://docs.livekit.io/agents/models/llm/openai-compatible-llms/
- https://docs.livekit.io/reference/python/livekit/agents/tts/index.html
- https://docs.livekit.io/agents/server/agent-dispatch/
- https://docs.livekit.io/agents/server/options/
- https://docs.livekit.io/agents/logic/tools/definition/
