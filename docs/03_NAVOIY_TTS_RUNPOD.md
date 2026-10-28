# 03 — Navoiy TTS + gateway on RunPod (fresh installation and later restart)

**Services:** Navoiy worker (`127.0.0.1:8092`, CosyVoice2-based) behind the dual-TTS gateway (`0.0.0.0:8000`) · **Python:** 3.10 · **GPU:** RTX 4090 24 GB or similar · **Purpose:** default Uzbek TTS for the LiveKit orchestrator. Sayro stays optional (see `03_SAYRO_TTS_RUNPOD_UPDATED.md`).

> Verified on an RTX 4090 pod (October 2026): install passes, model loads in ~17 s, warm-up ~2.3 s. Speech latency under real load is not yet benchmarked. Run `scripts/bench_latency.py` from the orchestrator.

## 1. Prerequisites

- RunPod GPU pod, **Python 3.10**, 30 GB+ free under `/workspace` (models ≈ 7 GB, deps ≈ 10 GB). Persistent `/workspace` volume strongly recommended.
- HTTP port `8000` exposed (RunPod proxy). Both Hugging Face repos are public, so no HF token is needed.
- The TTS repo cloned to `/workspace/uzbek-dual-tts-backend`.

## 2. Fresh install

```bash
cd /workspace
git clone <TTS_REPO_URL> uzbek-dual-tts-backend && cd uzbek-dual-tts-backend

# the gateway key; generate one and put it in .env (never commit .env)
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'     # copy the output
nano .env                                                          # set TTS_API_KEY=<that value>

unset HF_HUB_ENABLE_HF_TRANSFER      # some templates set it without installing hf_transfer
./scripts/install_navoiy.sh          # venv, pinned deps, verified model download, validation (long)
./scripts/install_gateway.sh
./scripts/preflight.sh --role navoiy,gateway    # must show no FAIL (Sayro is optional and gated; do not use --role all)
```

The installer is idempotent: re-running skips finished steps. It prints "installed and validated" only after real import, pin and model checks pass. Harmless warnings: `pkg_resources is deprecated`, `no frontend is available`, `sox not found` (`apt-get install -y sox libsox-dev ffmpeg libsndfile1` removes the last one).

## 3. Start (background) — copy/paste

```bash
cd /workspace/uzbek-dual-tts-backend

./scripts/start_all.sh
```

It stops old copies, starts Navoiy and waits until it is loaded and warm, starts the gateway, prints the readiness report and runs a real speech test (saves `navoiy-test.wav`). Success: `Navoiy ready`, `gateway up`, `navoiy` with `ready: true`, `got a WAV`.

Both health endpoints need the key (`Authorization: Bearer <key>`): the gateway uses `TTS_API_KEY`, the Navoiy worker uses `NAVOIY_API_KEY`. Only `/health/live` is open. A keyless `/health/ready` answers `Invalid API key`.

If startup fails: `tail -n 30 /workspace/logs/navoiy.log` and `tail -n 30 /workspace/gateway.log` (a pod set up with `restart_tts.sh` in 5b logs to `/workspace/navoiy.log`).

**`ERROR Navoiy did not become ready`** does not always mean a failure: `start_all.sh` can stop waiting before the model has finished loading. Read the end of `/workspace/logs/navoiy.log`. If it shows `model loaded in ...` and `warm-up done`, Navoiy is up (confirm with `ps aux | grep navoiy_worker` and `nvidia-smi`, about 3.8 GB VRAM). Do **not** rerun `start_all.sh`, because it stops the running copies and reloads the model. Start only the gateway:

```bash
cd /workspace/uzbek-dual-tts-backend
nohup ./scripts/start_gateway.sh > /workspace/gateway.log 2>&1 &
```

If the log shows `CUDA out of memory`, an old process still holds the GPU: `pkill -f navoiy_worker`, check that `nvidia-smi` shows free VRAM, then run `start_all.sh` again.

