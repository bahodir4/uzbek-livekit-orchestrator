# NEUROTECH Voice Orchestrator

Python LiveKit Agents worker + FastAPI backend for an Uzbek voice agent: four female agents (Sofiya, Malika, Dilnoza, Zarina) with their own prompts and tools, GigaAM STT, Gemma 4 on vLLM, Navoiy TTS behind a dual-TTS gateway, per-user SQLite conversation history (chat and call kept separate), and the NEUROTECH customer UI (Uzbek only for now).

## Install

Follow **[`docs/00_QUICKSTART.md`](docs/00_QUICKSTART.md)**: LLM pod, STT pod, TTS pod, then the orchestrator.

```bash
cp .env.example .env        # LIVEKIT_*, STT_API_KEY, VLLM_API_KEY, TTS_API_KEY, ADMIN_*
nano config/agent.yaml      # the three pod URLs (LLM URL ends with /v1)
docker compose up -d --build
python scripts/bench_latency.py -n 4      # per-stage latency
```

## Docs

| File | Topic |
|---|---|
| `docs/00_QUICKSTART.md` | default install order, keys, health checks, logs |
| `docs/01_GIGAAM_STT_RUNPOD.md` | STT pod |
| `docs/02_GEMMA_4_31B_VLLM.md` | LLM pod (needs the tool-calling flags) |
| `docs/03_NAVOIY_TTS_RUNPOD.md` | TTS pod: Navoiy + gateway (default) |
| `docs/03_SAYRO_TTS_RUNPOD_UPDATED.md` | Sayro TTS (optional) |
| `docs/04_ORCHESTRATOR_WIRING.md` | connect everything, acceptance checklist |
| `docs/05_DUAL_GATEWAY_FRONTEND.md` | UI and gateway notes |
| `docs/06_AGENTS.md` | agent prompts, tools and business rules (`agents/`) |
| `docs/09_NGINX_PUBLIC.md` | publish the orchestrator and UI on one domain with nginx (`deploy/nginx/orchestrator.conf`) |
| `docs/07_NAVOIY_FIRST_AUDIO_FIX.md` | fix for the slow first audio on the TTS pod (hop-length reset), with the patch to paste |

- API: port 8090 on localhost; expose it through a trusted reverse proxy.
- Worker: joins LiveKit rooms and calls the external STT, LLM and TTS services.
- Data: `conversation_data` Docker volume at `/app/data` (conversations and agent records; survives container recreation).
- No model weights and no TTS gateway code in this repository.

`pytest -q` runs the local contract tests. Real streaming latency and interruption behaviour must be measured on the deployed pods.

## O'zbekcha hujjatlar (docs/uz/)

| Fayl | Mavzu |
|---|---|
| `docs/uz/00_QUICKSTART.md` | standart o'rnatish tartibi, kalitlar, tekshiruvlar, loglar |
| `docs/uz/01_GIGAAM_STT_RUNPOD.md` | STT pod |
| `docs/uz/02_GEMMA_4_31B_VLLM.md` | LLM pod |
| `docs/uz/08_UI_ULASH.md` | **orchestrator'ni UI bilan ulash**: lokal, Docker, ngrok, kirish qoidalari, xatolar |
| `docs/uz/09_NGINX_PUBLIC.md` | **orchestrator'ni nginx bilan internetga ochish** (`deploy/nginx/orchestrator.conf`) |
