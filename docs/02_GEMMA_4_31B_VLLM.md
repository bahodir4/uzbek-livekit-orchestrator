# 02 — Gemma 4 31B IT via vLLM (RunPod, no Docker)

**Model:** `google/gemma-4-31B-it`, full BF16 (not the base `google/gemma-4-31B`). **Hardware:** 1× A100 80GB as the official minimum NVIDIA BF16 recipe; 2× A100 80GB with tensor parallelism 2 for more room. The model files are about **62.6 GB**, so allocate at least ~100 GB persistent storage for this Pod. Start with 8K tokens and modest concurrency.

## A. GPU environment and installation

Use a RunPod CUDA/PyTorch Pod with a compatible driver and **Python 3.12**. Verify GPU and disk:

```bash
nvidia-smi
python3.12 --version
df -h /workspace
```

For Python 3.12:

```bash
python3.12 -m venv /workspace/gemma-vllm-venv
source /workspace/gemma-vllm-venv/bin/activate
python -m pip install -U pip uv
uv pip install vllm --torch-backend=auto
vllm --version
```

If your template does not include Python 3.12, use a supported vLLM Python version / compatible RunPod template instead of force-upgrading Torch/CUDA packages. Check vLLM's GPU install guide. For gated access / rate limits, authenticate via `hf auth login` (install `huggingface_hub[cli]` if needed), or provide `HF_TOKEN` securely in the Pod environment.

## B. Start the server

The key is passed to vLLM through the `VLLM_API_KEY` **environment variable**. Do not use `--api-key` on the command line: it shows up in the process list (`ps`, `pgrep -af`). Generate a key and keep it out of Git and logs:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Make sure no old vLLM still holds the GPU (otherwise start fails with `Free memory on device ... is less than desired GPU memory utilization`):

```bash
pkill -9 -f vllm; pkill -9 -f EngineCore; sleep 5
nvidia-smi --query-gpu=memory.used --format=csv      # should be about 0 MiB
```

Create the start script once. **The tool-calling flags are required**: the agents have tools (loan/deposit calculators, bookings), so the orchestrator sends `tool_choice: auto`. Without these flags every reply fails with `400 "auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set`.

```bash
cat > /workspace/start_llm.sh <<'EOF'
#!/usr/bin/env bash
source /workspace/gemma-vllm-venv/bin/activate
export HF_HOME=/workspace/gemma-hf-cache
: "${VLLM_API_KEY:?set VLLM_API_KEY first}"
exec vllm serve google/gemma-4-31B-it --host 0.0.0.0 --port 8000 \
  --dtype bfloat16 --max-model-len 8192 --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4
EOF
chmod +x /workspace/start_llm.sh
```

Start it in the background (the key prompt is hidden; type the key itself, not a placeholder):

```bash
read -rs -p "VLLM_API_KEY: " VLLM_API_KEY; export VLLM_API_KEY; echo
nohup /workspace/start_llm.sh > /workspace/vllm.log 2>&1 &
tail -f /workspace/vllm.log          # Ctrl+C stops watching, not the server
```

Wait for `Application startup complete` (several minutes the first time; the 62 GB download is cached under `/workspace/gemma-hf-cache`). `vllm` is only on the PATH inside the venv, so a bare `vllm serve` in a fresh shell fails with `command not found`; use the script.

This uses **one** A100 80GB. For **two** A100s, add `--tensor-parallel-size 2`. `--max-model-len` and GPU memory settings are starting values, not guaranteed throughput. `--host 0.0.0.0` is needed for access via the RunPod HTTP proxy, so always require authentication and TLS.

Check which parser names your vLLM version accepts: `/workspace/gemma-vllm-venv/bin/vllm serve --help=all | grep -A2 -- --tool-call-parser`. Verified on vLLM 0.32.0: it loads the `Gemma4ForConditionalGeneration` architecture; the tool-call flags above are **not yet verified end to end** (see section D).

## C. Verify readiness and streaming output

In a second terminal on the LLM Pod with the same key securely available:

```bash
curl -fsS http://127.0.0.1:8000/v1/models \
  -H "Authorization: Bearer $VLLM_API_KEY"
```

The model ID must match the value in the JSON. Smoke test **streamed text**:

```bash
curl -N --fail-with-body -sS http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $VLLM_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model":"google/gemma-4-31B-it",
    "messages":[{"role":"user","content":"O‘zbek tilida bir gap bilan salomlash."}],
    "temperature":0.3,
    "max_tokens":90,
    "stream":true
  }'
```

Successful SSE responses arrive progressively as `data: ...` events. Remote orchestrator base URL is `https://LLM_POD_ID-8000.proxy.runpod.net/v1` (expose `8000/http`). Configure that exact served model name in `config/agent.yaml` and share the **same** `VLLM_API_KEY` in the orchestrator's `.env`.

## D. Tools and performance

- **Tool calls:** required for the specialised agents (see `06_AGENTS.md`). Start vLLM with `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4` (section B). If your vLLM version also wants the recipe's `tool_chat_template_gemma4.jinja`, add `--chat-template <path>`. Test a structured tool call end to end (for example ask the savings agent for a deposit calculation) before relying on actions. If you start vLLM without these flags, the worker log shows `400 "auto" tool choice requires --enable-auto-tool-choice ...` on every reply.
- **Latency:** For voice calls, reduce answer length (`max_completion_tokens` in the orchestrator, e.g., 128–256), test first-token latency, and avoid long reasoning outputs. Check KV-cache utilization and the model's memory footprint before increasing simultaneous calls.
- **OOM:** Reduce context/window and concurrent requests, or move to two 80GB GPUs; 24 GB GPUs cannot hold unquantized 31B BF16 weights without multi-GPU sharding/offload.
- **401:** The agent and server must use the **same** new API key, without `Bearer` in the `.env` value.
- **502 from the RunPod URL:** the proxy cannot reach vLLM: it is still loading, stopped, or the URL belongs to another pod. Check `pgrep -af "vllm serve"` and `tail -n 30 /workspace/vllm.log` on the pod; the pod ID changes when you create a new pod.
- **`Free memory on device (18.59/79.25 GiB) ... less than desired GPU memory utilization`:** a previous vLLM process still holds the GPU. Kill it (section B) or restart the pod.

**Upstream documentation:** https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html · https://docs.vllm.ai/en/stable/getting_started/quickstart/ · https://huggingface.co/google/gemma-4-31B-it

## E. Tuning for voice latency (first token under ~1 s)

Measured through the RunPod HTTPS proxy: first token 0.8-1.8 s, of which ~0.5-1 s is proxy overhead.
Fix the placement first (run the worker in the same datacenter and use the pod's private/localhost URL),
then tune the server:

- Keep prefix caching on (default in vLLM V1; the system prompt + history are reused every turn).
- Lower the context to what a voice call needs: `--max-model-len 4096` (smaller KV cache, more headroom).
- Cap concurrency to what you serve: `--max-num-seqs 8` (voice calls are few and latency-sensitive).
- Decode speed is memory-bandwidth bound (~62 GB of BF16 weights per token). The only big lever is
  fewer bytes per token: weight-only FP8 (`--quantization fp8`) or a smaller instruct model.
  Validate Uzbek answer quality after any change; test with the curl smoke test above.
- Keep replies short in the orchestrator (`max_completion_tokens: 200`) and keep thinking/reasoning
  output off for voice.

Verify with the orchestrator log line `Voice metric ... stage=llm {'ttft': ...}` (target < 1 s).
