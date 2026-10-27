# 04 — Connect all services to the LiveKit Orchestrator

**Host:** CPU VM/Pod with Docker Compose or Python 3.11 environment. **No GPU needed** if STT, LLM and TTS are hosted on other GPUs. Each model server must be running and independently reachable before starting a LiveKit call.

## A. Install and configure

Extract `uzbek-livekit-voice-agent-orchestrator.zip` or clone your matching source into the orchestration host:

```bash
cd /workspace/uzbek-livekit-orchestrator
cp -n .env.example .env
nano .env
nano config/agent.yaml
```

**`.env` secrets (examples are placeholders):**

```ini
LIVEKIT_URL=wss://YOUR_LIVEKIT_HOST
LIVEKIT_API_KEY=YOUR_LIVEKIT_API_KEY
LIVEKIT_API_SECRET=YOUR_LIVEKIT_API_SECRET
STT_API_KEY=ROTATED_STT_SERVICE_KEY
VLLM_API_KEY=LLM_SERVICE_KEY
TTS_API_KEY=TTS_GATEWAY_KEY            # same value as TTS_API_KEY on the TTS pod
ADMIN_USERNAME=admin
ADMIN_PASSWORD=YOUR_LONG_UNIQUE_ADMIN_PASSWORD
```

**`config/agent.yaml` provider fields (edit the corresponding entries; preserve the rest of the YAML):**

```yaml
active:
  stt: gigaam_primary
  llm: local_vllm
  tts: dual_gateway
providers:
  stt:
    gigaam_primary:
      type: gigaam_ws
      url: https://STT_POD_ID-8000.proxy.runpod.net
      api_key_env: STT_API_KEY
  llm:
    local_vllm:
      type: openai_compatible
      url: https://LLM_POD_ID-8000.proxy.runpod.net/v1
      api_key_env: VLLM_API_KEY
      model: google/gemma-4-31B-it
      temperature: 0.35
      max_completion_tokens: 200
  tts:
    dual_gateway:
      type: dual_gateway_stream
      url: https://TTS_POD_ID-8000.proxy.runpod.net
      api_key_env: TTS_API_KEY
      model: navoiy
      voice: navoiy
      emotion: warm                # calm, warm, gentle, happy, excited, sad, angry, surprised
      greeting_emotion: warm
      phrase_cache: true           # repeated short phrases are played from disk, no TTS delay
      sample_rate: 24000
      max_chars_per_request: 340
      timeout_seconds: 120
      max_audio_seconds: 35
      prefetch_phrases: 2          # pieces requested ahead of playback
      input_streaming: true        # false = old sentence-by-sentence path (fallback)
      streaming_mode: pcm_http
      transport: http              # 'ws' is faster in the benchmark but crashed a job process once (exit -11): keep http until investigated
```

**Important:** This is a **configuration excerpt**, not a replacement for the complete `config/agent.yaml`; replacing the entire file with the excerpt would drop required sections (`agent`, `turn`, `vad`, `capacity`, `knowledge`). Use `python -m voice_agent.cli provider set ...` commands instead if preferred.

## B. Start via Docker Compose

```bash
docker compose build
docker compose up -d
docker compose logs -f worker api
```

The `admin` service exposes protected Swagger only at `127.0.0.1:8090/docs` on the orchestrator host; do **not** publicly expose basic-auth admin over HTTP. Use an SSH tunnel/VPN if accessing from a different computer. Docker containers cannot access a different host's `127.0.0.1`; configure reachable service URLs.

**Run without Docker** (if Docker is unavailable):

```bash
bash scripts/install_runpod_no_docker.sh
source .venv/bin/activate
python -m voice_agent.cli validate
python -m voice_agent.cli doctor
bash scripts/run_local.sh dev
```

## C. Join a LiveKit room

On the orchestrator host with the Python environment and `.env` variables loaded securely:

```bash
set -a; source .env; set +a
source .venv/bin/activate
python scripts/dispatch_room.py --room demo-uzbek-room --agent-name uzbek-voice-agent
```

Connect your LiveKit client or Agents Playground to the same room with a **separate participant token**. The orchestrator worker should receive GigaAM interim/final events, send final user text to Gemma (streaming SSE), stream each short phrase to the Navoiy TTS gateway, and play the returned audio to LiveKit.

## D. Change models from terminal

```bash
source .venv/bin/activate
python -m voice_agent.cli show
python -m voice_agent.cli provider set llm local_vllm model 'google/gemma-4-31B-it'
python -m voice_agent.cli provider set llm local_vllm url 'https://LLM_POD_ID-8000.proxy.runpod.net/v1'
python -m voice_agent.cli provider set stt gigaam_primary url 'https://STT_POD_ID-8000.proxy.runpod.net'
python -m voice_agent.cli provider set tts dual_gateway url 'https://TTS_POD_ID-8000.proxy.runpod.net'
python -m voice_agent.cli tune turn.endpointing_min_delay 0.25
python -m voice_agent.cli validate
```

CLI changes persist in `config/agent.yaml`. New rooms use updated provider selections; active calls retain their original provider instances. Environment-variable/credential changes require a worker restart.

