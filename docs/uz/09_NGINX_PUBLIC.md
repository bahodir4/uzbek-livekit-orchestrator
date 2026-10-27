# 09 — Orchestrator va UI'ni nginx bilan internetga ochish

Ikki domen, ikkita konfiguratsiya fayli:

| Domen | Nima | Fayl |
|---|---|---|
| `api-voice.backend.uz` | orchestrator **API** (faqat API, sahifalar yo'q) | `deploy/nginx/orchestrator.conf` |
| `voice.neurotech.uz` | mijoz **UI** + `/api/` ni API domeniga yo'naltiradi | `deploy/nginx/ui-domain.conf` |

Brauzer faqat `voice.neurotech.uz` bilan gaplashadi. Kirish cookie'si `SameSite=Strict`, shuning uchun brauzer UI va `/api` ni **bir domenda** ko'rishi shart: `voice.neurotech.uz` serveri `/api/` ni `https://api-voice.backend.uz` ga o'zi (serverdan serverga) uzatadi. Ikkala domen bitta serverda bo'lishi ham mumkin.

## Nima qiladi

| Qism | Vazifasi |
|---|---|
| `80 → 443` | HTTP ni HTTPS ga yo'naltiradi (`/.well-known/acme-challenge/` sertifikat uchun ochiq) |
| TLS + sarlavhalar | TLS 1.2/1.3, HSTS, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy: microphone=(self)` |
| `/api/login` | Har IP uchun daqiqasiga 5 urinish (+5 burst), ortig'iga `429`. Cheklov **UI serverida** qo'llanadi (u haqiqiy foydalanuvchi IP'sini ko'radi) |
| `/api/` | Soniyasiga 20 so'rov (burst 40); `proxy_buffering off`, 180 s o'qish vaqti (TTS audio oqimi uchun) |
| `/docs`, `/openapi.json`, `/admin/` | API domenida internetdan **yopiq** (`403`) |
| API domenida `/` | `404` (sahifa yo'q) |
| UI domenida `/` | UI build'i (`/var/www/neurotech-ui`), SPA uchun `index.html` ga qaytadi |

## 1. DNS

Ikki **A-yozuv** qo'shing: `api-voice.backend.uz` → API serveri IP'si, `voice.neurotech.uz` → UI serveri IP'si (`backend.uz` hozir `167.235.222.200` ga qaraydi). Tekshirish: `nslookup api-voice.backend.uz`, `nslookup voice.neurotech.uz`. Yozuvlar tarqalmaguncha sertifikat olinmaydi. Brandmauer: faqat **80 va 443** ochiq (8090 ni ochmang; `docker-compose.yml` uni faqat `127.0.0.1` ga e'lon qiladi).

## 2. API serveri (`api-voice.backend.uz`)

```bash
docker compose up -d --build                              # API: 127.0.0.1:8090
sudo apt install -y nginx certbot
sudo cp deploy/nginx/orchestrator.conf /etc/nginx/conf.d/orchestrator.conf
sudo systemctl stop nginx && sudo certbot certonly --standalone -d api-voice.backend.uz && sudo systemctl start nginx
sudo nginx -t && sudo systemctl reload nginx
curl -s https://api-voice.backend.uz/health/live          # {"status":"alive"}
```

UI serveri API serveriga murojaat qilganda API serveri hamma so'rovni UI serverining manzilidan ko'radi. Cheklovlar real foydalanuvchilarni sanashi uchun `orchestrator.conf` dagi ikki qatorni oching va `UI_SERVER_IP` ni UI serverining IP'siga almashtiring:

```
set_real_ip_from  UI_SERVER_IP;
real_ip_header    X-Forwarded-For;
```

## 3. UI serveri (`voice.neurotech.uz`)

```bash
sudo apt install -y nginx certbot
sudo mkdir -p /var/www/certbot /var/www/neurotech-ui
sudo cp deploy/nginx/ui-domain.conf /etc/nginx/conf.d/ui-domain.conf
sudo systemctl stop nginx && sudo certbot certonly --standalone -d voice.neurotech.uz && sudo systemctl start nginx
# UI repozitoriysida (in.voice-back-frontend): npm run build -> dist/ papkasi, keyin:
sudo rsync -a --delete ../in.voice-back-frontend/dist/ /var/www/neurotech-ui/
sudo nginx -t && sudo systemctl reload nginx
curl -s https://voice.neurotech.uz/api/session            # {"authenticated":false,...}
```

Sertifikat avtomatik yangilanishi uchun: `sudo systemctl enable --now certbot.timer` (yangilangach `sudo systemctl reload nginx`).

## Ochishdan oldingi majburiy tekshiruv

| Nima | Qiymat |
|---|---|
| API serveri `.env` → `UI_SECURE_COOKIE` | **`true`** (lokalda `false` bo'lishi mumkin; `false` bo'lsa kirish cookie'si `Secure` belgisisiz beriladi). O'zgartirgach: `docker compose up -d --force-recreate api` |
| `ADMIN_PASSWORD` | uzun va noyob (≥ 16 belgi) |
| `LIVEKIT_URL` | brauzerdan ochiladigan `wss://...` manzil |
| Almashtirilgan kalitlar | chatga yoki skrinshotga tushgan hamma kalitni yangilang |
| `curl -sI https://api-voice.backend.uz/docs` | `403` qaytarishi kerak |

## Eslatmalar

- Sinovdan o'tgan (vaqtinchalik sertifikat bilan, sizning ishlayotgan API'ngizga qarshi): ikkala fayl sintaksisi (nginx 1.31), UI va statik fayllar, `/api` uzatilishi, login cookie'si, `/api/conversations` yaratish, UI domenida login cheklovi (`429`), API domenida `/` → `404`. Haqiqiy sertifikat va domenlar bilan sinab ko'rilmagan.
- UI va API bitta domenda bo'lsin desangiz: `orchestrator.conf` oxiridagi izohdagi UI blokini oching.
- Vaqtinchalik, domensiz ochish uchun ngrok (`ngrok http 5173`, `08_UI_ULASH.md`).
