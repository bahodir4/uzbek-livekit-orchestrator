# Independent LiveKit Orchestrator — STT → LLM → Sayro/Navoiy

This **separate backend** contains no compiled UI, frontend source, or copied TTS GPU workers. It receives an existing external dual-TTS gateway URL via `TTS_GATEWAY_URL` and key via `TTS_API_KEY`; both models remain hosted and managed in their original project.

## Start

```bash
cp .env.example .env
# Set LIVEKIT_*, TTS_GATEWAY_URL, TTS_API_KEY, STT_API_KEY,
# VLLM_API_KEY and a strong ADMIN_PASSWORD in .env.
# Update config/agent.yaml STT and LLM URLs/model for current deployment.
docker compose up --build -d
# API at http://localhost:8090 (loopback only); worker runs separately.
```

To run with **Vue frontend**, use `../docker-compose.yml` at the delivery root. Both use the same backend Dockerfile and configuration. No secrets belong in the UI. Use TLS in production and `UI_SECURE_COOKIE=true` with HTTPS.

## Backend functionality matching the UI

| UI feature | API or LiveKit behavior |
|---|---|
| Auth | `/api/login`, `/api/session`, `/api/logout`; HttpOnly cookie |
| Dashboard & model health | `/api/config`, `/api/overview`, `/api/capabilities` |
| Route STT/LLM/TTS provider | `/api/providers/activate` (new calls only) |
| Agent behavior, TTS defaults, interruption settings | `PATCH /api/settings` (new calls only) |
| Call | `POST /api/rooms` -> explicit LiveKit agent dispatch, one model pinned per job |
| Live voice & agent playback | LiveKit RTC microphone/remote audio |
| Chat & transcription | `lk.chat` (frontend to agent); `lk.transcription` (agent to frontend) |
| Sayro/Navoiy streaming preview | `POST /api/tts/preview` -> gateway PCM16 chunks (no WAV buffering) |
| Live TTS output | `voice_agent/adapters/dual_tts.py` -> LiveKit AudioEmitter frames |

**Important:** Web/API tests are not actual GPU-to-RTC latency or barge-in tests. Run the gateway's existing health/stream tests and make a real LiveKit call for both models before production.