**Port already in use** (`[Errno 98]`, uvicorn exit code 3): find the owner with `ss -ltnp | grep ':8000'`. If it is an old gateway, `pkill -f gateway.app` and start again. If it is another service, start the gateway on another port and expose that port in RunPod (RunPod only proxies the ports you expose, normally `8000/http`; a gateway on 8010 is unreachable from the orchestrator until 8010 is exposed too, so use the default 8000 whenever you can):

```bash
GATEWAY_PORT=8010 nohup ./scripts/start_gateway.sh > /workspace/gateway.log 2>&1 &
```

## 4. Smoke test on the pod

```bash
cd /workspace/uzbek-dual-tts-backend
set -a; . ./.env; set +a
curl -s -H "Authorization: Bearer $TTS_API_KEY" http://127.0.0.1:8000/health/ready
curl -s -H "Authorization: Bearer $TTS_API_KEY" http://127.0.0.1:8000/v1/models
curl -fS http://127.0.0.1:8000/v1/tts \
  -H "Authorization: Bearer $TTS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"navoiy","text":"Assalomu alaykum! Sizga qanday yordam bera olaman?","voice":"navoiy","emotion":"calm"}' \
  --output navoiy-test.wav
file navoiy-test.wav && ls -lh navoiy-test.wav
```

Check that the file is a playable WAV (`RIFF ... WAVE audio, 16 bit, mono 24000 Hz`) and not an error payload. Streaming test: `python scripts/test_stream.py`. The orchestrator uses `/v1/tts/stream` (HTTP) or `/v1/tts/ws` (WebSocket), not `/v1/tts`, so also check the stream endpoint returns bytes:

```bash
curl -sS -N -H "Authorization: Bearer $TTS_API_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"navoiy","text":"Assalomu alaykum!"}' http://127.0.0.1:8000/v1/tts/stream | wc -c
```

**Expected `degraded` status:** without Sayro installed, `/health/ready` reports `"sayro": {"reachable": false, "state": "unreachable"}` and `"status": "degraded"`, but `"ready": true` as long as Navoiy is ready. This is normal for a Navoiy-only pod. Selecting Sayro in the UI fails until it is installed (`03_SAYRO_TTS_RUNPOD_UPDATED.md`).

## 5. Configure the LiveKit orchestrator

`config/agent.yaml`:

```yaml
tts:
  dual_gateway:
    type: dual_gateway_stream
    url: https://YOUR-TTS-POD-8000.proxy.runpod.net
    api_key_env: TTS_API_KEY
    model: navoiy
    voice: navoiy
    emotion: warm
    greeting_emotion: warm
    phrase_cache: true
    sample_rate: 24000
    max_chars_per_request: 340
    timeout_seconds: 120
    max_audio_seconds: 35
    streaming_mode: pcm_http    # required for dual_gateway_stream, also when transport is ws
    transport: http             # 'ws' is faster in the benchmark but crashed a job process once (exit -11): keep http until investigated
    prefetch_phrases: 2
    input_streaming: true
```

Put the same `TTS_API_KEY` in the orchestrator `.env`. `api_key_env` is a variable **name**, never the secret. Optional lower latency: set `transport: ws` (falls back to HTTP for 30 s when the socket cannot open). `http` is still streaming (the gateway returns PCM chunks as they are generated); `ws` only saves the per-request setup, about 0.4 s of first audio in the benchmark. See `04_ORCHESTRATOR_WIRING.md` section G.

## 5b. Restart script for a pod without `start_all.sh`

A pod set up earlier may have an older copy of the TTS repo without `scripts/start_all.sh` (`git pull` brings it; a pod with no git remote cannot pull). Paste this once to create `/workspace/restart_tts.sh`, then run it for every restart:

