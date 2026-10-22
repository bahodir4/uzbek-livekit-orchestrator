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
SAYRO_API_KEY=TTS_SERVICE_KEY
ADMIN_USERNAME=admin
ADMIN_PASSWORD=YOUR_LONG_UNIQUE_ADMIN_PASSWORD
```

**`config/agent.yaml` provider fields (edit the corresponding entries; preserve the rest of the YAML):**

```yaml
active:
  stt: gigaam_primary
  llm: local_vllm
  tts: sayro_primary
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
      max_completion_tokens: 256
  tts:
    sayro_primary:
      type: sayro_http
      url: https://TTS_POD_ID-8000.proxy.runpod.net
      api_key_env: SAYRO_API_KEY
      voice: sayro
      language: Auto
      task_type: CustomVoice
      sample_rate: 24000
      max_new_tokens: 180
      max_chars_per_request: 115
      streaming_mode: sentence_http
```

**Important:** This is a **configuration excerpt**, not a replacement for the complete `config/agent.yaml`; replacing the entire file with the excerpt would drop required sections (`agent`, `turn`, `vad`, `capacity`, `knowledge`). Use `python -m voice_agent.cli provider set ...` commands instead if preferred.

## B. Start via Docker Compose

```bash
docker compose build
docker compose up -d
docker compose logs -f agent
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

Connect your LiveKit client or Agents Playground to the same room with a **separate participant token**. The orchestrator worker should receive GigaAM interim/final events, send final user text to Gemma (streaming SSE), commit completed phrase text to Sayro HTTP, and play the returned audio to LiveKit.

## D. Change models from terminal

```bash
source .venv/bin/activate
python -m voice_agent.cli show
python -m voice_agent.cli provider set llm local_vllm model 'google/gemma-4-31B-it'
python -m voice_agent.cli provider set llm local_vllm url 'https://LLM_POD_ID-8000.proxy.runpod.net/v1'
python -m voice_agent.cli provider set stt gigaam_primary url 'https://STT_POD_ID-8000.proxy.runpod.net'
python -m voice_agent.cli provider set tts sayro_primary url 'https://TTS_POD_ID-8000.proxy.runpod.net'
python -m voice_agent.cli tune turn.endpointing_min_delay 0.25
python -m voice_agent.cli validate
```

CLI changes persist in `config/agent.yaml`. New rooms use updated provider selections; active calls retain their original provider instances. Environment-variable/credential changes require a worker restart.

## E. Voice-agent acceptance checklist

1. `/health/ready` on STT is true; `/v1/models` on LLM and Sayro respond; the three service APIs can be called from the orchestration host.
2. Sayro returns audible Uzbek for **short** prompts; no long trailing silence or unrelated speech.
3. LiveKit can dispatch the agent into a real room and STT produces final transcripts.
4. Gemma emits SSE content chunks early and responds in Uzbek; test `search_knowledge` tool separately.
5. Caller interrupts TTS; playback stops and queued synthesis results are ignored.
6. p50/p95 metrics: STT end-to-final, LLM time-to-first-token, TTS time-to-first-audio, total speech-to-speech latency, errors and GPU queue depth.
7. Repeat with 1, 5 and 10 simultaneous calls before promising a concurrency limit.

**Turn-taking:** GigaAM streaming uses repeated full-context decoding + its own VAD. LiveKit also uses Silero VAD for barge-in. Avoid aggressively lowering both endpoint delays before measuring extra waits and transcription accuracy. Uzbek is not a guaranteed language for the separate learned LiveKit turn detector, so keep the configured `turn.mode: stt` unless tested otherwise.

**Knowledge/tools:** put non-secret `.md`/`.txt` content in `knowledge/docs/`; the bundled `search_knowledge` tool uses basic local keyword search (not vector RAG). When enabling Gemma function calling, ensure your vLLM server has the correct Gemma 4 tool parser/template and test tool-call JSON structure.
