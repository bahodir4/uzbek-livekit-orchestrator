# 01 — GigaAM Multilingual STT (RunPod, no Docker)

**Goal:** Uzbek STT, HTTP WAV/MP3 upload and low-latency pseudo-streaming WebSocket. **Recommended GPU:** 1× RTX A5000 24 GB (or the previously tested RTX 4090). **Port:** `8000/http` exposed in the RunPod Pod configuration.

## A. Create and install

Choose a RunPod **PyTorch/CUDA GPU Pod** and a persistent `/workspace` volume (50 GB is a reasonable initial allocation). Open the terminal:

```bash
nvidia-smi
cd /workspace
git clone https://github.com/bahodir4/gigaam-livekit-stt.git
cd gigaam-livekit-stt
bash scripts/runpod/install.sh
```

If the repo is private, configure GitHub auth first. The checked-out `main` branch must contain the **latest v0.2 changes**, including `/tester`, long-WAV support, and the `TranscriptionResult.text` fix. `install.sh` prepares `/workspace/gigaam-stt-venv`, FFmpeg, model cache, and `.env`; it is **not** a Docker command.

## B. Configure checkpoint, secrets, and latency

```bash
nano /workspace/gigaam-livekit-stt/.env
```

Set or verify (preserve the generated `STT_API_KEY` and keep it private):

```ini
STT_API_KEY=YOUR_NEW_RANDOM_API_KEY
PREFER_BASE_MODEL=true
ENABLE_BASE_FALLBACK=true
GIGAAM_FALLBACK_REPO=ai-sage/GigaAM-Multilingual
GIGAAM_FALLBACK_REVISION=large_ctc
GPU_WORKERS=1
PARTIAL_INTERVAL_MS=850
END_SILENCE_MS=570
HF_HOME=/workspace/gigaam-hf-cache
```

`PREFER_BASE_MODEL=true` avoids repeated `403` requests to Rustam's gated checkpoint. To use Rustam's fine-tune instead, first obtain authorization and its commercial license, then set `PREFER_BASE_MODEL=false` and configure `HF_TOKEN`, `HF_REPO` and `HF_REVISION`. Do **not** use the base repo's `large_ssl` variant for transcription; `large_ctc` is the ASR checkpoint.

## C. Start and test

```bash
bash scripts/runpod/start.sh   # foreground for first run
```

In a **second terminal**:

```bash
curl -sS http://127.0.0.1:8000/health/ready
cd /workspace/gigaam-livekit-stt
set -a; source .env; set +a
curl --fail-with-body -sS http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $STT_API_KEY" \
  -F 'file=@/workspace/sample_uzbek.wav;type=audio/wav' \
  -F 'language=uz'
```

Replace the sample WAV with **your real uploaded file**. For MP3 use `file=@/workspace/sample_uzbek.mp3;type=audio/mpeg`. The backend converts both formats to the input required by GigaAM; long recordings are divided into smaller inference segments. `file` must be sent as multipart/form-data, with curl `-F` handling the boundary.

## D. Remote streaming test

- In your RunPod template, expose `8000/http`.
- Browser URL: `https://STT_POD_ID-8000.proxy.runpod.net/tester`.
- Enter the service base URL plus the **STT API key only** (no `Bearer` prefix); connect, start microphone, speak Uzbek and verify interim/final transcripts.
- Orchestrator WebSocket: `wss://STT_POD_ID-8000.proxy.runpod.net/v1/stt/stream`. The Python LiveKit adapter sends `Authorization: Bearer ...` and binary **16 kHz PCM16 mono** frames.

## E. Background service and diagnostics

```bash
# Stop foreground process with Ctrl+C before launching background
bash scripts/runpod/start-background.sh
tail -f /workspace/gigaam-stt-logs/server.log
# Later:
bash scripts/runpod/stop.sh
```

| Symptom | Action |
|---|---|
| `403` on Rustam repo | Use public base with `PREFER_BASE_MODEL=true`, or obtain access |
| `TranscriptionResult` 500 | Confirm your latest GitHub code includes the `.text` handling fix |
| `Too long wav file` | Confirm latest code includes safe chunking below the model limit |
| WSS `1006` | Check backend logs, RunPod proxy, key rotation, and `/tester` version |
| High streaming delay | Inspect `PARTIAL_INTERVAL_MS`, queue depth and GPU latency; do not reduce values blindly |

**Security:** Rotate the previously exposed STT and Hugging Face tokens. Never embed the STT key into a public browser application or GitHub repository.
