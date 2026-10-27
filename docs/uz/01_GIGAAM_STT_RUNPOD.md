# 01 — GigaAM Multilingual STT (RunPod, Dockersiz)

**Maqsad:** o'zbek tilida nutqni matnga aylantirish (STT): HTTP orqali WAV/MP3 yuklash va kechikishi kam «yarim-oqimli» WebSocket. **Tavsiya etilgan GPU:** 1× RTX A5000 24 GB (yoki avval sinalgan RTX 4090). **Port:** RunPod Pod sozlamalarida `8000/http` ochilgan bo'lishi kerak.

## A. Yaratish va o'rnatish

RunPod'da **PyTorch/CUDA GPU Pod** va doimiy `/workspace` diskini tanlang (boshlang'ich uchun 50 GB yetarli). Terminalni oching:

```bash
nvidia-smi
cd /workspace
git clone https://github.com/bahodir4/gigaam-livekit-stt.git
cd gigaam-livekit-stt
bash scripts/runpod/install.sh
```

Repozitoriy yopiq (private) bo'lsa, avval GitHub autentifikatsiyasini sozlang. Yuklab olingan `main` shoxida **v0.2 ning oxirgi o'zgarishlari** bo'lishi shart: `/tester`, uzun WAV qo'llab-quvvatlashi va `TranscriptionResult.text` tuzatishi. `install.sh` `/workspace/gigaam-stt-venv`, FFmpeg, model keshi va `.env` ni tayyorlaydi; bu **Docker buyrug'i emas**.

## B. Checkpoint, maxfiy kalitlar va kechikishni sozlash

```bash
nano /workspace/gigaam-livekit-stt/.env
```

Quyidagilarni o'rnating yoki tekshiring (yaratilgan `STT_API_KEY` ni saqlab qoling va uni sir tuting):

```ini
STT_API_KEY=SIZNING_YANGI_TASODIFIY_KALITINGIZ
PREFER_BASE_MODEL=true
ENABLE_BASE_FALLBACK=true
GIGAAM_FALLBACK_REPO=ai-sage/GigaAM-Multilingual
GIGAAM_FALLBACK_REVISION=large_ctc
GPU_WORKERS=1
PARTIAL_INTERVAL_MS=500
END_SILENCE_MS=380
HF_HOME=/workspace/gigaam-hf-cache
```

`PREFER_BASE_MODEL=true` Rustamning yopiq (gated) checkpoint'iga takroriy `403` so'rovlarini oldini oladi. Rustamning fine-tune modelidan foydalanish uchun avval ruxsat va tijoriy litsenziya oling, so'ng `PREFER_BASE_MODEL=false` qiling va `HF_TOKEN`, `HF_REPO` hamda `HF_REVISION` ni sozlang. Matnni aylantirish uchun asosiy repozitoriyning `large_ssl` variantidan foydalanmang; ASR checkpoint'i — `large_ctc`.

## C. Ishga tushirish va sinash

```bash
bash scripts/runpod/start.sh   # birinchi marta oldingi planda
```

**Ikkinchi terminalda**:

```bash
curl -sS http://127.0.0.1:8000/health/ready
cd /workspace/gigaam-livekit-stt
set -a; source .env; set +a
curl --fail-with-body -sS http://127.0.0.1:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $STT_API_KEY" \
  -F 'file=@/workspace/sample_uzbek.wav;type=audio/wav' \
  -F 'language=uz'
```

Namuna WAV o'rniga **o'zingizning haqiqiy faylingizni** qo'ying. MP3 uchun `file=@/workspace/sample_uzbek.mp3;type=audio/mpeg` ishlating. Backend ikkala formatni ham GigaAM talab qilgan kirishga o'tkazadi; uzun yozuvlar kichikroq bo'laklarga bo'linadi. `file` multipart/form-data sifatida yuborilishi kerak; curl `-F` chegarani o'zi hal qiladi.

## D. Masofadan oqim (streaming) sinovi

- RunPod shablonida `8000/http` ni oching.
- Brauzer manzili: `https://STT_POD_ID-8000.proxy.runpod.net/tester`.
- Xizmat bazaviy manzilini va **faqat STT API kalitini** kiriting (`Bearer` prefiksisiz); ulaning, mikrofonni yoqing, o'zbekcha gapiring va oraliq/yakuniy matnlarni tekshiring.
- Orchestrator WebSocket manzili: `wss://STT_POD_ID-8000.proxy.runpod.net/v1/stt/stream`. Python LiveKit adapteri `Authorization: Bearer ...` va ikkilik **16 kHz PCM16 mono** kadrlarini yuboradi.

## E. Fon xizmati va diagnostika

```bash
# Fon rejimida ishga tushirishdan oldin oldingi plandagi jarayonni Ctrl+C bilan to'xtating
bash scripts/runpod/start-background.sh
tail -f /workspace/gigaam-stt-logs/server.log
# Keyinroq:
bash scripts/runpod/stop.sh
```

| Alomat | Chora |
|---|---|
| Rustam repozitoriysida `403` | `PREFER_BASE_MODEL=true` bilan ochiq bazaviy modelni ishlating yoki ruxsat oling |
| `TranscriptionResult` 500 | GitHub'dagi oxirgi kodda `.text` tuzatishi borligini tekshiring |
| `Too long wav file` | Oxirgi kodda model chegarasidan kichik xavfsiz bo'laklash borligini tekshiring |
| WSS `1006` | Backend loglarini, RunPod proksisini, kalit almashtirilganini va `/tester` versiyasini tekshiring |
| Oqimda katta kechikish | `PARTIAL_INTERVAL_MS`, navbat chuqurligi va GPU kechikishini ko'ring; qiymatlarni ko'r-ko'rona kamaytirmang |

**Xavfsizlik:** Avval fosh bo'lgan STT va Hugging Face tokenlarini almashtiring. STT kalitini hech qachon ochiq brauzer ilovasiga yoki GitHub repozitoriysiga joylamang.

## Ovozli agent uchun standart qiymatlar va tekshiruvlar

`END_SILENCE_MS=380` va `PARTIAL_INTERVAL_MS=500` — orchestrator bilan ishlatiladigan kechikishi kam standart qiymatlar. Qo'ng'iroq qiluvchilar gapning o'rtasida to'xtatilsa, `END_SILENCE_MS` ni oshiring. Proksi orqali o'lchangan gap tugashidan yakuniy matngacha vaqt: ≈ 0,6 s (`scripts/bench_latency.py` gapdan keyin haqiqiy sukunatni yuboradi; WebSocket ulanishi ≈ 0,5–1,2 s, shuning uchun worker oqimni ochiq ushlab turadi).

```bash
bash scripts/runpod/start-background.sh
curl -s http://127.0.0.1:8000/health/ready                       # {"ready":true,...}
tail -f /workspace/gigaam-stt-logs/server.log
```

RunPod proksisi orqali chaqirilganda `/health/ready` uchun `Authorization: Bearer <STT_API_KEY>` kerak. To'liq tartib uchun `00_QUICKSTART.md` ga qarang.
