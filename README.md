# NEUROTECH Voice Orchestrator — Updated for New Customer UI

Independent Python LiveKit Agents worker + FastAPI backend. Keeps previous GigaAM/vLLM/dual-streaming-TTS integrations, adds personae, user-scoped SQLite conversation history, reconnect ChatContext, voice-note WAV conversion/transcription, manual interruption and new UI contracts.

`cp .env.example .env`, set real service credentials and `TTS_GATEWAY_URL`, edit `config/agent.yaml` with real GigaAM/vLLM URLs, then `docker compose up -d --build` in this folder.

- API: port 8090 on localhost, expose through trusted frontend reverse proxy.
- Worker: dispatches to LiveKit and consumes external STT/LLM/TTS GPU services.
- Database: persistent `conversation_data` Docker volume at `/app/data/conversations.sqlite3`.
- Dual TTS gateway: external URL and key only. No cloned gateway and no model weights in this folder.

See parent `INTEGRATION.md` and `FEATURE_MAPPING.md` for all routes, supported UI behavior, and limitations. `pytest -q` for local contract tests. Production model and browser streaming remain deployment-level tests.
