# 09 — Orchestrator va UI'ni nginx bilan internetga ochish

Ikki domen, ikkita konfiguratsiya fayli:

| Domen | Nima | Fayl |
|---|---|---|
| `api-voice.neurotech.uz` | orchestrator **API** (faqat API, sahifalar yo'q) | `deploy/nginx/orchestrator.conf` |
| `voice.neurotech.uz` | mijoz **UI** + `/api/` ni API domeniga yo'naltiradi | `deploy/nginx/ui-domain.conf` |

Brauzer faqat `voice.neurotech.uz` bilan gaplashadi. Kirish cookie'si `SameSite=Strict`, shuning uchun brauzer UI va `/api` ni **bir domenda** ko'rishi shart: `voice.neurotech.uz` serveri `/api/` ni `https://api-voice.neurotech.uz` ga o'zi (serverdan serverga) uzatadi. Ikkala domen bitta serverda bo'lishi ham mumkin.

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

Ikki **A-yozuv** qo'shing: `api-voice.neurotech.uz` → API serveri IP'si, `voice.neurotech.uz` → UI serveri IP'si. Tekshirish: `nslookup api-voice.neurotech.uz`, `nslookup voice.neurotech.uz`. Yozuvlar tarqalmaguncha sertifikat olinmaydi. Brandmauer: faqat **80 va 443** ochiq (8090 ni ochmang; `docker-compose.yml` uni faqat `127.0.0.1` ga e'lon qiladi).

## 2. API serveri (`api-voice.neurotech.uz`)

```bash
docker compose up -d --build                              # API: 127.0.0.1:8090
sudo apt install -y nginx certbot
sudo ufw allow 80,443/tcp                                  # ufw yoqilgan bo'lsa
sudo systemctl stop nginx
sudo certbot certonly --standalone -d api-voice.neurotech.uz  # DNS yozuvi bo'lishi va 80-port internetdan ochiq bo'lishi kerak
sudo cp deploy/nginx/orchestrator.conf /etc/nginx/conf.d/orchestrator.conf   # FAQAT sertifikat olingandan KEYIN (konfiguratsiya uning fayllarini talab qiladi)
sudo nginx -t && sudo systemctl start nginx
curl -s https://api-voice.neurotech.uz/health/live          # {"status":"alive"}
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
sudo ufw allow 80,443/tcp                                  # ufw yoqilgan bo'lsa
sudo systemctl stop nginx
sudo certbot certonly --standalone -d voice.neurotech.uz
sudo cp deploy/nginx/ui-domain.conf /etc/nginx/conf.d/ui-domain.conf       # FAQAT sertifikat olingandan KEYIN
# UI repozitoriysida (in.voice-back-frontend): npm run build -> dist/ papkasi, keyin:
sudo rsync -a --delete ../in.voice-back-frontend/dist/ /var/www/neurotech-ui/
sudo nginx -t && sudo systemctl start nginx
curl -s https://voice.neurotech.uz/api/session            # {"authenticated":false,...}
```

Sertifikat avtomatik yangilanishi uchun: `sudo systemctl enable --now certbot.timer` (yangilangach `sudo systemctl reload nginx`).

## nginx ishga tushmasa

- `unknown directive "http2"`: nginx 1.25.1 dan eski (Ubuntu 24.04 da 1.24). `deploy/nginx/` dagi fayllar allaqachon `listen 443 ssl http2;` ishlatadi; eski nusxada `listen 443 ssl;` ni (va `[::]` qatorini) `listen 443 ssl http2;` ga almashtiring, `http2 on;` qatorini o'chiring.
- `cannot load certificate ... fullchain.pem`: sertifikat hali olinmagan. Konfiguratsiyani olib tashlang (`sudo rm /etc/nginx/conf.d/orchestrator.conf`), nginx'ni ishga tushiring, sertifikatni to'g'rilang, keyin konfiguratsiyani qayta nusxalang.

## Ochishdan oldingi majburiy tekshiruv

| Nima | Qiymat |
|---|---|
| API serveri `.env` → `UI_SECURE_COOKIE` | **`true`** (lokalda `false` bo'lishi mumkin; `false` bo'lsa kirish cookie'si `Secure` belgisisiz beriladi). O'zgartirgach: `docker compose up -d --force-recreate api` |
| `ADMIN_PASSWORD` | uzun va noyob (≥ 16 belgi) |
| `LIVEKIT_URL` | brauzerdan ochiladigan `wss://...` manzil |
| Almashtirilgan kalitlar | chatga yoki skrinshotga tushgan hamma kalitni yangilang |
| `curl -sI https://api-voice.neurotech.uz/docs` | `403` qaytarishi kerak |

## Eslatmalar

- Sinovdan o'tgan (vaqtinchalik sertifikat bilan, sizning ishlayotgan API'ngizga qarshi): ikkala fayl sintaksisi (nginx 1.31), UI va statik fayllar, `/api` uzatilishi, login cookie'si, `/api/conversations` yaratish, UI domenida login cheklovi (`429`), API domenida `/` → `404`. Haqiqiy sertifikat va domenlar bilan sinab ko'rilmagan.
- UI va API bitta domenda bo'lsin desangiz: `orchestrator.conf` oxiridagi izohdagi UI blokini oching.
- Vaqtinchalik, domensiz ochish uchun ngrok (`ngrok http 5173`, `08_UI_ULASH.md`).
