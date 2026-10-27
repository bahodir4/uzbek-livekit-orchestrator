# 00 — Default installation, start to finish

This is the default setup: **Uzbek only, Navoiy voice, four agents** (Sofiya, Malika, Dilnoza, Zarina). Each step links to the detailed guide. Put every pod in the **same RunPod datacenter**. The RunPod proxy adds about 0.4–0.5 s per request, and that dominates the response delay (measured with the worker on a laptop: ≈ 2.4–2.8 s from end of speech to first audio in the benchmark; replies that need a tool call take several seconds longer).

| # | Part | Pod / host | Guide |
|---|---|---|---|
| 1 | LLM: Gemma 4 31B via vLLM | A100 SXM 80 GB, ≥ 100 GB volume at `/workspace`, port 8000 | `02_GEMMA_4_31B_VLLM.md` |
| 2 | STT: GigaAM | RTX 3090/4090 class, port 8000 | `01_GIGAAM_STT_RUNPOD.md` |
| 3 | TTS: Navoiy + gateway | RTX 4090 24 GB, ≥ 30 GB volume, port 8000 | `03_NAVOIY_TTS_RUNPOD.md` |
| 4 | Orchestrator (worker + API) | CPU host or pod with Docker; best in the same datacenter | `04_ORCHESTRATOR_WIRING.md` |
| 5 | Customer UI | wherever you host the UI | `05_DUAL_GATEWAY_FRONTEND.md` |

Sayro (`03_SAYRO_TTS_RUNPOD_UPDATED.md`) is **optional and not part of the default**; skip it.

## Keys: create four new ones

Generate each with `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`. Never paste them in chat or commit them. The same value goes on the pod and in the orchestrator `.env`.

| Variable | Pod `.env` | Orchestrator `.env` |
|---|---|---|
| `VLLM_API_KEY` | LLM pod (environment variable, not `--api-key`) | `VLLM_API_KEY` |
| `STT_API_KEY` | STT pod `.env` | `STT_API_KEY` |
| `TTS_API_KEY` | TTS pod `.env` (gateway key) | `TTS_API_KEY` |
| `NAVOIY_API_KEY` | TTS pod `.env` (gateway to worker, internal) | not needed |

## Order and exact commands

**1. LLM pod.** Install once (section A of doc 02), then:

```bash
read -rs -p "VLLM_API_KEY: " VLLM_API_KEY; export VLLM_API_KEY; echo
nohup /workspace/start_llm.sh > /workspace/vllm.log 2>&1 &
tail -f /workspace/vllm.log          # wait for: Application startup complete
```

The start script must include `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4` (doc 02, section B). Without them every reply fails with `400 "auto" tool choice requires ...`.

**2. STT pod.**

```bash
cd /workspace && git clone https://github.com/bahodir4/gigaam-livekit-stt.git && cd gigaam-livekit-stt
bash scripts/runpod/install.sh          # then set STT_API_KEY in .env
bash scripts/runpod/start-background.sh
curl -s http://127.0.0.1:8000/health/ready
```

**3. TTS pod.**

```bash
cd /workspace && git clone <TTS_REPO_URL> uzbek-dual-tts-backend && cd uzbek-dual-tts-backend
cp .env.example .env && nano .env       # set TTS_API_KEY and NAVOIY_API_KEY
unset HF_HUB_ENABLE_HF_TRANSFER
./scripts/install_navoiy.sh && ./scripts/install_gateway.sh
./scripts/preflight.sh --role navoiy,gateway
./scripts/start_all.sh                  # starts Navoiy, then the gateway, then runs a speech test
```

**4. Orchestrator.** Fill `.env` (`LIVEKIT_*`, `STT_API_KEY`, `VLLM_API_KEY`, `TTS_API_KEY`, `ADMIN_*`). In `config/agent.yaml` set the three pod URLs (LLM URL **ends with `/v1`**; TTS and STT URLs do not). Then:

```bash
docker compose up -d --build
docker compose logs -f worker api
```

**5. Check everything from the orchestrator host** (keys come from `.env`, nothing is printed):

```bash
python scripts/bench_latency.py -n 4
```

It prints each stage (network, LLM first token, TTS first audio, STT final) and the estimated total. Run it before and after every change.

## Health endpoints (keys required except `/health/live`)

| Service | Endpoint | Key |
|---|---|---|
| LLM | `/v1/models` | `VLLM_API_KEY` |
| STT | `/health/ready` | `STT_API_KEY` |
| TTS gateway | `/health/ready` | `TTS_API_KEY` |
| Navoiy worker (pod-internal) | `/health/ready` | `NAVOIY_API_KEY` |

Pass the key as `Authorization: Bearer <key>`. A `502` from a `*.proxy.runpod.net` URL means the proxy cannot reach the service: it is still loading, stopped, or the pod ID changed.

## After a pod restart

Pod IDs and proxy URLs change when you create a new pod, so update `config/agent.yaml` and restart the worker (`docker compose restart worker`). Models stay on the `/workspace` volume, so a restart is only the start commands above.

## Logs

| Where | Command |
|---|---|
| LLM | `tail -f /workspace/vllm.log` |
| TTS gateway / Navoiy | `tail -f /workspace/gateway.log /workspace/navoiy.log` (or `/workspace/logs/` when started with `start_all.sh`) |
| STT | `tail -f /workspace/gigaam-stt-logs/server.log` |
| Orchestrator | `docker compose logs -f worker api` |
