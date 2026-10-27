# 07 — Navoiy: fix the slow first audio (hop-length reset)

**Symptom:** on the TTS pod every longer phrase takes 2.5–3 s before any sound (`first_audio_ms` 2.5–3.0 s in `/workspace/navoiy.log`) and the log shows `yield speech len 4.04` for the first chunk, never a smaller one. Short phrases are fine (about 1.1 s).

**Cause (read in the pod's CosyVoice source, `cosyvoice/cli/model.py`, `CosyVoice2Model.tts`):** while streaming, the loop executes

```python
self.token_hop_len = min(self.token_max_hop_len, self.token_hop_len * self.stream_scale_factor)
```

on the **model object** and never resets it. The first request (the startup warm-up) grows it 25 → 50 → 100, and it stays at 100 for every later request. So the first chunk is always 100 speech tokens (about 4 s of audio), and the model has to generate all of them before the first sound. With the value reset to 25, the first chunk is about 25 tokens plus padding and lookahead (1–2 s of audio).

**Fix:** the Navoiy engine restores the configured hop length before each request (`navoiy_worker/engine.py`, method `_restore_first_chunk_size`). It is in the TTS repo (commit `a53e129`, tests in `tests/test_engine_hop_reset.py`). A pod set up earlier has an older copy of the repo and no git remote, so apply the same change with the idempotent patch below.

## Apply on the TTS pod (copy/paste the whole block)

```bash
cat > /workspace/patch_hop_reset.py <<'EOF'
import sys
from pathlib import Path
path = Path("/workspace/uzbek-dual-tts-backend/navoiy_worker/engine.py")
text = path.read_text(encoding="utf-8")
if "_restore_first_chunk_size" in text:
    print("already patched"); raise SystemExit(0)
method = '''    def _restore_first_chunk_size(self) -> None:
        """CosyVoice doubles model.token_hop_len while streaming and never resets it; restore it for every request."""
        inner = getattr(self.model, "model", None)
        if inner is None or not hasattr(inner, "token_hop_len"):
            return
        if not hasattr(self, "_hop_default"):
            self._hop_default = inner.token_hop_len
        inner.token_hop_len = self._hop_default

'''
anchor_def = "    def synthesize(self, text: str, emotion: str, seed: int | None, cancelled: threading.Event)"
anchor_call = '        instruction = item["instruct"].strip() + "<|endofprompt|>"\n'
if anchor_def not in text or anchor_call not in text:
    raise SystemExit("engine.py layout differs; not patched")
text = text.replace(anchor_def, method + anchor_def, 1)
text = text.replace(anchor_call, anchor_call + "        self._restore_first_chunk_size()\n", 1)
path.write_text(text, encoding="utf-8")
print("patched")
EOF
python3 /workspace/patch_hop_reset.py
/workspace/restart_tts.sh
```

Expected output: `patched` (or `already patched`), then `NAVOIY READY` and the gateway readiness report. `/workspace/restart_tts.sh` is the restart script from `03_NAVOIY_TTS_RUNPOD.md`, section 5b. If the patch says the layout differs, the pod's `engine.py` is a different version: do not edit it by hand, send the message to the maintainer.

## Check that it worked

Place a test call (or a few chat turns), then on the pod:

```bash
grep -E "yield speech len|first_audio_ms" /workspace/navoiy.log | tail -n 16
```

| | Before | Expected after |
|---|---|---|
| first `yield speech len` of a long phrase | always 4.04 | about 1.1–2.0 |
| `first_audio_ms` of a long phrase | 2.5–3.0 s | about 1.0–1.6 s |
| `first_audio_ms` of a short phrase | about 1.1 s | about the same |

Later chunks of a phrase grow again (the hop doubles up to its maximum), so the total audio per phrase is unchanged. Smaller first chunks add some overhead, so `rtf` of the first chunk can rise (toward 0.8–1.0). If the audio starts to stutter between chunks, tell the maintainer: the next step is a slightly larger starting hop (`NAVOIY_TOKEN_HOP_LEN=35` in the pod `.env`).

## Undo

```bash
cd /workspace/uzbek-dual-tts-backend
python3 - <<'EOF'
from pathlib import Path
p = Path("navoiy_worker/engine.py"); s = p.read_text()
a = s.index("    def _restore_first_chunk_size"); b = s.index("    def synthesize")
s = s[:a] + s[b:]
s = s.replace("        self._restore_first_chunk_size()\n", "")
p.write_text(s); print("reverted")
EOF
/workspace/restart_tts.sh
```

## Measured result (RTX 4090 pod, after the fix)

| | Before | After |
|---|---|---|
| first `yield speech len` of a longer phrase | 4.04 s of audio, always | 0.9–1.04 s, then 2.0 s, then 2.8 s |
| `first_audio_ms` | 2.5–3.0 s | 1.07–1.78 s (mostly 1.1–1.5 s) |

The first small chunk has `rtf` 1.1–1.3 (slightly slower than real time), later chunks 0.4–0.65. A very short pause after the first chunk is possible; if audible, set `NAVOIY_TOKEN_HOP_LEN=35` in the pod `.env` and restart.

## Do not enable JIT

`NAVOIY_LOAD_JIT=1` fails on a standard install (`ValueError: ... flow.encoder.fp16.zip does not exist`): those files are not in the model download. The engine then falls back to the plain model, but loads twice (77 s instead of 18 s). If it is in the pod `.env`, remove it: `sed -i '/^NAVOIY_LOAD_JIT=/d' .env` and restart.

## Other things this does not change

- The model's speed is the same: about 0.6–0.8 s of generation per second of audio on an RTX 4090.
- The RunPod proxy still adds about 0.4–0.5 s per request. Run the worker in the same datacenter as the pods.
- Streamed text input (sending LLM tokens straight into the model) is separate and not built. `scripts/probe_streaming_input.py` in the TTS repo tells whether the checkpoint supports it.
