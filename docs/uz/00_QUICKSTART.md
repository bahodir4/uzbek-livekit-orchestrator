# 00 — Standart o'rnatish: boshidan oxirigacha

Bu standart sozlama: **faqat o'zbek tili, Navoiy ovozi, to'rtta agent** (Sofiya, Malika, Dilnoza, Zarina). Har bir qadam batafsil qo'llanmaga havola qiladi. Barcha podlarni **bir xil RunPod datacenter**'ida joylashtiring. RunPod proksisi har bir so'rovga taxminan 0,4–0,5 soniya qo'shadi va javob kechikishining asosiy qismini aynan shu tashkil qiladi (worker noutbukda ishlaganda o'lchandi: gap tugashidan birinchi ovozgacha benchmark bo'yicha ≈ 2,4–2,8 soniya; vosita (tool) chaqirishni talab qiladigan javoblar bir necha soniya uzoqroq davom etadi).

| # | Qism | Pod / xost | Qo'llanma |
|---|---|---|---|
| 1 | LLM: vLLM orqali Gemma 4 31B | A100 SXM 80 GB, `/workspace` da ≥ 100 GB disk, port 8000 | `02_GEMMA_4_31B_VLLM.md` |
| 2 | STT: GigaAM | RTX 3090/4090 sinfi, port 8000 | `01_GIGAAM_STT_RUNPOD.md` |
| 3 | TTS: Navoiy + gateway | RTX 4090 24 GB, ≥ 30 GB disk, port 8000 | `03_NAVOIY_TTS_RUNPOD.md` |
| 4 | Orchestrator (worker + API) | Docker o'rnatilgan CPU xost yoki pod; eng yaxshisi — bir xil datacenter | `04_ORCHESTRATOR_WIRING.md` |
| 5 | Mijoz interfeysi (UI) | UI qayerda joylashtirilgan bo'lsa, o'sha yerda | `05_DUAL_GATEWAY_FRONTEND.md` |

Sayro (`03_SAYRO_TTS_RUNPOD_UPDATED.md`) **ixtiyoriy va standart sozlamaga kirmaydi**; uni o'tkazib yuboring.

## Kalitlar: to'rtta yangi kalit yarating

Har birini `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'` buyrug'i bilan yarating. Ularni hech qachon chatga tashlamang va gitga qo'shmang. Bir xil qiymat ham podda, ham orchestrator `.env` faylida turadi.

| O'zgaruvchi | Podning `.env` fayli | Orchestrator `.env` fayli |
|---|---|---|
| `VLLM_API_KEY` | LLM pod (muhit o'zgaruvchisi, `--api-key` emas) | `VLLM_API_KEY` |
| `STT_API_KEY` | STT podning `.env` fayli | `STT_API_KEY` |
| `TTS_API_KEY` | TTS podning `.env` fayli (gateway kaliti) | `TTS_API_KEY` |
| `NAVOIY_API_KEY` | TTS podning `.env` fayli (gateway'dan worker'ga, ichki) | kerak emas |

## Tartib va aniq buyruqlar

**1. LLM pod.** Bir marta o'rnating (02-hujjatning A bo'limi), keyin:

```bash
read -rs -p "VLLM_API_KEY: " VLLM_API_KEY; export VLLM_API_KEY; echo
nohup /workspace/start_llm.sh > /workspace/vllm.log 2>&1 &
tail -f /workspace/vllm.log          # kuting: Application startup complete
```

Ishga tushirish skriptida `--enable-auto-tool-choice --tool-call-parser gemma4 --reasoning-parser gemma4` bo'lishi shart (02-hujjat, B bo'limi). Ularsiz har bir javob `400 "auto" tool choice requires ...` xatosi bilan tugaydi.

**2. STT pod.**

```bash
cd /workspace && git clone https://github.com/bahodir4/gigaam-livekit-stt.git && cd gigaam-livekit-stt
bash scripts/runpod/install.sh          # keyin .env ichida STT_API_KEY ni kiriting
bash scripts/runpod/start-background.sh
curl -s http://127.0.0.1:8000/health/ready
```

**3. TTS pod.**

```bash
cd /workspace && git clone <TTS_REPO_URL> uzbek-dual-tts-backend && cd uzbek-dual-tts-backend
cp .env.example .env && nano .env       # TTS_API_KEY va NAVOIY_API_KEY ni kiriting
unset HF_HUB_ENABLE_HF_TRANSFER
./scripts/install_navoiy.sh && ./scripts/install_gateway.sh
./scripts/preflight.sh --role navoiy,gateway
./scripts/start_all.sh                  # avval Navoiy, keyin gateway'ni ishga tushiradi va nutq testini o'tkazadi
```

**4. Orchestrator.** `.env` faylini to'ldiring (`LIVEKIT_*`, `STT_API_KEY`, `VLLM_API_KEY`, `TTS_API_KEY`, `ADMIN_*`). `config/agent.yaml` ichida uchta pod manzilini kiriting (LLM manzili **`/v1` bilan tugaydi**; TTS va STT manzillari tugamaydi). Keyin:

```bash
docker compose up -d --build
docker compose logs -f worker api
```

**5. Hammasini orchestrator xostidan tekshiring** (kalitlar `.env` dan olinadi, hech narsa chop etilmaydi):

```bash
python scripts/bench_latency.py -n 4
```

U har bir bosqichni (tarmoq, LLM birinchi token, TTS birinchi audio, STT yakuniy matn) va taxminiy umumiy vaqtni chiqaradi. Har bir o'zgartirishdan oldin va keyin ishga tushiring.

## Salomatlik (health) endpoint'lari (`/health/live` dan boshqalariga kalit kerak)

| Xizmat | Endpoint | Kalit |
|---|---|---|
| LLM | `/v1/models` | `VLLM_API_KEY` |
| STT | `/health/ready` | `STT_API_KEY` |
| TTS gateway | `/health/ready` | `TTS_API_KEY` |
| Navoiy worker (pod ichida) | `/health/ready` | `NAVOIY_API_KEY` |

Kalitni `Authorization: Bearer <kalit>` ko'rinishida yuboring. `*.proxy.runpod.net` manzilidan `502` kelsa, proksi xizmatga ulana olmayapti: u hali yuklanmoqda, to'xtagan yoki pod ID o'zgargan.

## Pod qayta ishga tushgandan keyin

Yangi pod yaratganingizda pod ID va proksi manzillari o'zgaradi. Shuning uchun `config/agent.yaml` ni yangilang va worker'ni qayta ishga tushiring (`docker compose restart worker`). Modellar `/workspace` diskida qoladi, shuning uchun qayta ishga tushirish faqat yuqoridagi ishga tushirish buyruqlaridan iborat.

## Loglar

| Qayerda | Buyruq |
|---|---|
| LLM | `tail -f /workspace/vllm.log` |
| TTS gateway / Navoiy | `tail -f /workspace/gateway.log /workspace/navoiy.log` (yoki `start_all.sh` bilan ishga tushirilsa `/workspace/logs/`) |
| STT | `tail -f /workspace/gigaam-stt-logs/server.log` |
| Orchestrator | `docker compose logs -f worker api` |
