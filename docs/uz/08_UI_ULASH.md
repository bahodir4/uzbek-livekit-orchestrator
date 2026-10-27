# 08 — Orchestrator API: vazifasi va ishlatilishi

Bazaviy manzil: `http://127.0.0.1:8090` (ngrok orqali: `https://....ngrok-free.app`). Hamma javoblar JSON, boshqacha ko'rsatilmasa.

## Kirish (autentifikatsiya)

Ikki usul; ikkalasi ham `.env` dagi `ADMIN_USERNAME` / `ADMIN_PASSWORD` dan foydalanadi (parol ≥ 16 belgi):

- **Brauzer (UI):** `POST /api/login` cookie beradi (`voice_console_session`, 8 soat). Keyingi so'rovlar `credentials: 'same-origin'` bilan ketadi. **Yozuvchi so'rovlarda** (`POST/PUT/PATCH/DELETE`) `X-Voice-UI: 1` sarlavhasi shart. Cookie `SameSite=Strict`, shuning uchun UI `/api` ni o'z origin'i orqali proksilashi kerak.
- **Dastur/curl:** har so'rovga Basic-auth: `curl -u "$ADMIN_USERNAME:$ADMIN_PASSWORD" ...`.

## Endpoint'lar

### Kirish

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `POST /api/login` | Tizimga kirish, cookie olish | Tana: `{"username":"admin","password":"..."}` → `{"authenticated":true,"username":"admin"}` |
| `POST /api/logout` | Chiqish | Tanasiz → `{"authenticated":false}` |
| `GET /api/session` | Kirilganmi? | → `{"authenticated":true,"username":"admin"}` yoki `false` |

### Agentlar va sozlamalar

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `GET /api/experience` | Agentlar, tillar, TTS modellari, chegaralar ro'yxati | → `{"personas":[...],"languages":[...],"tts_models":["sayro","navoiy"],"limits":{...}}` |
| `GET /api/preferences` | Foydalanuvchi sozlamalarini o'qish | → `{"persona_id","language","tts_model","voice_enabled","rate","pitch"}` |
| `PUT /api/preferences` | Sozlamalarni saqlash | Tana: `{"persona_id":"sofiya","language":"uz","tts_model":"navoiy","voice_enabled":true,"rate":1,"pitch":1}` (`rate`, `pitch`: 0,5–2) |

