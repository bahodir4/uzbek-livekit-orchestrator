# 03 — Sayro TTS on RunPod (fresh installation and later restart)

**Service:** `uzlm/sayro-tts-1.7B` · **Port:** `8000` · **Stack:** vLLM 0.30.0 + vLLM-Omni 0.30.0 · **Purpose:** Uzbek TTS for the LiveKit orchestrator.

> This is a reproducible operations guide for the environment used in October 2026, not a guarantee that future upstream releases have identical APIs. Pin and verify package versions before production deployment.

## 1. Prerequisites

- RunPod GPU with enough free VRAM for the model and generation; validate memory and latency on the selected GPU.
- Python 3.12 and CUDA-compatible PyTorch/vLLM environment.
- Hugging Face account **approved** to access the gated model at <https://huggingface.co/uzlm/sayro-tts-1.7B>.
- Persistent `/workspace` volume recommended; otherwise model downloads and venv may disappear when the pod is replaced.
- RunPod HTTP proxy configured for port `8000` if external access is needed.

## 2. Activate an existing installation

```bash
source /workspace/sayro-vllm-venv/bin/activate
mkdir -p /workspace/sayro-hf-cache
export HF_HOME=/workspace/sayro-hf-cache
```

If the virtual environment does not exist on a new pod, **do not run the activation command**. Recreate the compatible environment from your tested lockfile/image or installation procedure first. Do not blindly upgrade all packages in an existing working environment.

## 3. Authenticate to the gated model

```bash
# HF_HOME must be set before login and before launching the server.
export HF_HOME=/workspace/sayro-hf-cache
hf auth login
hf auth whoami
```

The CLI may offer browser/device authentication. Do not paste tokens into documentation or Git. Verify download access:

```bash
python - <<'PY'
from huggingface_hub import hf_hub_download
path = hf_hub_download('uzlm/sayro-tts-1.7B', 'config.json')
print('Access OK:', path)
PY
```

A `401`/`GatedRepoError` means the account lacks access or the server is not authenticated with an approved account. A Hugging Face token is **not** the same as an optional API key protecting the TTS endpoint.

## 4. Dependency compatibility and known failure

The following installed package versions were observed in this project:

| Package | Observed version | Relevant dependency |
|---|---|---|
| `vllm` | `0.30.0` | `huggingface-hub>=1.31.0` |
| `vllm-omni` | `0.30.0` | `diffusers==0.40.0` |
| `transformers` | `5.14.1` | `huggingface-hub>=1.5.0,<2.0` |
| `diffusers` | `0.40.0` | `huggingface-hub>=1.23.0,<2.0` |
| `kernels` | `0.16.1` | `huggingface-hub>=1.10.0` |

**Important:** Those requirements imply `huggingface-hub>=1.31.0,<2.0`. This is the intersection of the *reported package metadata*, **not proof that a compatible published version exists**. Check the package index and resolver before installing. In particular, **do not blindly install `huggingface-hub==1.5.0`**: it fails the reported vLLM and Diffusers requirements. Installing `huggingface-hub==2.2.0` also caused Transformers to reject the environment.

Inspect the installed environment:

```bash
python -m pip check
python - <<'PY'
from importlib.metadata import version, requires
for name in ('vllm','vllm-omni','transformers','diffusers','huggingface-hub','kernels'):
    try:
        print('\n', name, version(name))
        for req in requires(name) or []:
            if 'huggingface' in req.lower() or 'diffusers' in req.lower():
                print('  ', req)
    except Exception as exc:
        print(name, exc)
PY
```

Check availability of compatible releases **without modifying the environment**:

```bash
python -m pip index versions huggingface-hub
python -m pip install --dry-run 'huggingface-hub>=1.31.0,<2.0'
```

If pip reports **no matching distribution**, the installed package versions are mutually incompatible. Resolve this by using a verified compatible vLLM-Omni/Transformers/Diffusers stack, preferably in a fresh venv or container; do not force an imaginary Hub version or bypass dependency checks. Keep the current venv intact for diagnosis.

Once dependencies have been resolved, verify imports:

```bash
python - <<'PY'
import huggingface_hub, transformers, vllm, vllm_omni
from diffusers import DiffusionPipeline
print('Hub:', huggingface_hub.__version__)
print('Transformers:', transformers.__version__)
print('vLLM:', vllm.__version__)
print('vLLM-Omni:', vllm_omni.__version__)
print('Diffusers import OK')
PY
python -m pip check
```