```bash
cat > /workspace/restart_tts.sh <<'EOF'
#!/usr/bin/env bash
cd /workspace/uzbek-dual-tts-backend || exit 1
pkill -f navoiy_worker; pkill -f "gateway.app"; sleep 3
set -a; . ./.env; set +a
nohup ./scripts/start_navoiy.sh > /workspace/navoiy.log 2>&1 &
for i in $(seq 100); do
  curl -fs -H "Authorization: Bearer $NAVOIY_API_KEY" http://127.0.0.1:8092/health/ready >/dev/null && { echo "NAVOIY READY"; break; }
  sleep 3
done
nohup ./scripts/start_gateway.sh > /workspace/gateway.log 2>&1 &
sleep 5
curl -s -H "Authorization: Bearer $TTS_API_KEY" http://127.0.0.1:8000/health/ready; echo
grep -E "jit=|error|Error" /workspace/navoiy.log | tail -n 5
EOF
chmod +x /workspace/restart_tts.sh
/workspace/restart_tts.sh
```

Success: `NAVOIY READY`, and the gateway report shows `navoiy` with `ready: true`. Typical copy-paste mistakes: a space inside a path (`. env`, `vllm. log`), a line break in the middle of a command, a typed `<placeholder>`. Paste whole blocks and never type angle-bracket placeholders.

## 5c. Speed tuning (measure each change on its own)

What to look at, on the TTS pod, after a few spoken replies:

```bash
grep -E "first_audio_ms|rtf" /workspace/navoiy.log | tail -n 20
```

- Start with the hop-length fix in `07_NAVOIY_FIRST_AUDIO_FIX.md`: it is the one change that moved the first-chunk size. JIT/TRT only speed up the flow stage, not the speech-token model that dominates the time.
- `rtf` below 1 means audio is generated faster than it plays (healthy). Measured on an RTX 4090: 0.56–0.77 for normal phrases, about 1.0 for phrases under 1 s.
- `first_audio_ms` is the model's own time to first audio, with no network. Measured before tuning: about 1.0 s for a 1 s phrase, 2.0 s for 3 s, 2.5–2.8 s for 4 s, because the first chunk is nearly the whole short phrase. The worker's `Voice metric stage=tts ttfb` is this value plus 0.5–1 s of network.

Options, in the TTS pod `.env`, one at a time, restarting with `/workspace/restart_tts.sh` after each (remove a line to undo it):

| Setting | Effect | Risk |
|---|---|---|
| `NAVOIY_LOAD_JIT=1` | TorchScript flow encoder; startup log shows `jit=True` | **not usable out of the box**: needs `flow.encoder.fp16.zip` etc., which the model download does not contain (they must be exported with CosyVoice's export script). Without them the load fails, falls back to the plain model and takes about 77 s instead of 18 s. Leave it unset. |
| `NAVOIY_LOAD_TRT=1` | TensorRT flow decoder; the first start builds an engine (minutes) | higher; try only after JIT |
| `NAVOIY_TOKEN_HOP_LEN=25`, `NAVOIY_TOKEN_MAX_HOP_LEN=50` | smaller streaming chunks (the startup log prints the current defaults) | may add small gaps |

Do not paste or `cat` the `.env` into chat; it holds the keys. To show the settings without them: `grep -v -i key .env`.

## 6. Future restart checklist

1. Pod and `/workspace` volume present.
2. `./scripts/preflight.sh --role navoiy,gateway` shows no FAIL.
3. Run the start block in section 3.
4. Generate a playable WAV (section 4).
5. Update `config/agent.yaml` if the proxy hostname changed (a new pod gets a new `-8000.proxy.runpod.net` URL), then recreate the orchestrator containers (see `04_ORCHESTRATOR_WIRING.md` section B).
6. `python scripts/bench_latency.py`, then a full LiveKit call.

## 7. Security and operations

- Rotate any key previously pasted in chat or visible in `ps` output. Never commit `.env`.
- The female/male voice depends on `NAVOIY_REFERENCE` in `.env`. Check it by ear.
- Keep the TTS, STT, LLM and worker pods in the same datacenter. The RunPod proxy adds ≈ 0.5–1 s per request.
- Full details, upgrade/rollback and troubleshooting: `RUNPOD_INSTALL.md` in the TTS repo.