Agent identifikatorlari: `sofiya` (kredit), `malika` (qo'llab-quvvatlash), `dilnoza` (uchrashuv), `zarina` (omonat).

### Suhbatlar

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `POST /api/conversations` | Yangi suhbat yaratish | Tana: `{"persona":"sofiya","language":"uz","tts_model":"navoiy","kind":"chat"}` (`kind`: `chat` yoki `call`) → `201`, suhbat obyekti (`id` bilan) |
| `GET /api/conversations` | Suhbatlar ro'yxati | → `{"items":[...]}` |
| `GET /api/conversations/{id}` | Bitta suhbat va uning xabarlari | → suhbat obyekti; topilmasa `404` |
| `PUT /api/conversations/{id}/messages` | Xabarni saqlash | Tana: `{"id":"m-1","role":"user","kind":"chat","text":"Salom","final":true}` (`role`: `user`/`agent`; `kind`: `chat`/`transcription`/`voice_note`/`system`; `id` shu harflardan: `a-zA-Z0-9:_-`) → `{"saved":true}` |
| `PATCH /api/conversations/{id}/call` | Qo'ng'iroq davomiyligini saqlash | Tana: `{"seconds":125}` → `{"saved":true}` |
| `DELETE /api/conversations/{id}` | Bitta suhbatni o'chirish | → `{"deleted":true}` |
| `DELETE /api/conversations` | Barcha suhbatlarni o'chirish | → `{"deleted_count":N}` |

### Qo'ng'iroq / chatga ulanish

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `POST /api/rooms` | LiveKit xonasini yaratish, agentni chaqirish, brauzer uchun token olish | Tana: `{"conversation_id":"<id>","model":"navoiy","persona":"sofiya","language":"uz","text_only":false,"speak_responses":true}` → `{"room","url","token","model","persona","language","conversation_id"}`. Token 30 daqiqa amal qiladi; `url` + `token` bilan LiveKit xonasiga ulaning. Chat uchun `text_only:true`. |

Shartlar: `conversation_id` mavjud suhbat bo'lishi va persona/til/model suhbatdagi bilan bir xil bo'lishi kerak (aks holda `409`); mikrofon (`text_only:false`) faqat `language:"uz"` bilan (aks holda `422`); faol TTS provayderi `dual_gateway` bo'lishi kerak.

### Ovoz

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `POST /api/tts/preview` | Ovozni oldindan tinglash (oqimli audio) | Tana: `{"text":"Assalomu alaykum","model":"navoiy","emotion":"warm"}` (matn ≤ 350 belgi) → xom PCM16, 24 kHz, mono (`application/octet-stream`) |
| `POST /api/voice-notes/transcribe` | Ovozli xabarni matnga aylantirish (STT, faqat o'zbekcha) | `multipart/form-data`, maydon `file` (`audio/webm`, `audio/ogg`, `audio/mp4`, `audio/wav`; ≤ 8 MB, ≤ 60 s) → `{"text":"...","language":"uz","duration_limited_seconds":60}` |

### Holat

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `GET /health/live` | Ishlayaptimi? (kalitsiz) | → `{"status":"alive"}` |
| `GET /api/overview` | STT, LLM, TTS xizmatlari erishilishi va faol provayderlar | → `{"services":{"stt":{"state":"online"},"llm":...,"tts":...},"active":{...},"agent":"..."}` (`state`: `online`/`error`/`offline`) |
| `GET /api/capabilities` | UI uchun imkoniyatlar shartnomasi (LiveKit mavzulari `lk.chat`, `lk.transcription`, TTS formati va h.k.) | → imkoniyatlar obyekti |

### Administrator (konfiguratsiya)

| Endpoint | Vazifasi | Qanday ishlatiladi |
|---|---|---|
| `GET /api/config` | Joriy konfiguratsiyani ko'rish (maxfiy qiymatlar yashirilgan) | → konfiguratsiya |
| `PATCH /api/settings` | Agent sozlamalarini o'zgartirish | Tana (hammasi ixtiyoriy): `{"temperature":0.35,"max_completion_tokens":200,"endpointing_min_delay":0.1,"interruption_enabled":true,"preemptive_generation":true,"greeting":"...","auto_greet":true}` |
| `POST /api/providers/activate` | Faol STT/LLM/TTS provayderini almashtirish | Tana: `{"category":"tts","provider":"dual_gateway"}` (`category`: `stt`/`llm`/`tts`) → `{"active":{...},"applies_to":"new_sessions_only"}`; noma'lum provayder `400` |
| `GET /docs`, `GET /openapi.json` | Swagger hujjatlari | Basic-auth yoki kirgan cookie bilan |

## Namuna: kirish va suhbat yaratish (curl)

```bash
set -a; . ./.env; set +a
API=http://127.0.0.1:8090

curl -s -u "$ADMIN_USERNAME:$ADMIN_PASSWORD" $API/api/overview

curl -s -u "$ADMIN_USERNAME:$ADMIN_PASSWORD" -X POST $API/api/conversations \
  -H 'Content-Type: application/json' \
  -d '{"persona":"sofiya","language":"uz","tts_model":"navoiy","kind":"chat"}'
```

## Xato kodlari

| Kod | Ma'nosi |
|---|---|
| `401` | Kirilmagan yoki login/parol noto'g'ri |
| `403` | Yozuvchi so'rovda `X-Voice-UI: 1` yo'q (cookie bilan ishlaganda) |
| `404` | Suhbat topilmadi |
| `409` | Suhbat sozlamalari o'zgarmaydi yoki faol TTS `dual_gateway` emas |
| `422` | Noto'g'ri qiymat (masalan, mikrofon uchun o'zbekcha bo'lmagan til; ovoz juda qisqa yoki tanib bo'lmadi) |
| `502` | LiveKit, TTS yoki STT xizmati javob bermadi |
| `503` | Admin yoki LiveKit kalitlari sozlanmagan |