## E. Voice-agent acceptance checklist

1. `/health/ready` on STT is true; `/v1/models` on the LLM and `/health/ready` on the TTS gateway respond with their keys (see `00_QUICKSTART.md`); all three can be called from the orchestration host.
2. Navoiy returns audible Uzbek for **short** prompts; the voice is the intended (female) one; no long trailing silence or unrelated speech.
3. LiveKit can dispatch the agent into a real room and STT produces final transcripts.
4. Gemma emits SSE content chunks early and responds in Uzbek. The agents' tools (`06_AGENTS.md`) work, which needs vLLM started with `--enable-auto-tool-choice --tool-call-parser gemma4` (doc 02). Otherwise the worker log shows `400 "auto" tool choice requires ...`.
5. Caller interrupts TTS; playback stops and queued synthesis results are ignored.
6. Run `python scripts/bench_latency.py` for per-stage times (reference, worker on a laptop through the proxy, after the Navoiy hop-length fix: LLM first token ≈ 0.5–0.8 s, TTS first audio ≈ 1.3–1.9 s (WebSocket) / 1.7–2.3 s (HTTP), STT final ≈ 0.6 s after the speech stops, estimated total ≈ 2.4–2.8 s). p50/p95 metrics: STT end-to-final, LLM time-to-first-token, TTS time-to-first-audio, total speech-to-speech latency, errors and GPU queue depth.
7. Repeat with 1, 5 and 10 simultaneous calls before promising a concurrency limit.

**Turn-taking:** GigaAM streaming uses repeated full-context decoding + its own VAD. LiveKit also uses Silero VAD for barge-in. Avoid aggressively lowering both endpoint delays before measuring extra waits and transcription accuracy. Uzbek is not a guaranteed language for the separate learned LiveKit turn detector, so keep the configured `turn.mode: stt` unless tested otherwise.

**Knowledge/tools:** put non-secret `.md`/`.txt` content in `knowledge/docs/`; the bundled `search_knowledge` tool uses basic local keyword search (not vector RAG). When enabling Gemma function calling, the vLLM server must run with the Gemma 4 tool flags (doc 02, section B); test a tool call end to end.

## F. Persistence and the browser UI

Chat and call conversations are stored separately (`kind` = chat or call) in SQLite at `/app/data/neurotech-conversations.sqlite3`, on the `conversation_data` volume, so they survive `docker compose up --force-recreate`. Tickets and appointments created by agents go to `/app/data/agent_records.sqlite3`. Chat is text-only; only calls are voiced. The UI is Uzbek-only for now (Russian and English are marked "coming soon").

The latency-related worker settings (preemptive generation, endpointing, first-sentence split, `max_chars_per_request`) are described in `00_QUICKSTART.md` and measured with `scripts/bench_latency.py`. Running the worker in the same datacenter as the pods is the largest remaining gain.

## G. How the reply becomes speech (TTS pieces)

The LLM text is streamed into `DualGatewayTTS.stream()` (`voice_agent/adapters/dual_tts.py`). Its tokenizer (`voice_agent/tokenizers.py`) cuts the text into **pieces**: the first piece is the first full sentence (a short opener such as "Xo'p." is kept together with it), later sentences are merged until about 60 characters, and no piece is cut inside a sentence unless it has no punctuation for more than 120 (first) or 300 characters. One gateway request is made per piece, and up to `prefetch_phrases` pieces are requested ahead of playback, so there is no request round trip between pieces. Audio is played strictly in order.

Limits measured on the Navoiy pod: the gateway and the Navoiy worker accept at most **350 characters** per request (351 is rejected with HTTP 422). Up to 312 characters, speech speed stayed 15–16 characters per second with no looping, and time to first audio did not grow with the text length (about 2.0–2.5 s through the proxy), so `max_chars_per_request: 340` costs nothing in latency and avoids cutting sentences. Set `input_streaming: false` to return to LiveKit's sentence-by-sentence adapter if the streaming path misbehaves.

### Cached phrases and emotion

- **Greetings** are rendered once per agent and played from the cache the moment the caller joins (no 2.5-6 s wait). **Fixed phrases** (`agents/phrases.yaml`: fillers said before a tool call, closers) are rendered once too, and any other short phrase (up to 160 characters) is cached the first time it is spoken, so a repeated sentence is instant and sounds the same each time. The cache lives on the `conversation_data` volume (`/app/data/greeting-cache`), is keyed by model, voice, emotion and exact text, and is pruned at 3000 files.
- After a deploy, or after changing `emotion`, render the fixed phrases once: `docker compose exec worker python -m voice_agent.greeting_cache`.
- The model is told to use the filler sentences word for word (`agents/shared_rules.md`, from `agents/phrases.yaml`), so they hit the cache.
- Emotion: `emotion` and `greeting_emotion` in `config/agent.yaml`. Valid names found on the Navoiy pod: `calm`, `warm`, `gentle`, `happy`, `excited`, `sad`, `angry`, `surprised`. Listen to the same sentences in each before choosing; after a change, restart the worker and re-run the render command above. An unknown emotion makes the gateway drop the connection instead of answering with an error.
