# Independent Vue Frontend + External Dual-TTS Gateway

The source-based frontend is **../frontend**, a separate Vue 3/TypeScript build and separate Nginx Docker image. The backend API is served by `voice_agent.admin:app` on 8090, while the LiveKit worker is a second process/container. `admin.py` does not serve static files.

The Sayro/Navoiy gateway is unchanged and external to this repository. Set `TTS_GATEWAY_URL` and `TTS_API_KEY` in `orchestrator/.env`, and connect through the `dual_gateway` provider. When the UI selects Sayro or Navoiy, `/api/rooms` pins the TTS model to the explicit dispatch metadata; `worker.py` reads it when creating that session and forwards PCM chunks through `voice_agent/adapters/dual_tts.py`. Preview uses `/api/tts/preview` with HTTP streaming; Nginx proxy buffering is disabled.

Frontend endpoints are described by `../INTEGRATION.md`. LiveKit Agents text topics are `lk.chat` and `lk.transcription`; status is via `lk.agent.state` participant attributes. All settings apply to new sessions only.

Only GPU deployment smoke tests and real RTC calls establish actual streaming latency and interruption performance.