Known traceback: `cannot import name 'get_cached_repo_tree' from 'huggingface_hub'` indicates a Hub/Diffusers API mismatch. Diagnose versions rather than changing CUDA or redownloading model weights.

## 5. Launch the server

Only after imports and `pip check` succeed:

```bash
source /workspace/sayro-vllm-venv/bin/activate
export HF_HOME=/workspace/sayro-hf-cache
vllm serve uzlm/sayro-tts-1.7B \
  --omni \
  --host 0.0.0.0 \
  --port 8000
```

This command was accepted by the project's vLLM-Omni 0.30.0 entrypoint before dependency failure. For a newer stack, verify `vllm --help` and its Omni CLI documentation. Keep the process running or manage it using a supervisor/container. Model download on the first successful run may take time.

### Optional API authentication

For production, use an API key if the installed server supports `--api-key` (confirm with `vllm serve --help`):

```bash
export SAYRO_API_KEY='REPLACE_WITH_NEW_SECRET'
vllm serve uzlm/sayro-tts-1.7B --omni --host 0.0.0.0 --port 8000 --api-key "$SAYRO_API_KEY"
```

Do not expose an unauthenticated TTS endpoint publicly for persistent use. If the Omni server does not support the flag, enforce authentication through a gateway/reverse proxy instead.

## 6. Smoke test on the pod

In a **second terminal**:

```bash
curl -i http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/v1/models
```

Generate speech (include an Authorization header if the endpoint is protected):

```bash
curl -fS http://127.0.0.1:8000/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "uzlm/sayro-tts-1.7B",
    "input": "Assalomu alaykum! Sizga qanday yordam bera olaman?",
    "voice": "sayro",
    "language": "Auto",
    "task_type": "CustomVoice",
    "response_format": "wav",
    "non_streaming_mode": true,
    "max_new_tokens": 180,
    "seed": 123
  }' --output sayro-test.wav
file sayro-test.wav
ls -lh sayro-test.wav
```

For an authenticated endpoint add `-H "Authorization: Bearer $SAYRO_API_KEY"` to each applicable curl command. A nonempty file alone is insufficient: verify it is a playable WAV and not an error payload.

## 7. Configure the LiveKit orchestrator

`config/agent.yaml` (example provider block):

```yaml
tts:
  sayro_primary:
    type: sayro_http
    url: https://YOUR-SAYRO-POD-8000.proxy.runpod.net
    # Include only if the server requires authentication and adapter supports it:
    # api_key_env: SAYRO_API_KEY
    voice: sayro
    language: Auto
    task_type: CustomVoice
    sample_rate: 24000
    max_new_tokens: 180
    max_chars_per_request: 115
    timeout_seconds: 25
    max_audio_seconds: 20
    seed: 123
    trim_edge_silence: true
    silence_threshold: 450
    streaming_mode: sentence_http
```

`api_key_env` takes an **environment variable name**, never the secret itself. Set `SAYRO_API_KEY` in the orchestrator's `.env` only if endpoint authentication is enabled. Check the adapter's implementation for how omitted/empty keys are handled. If using Docker, ensure the env file is loaded into the running container.

**Streaming note:** `sentence_http` is phrase-level incremental generation from complete WAV responses, not native token-by-token audio streaming. Benchmark time to first audio, full response latency, and interruption behavior.

## 8. Future restart checklist

1. Confirm RunPod pod and persistent volume are present.
2. Activate venv and set `HF_HOME`.
3. Run `python -m pip check` and the import smoke test if the image/venv changed.
4. Confirm Hugging Face access (if downloading on a fresh pod).
5. Launch Sayro; wait for healthy API startup.
6. Generate a playable Uzbek WAV locally.
7. Update `config/agent.yaml` if the RunPod proxy hostname changed.
8. Run orchestrator configuration/connection checks and a full LiveKit speech-in/speech-out call.

## 9. Security and operational notes

- Rotate any LiveKit, STT, LLM, Sayro, or admin secrets previously exposed in chat/screenshots; never commit `.env`.
- Store secrets in environment variables or a secret manager, not YAML.
- `HF_HOME=/workspace/sayro-hf-cache` must be consistent across login and startup.
- Keep the GPU pod, TTS API, and orchestrator in nearby regions where possible.
- `max_concurrent_jobs` is a software limit, not a measured GPU capacity guarantee.
- For reproducibility, capture a **working** environment using `python -m pip freeze > requirements-sayro-locked.txt` only after a successful audio-generation test.
