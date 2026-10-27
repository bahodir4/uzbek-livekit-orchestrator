# 09 — Publishing the orchestrator and the UI with nginx

Two domains, two config files:

| Domain | What | File |
|---|---|---|
| `api-voice.neurotech.uz` | orchestrator **API** only (no pages) | `deploy/nginx/orchestrator.conf` |
| `voice.neurotech.uz` | customer **UI** + forwards `/api/` to the API domain | `deploy/nginx/ui-domain.conf` |

Browsers only talk to `voice.neurotech.uz`. The login cookie is `SameSite=Strict`, so the browser must see UI and `/api` on one domain: the UI server forwards `/api/` to `https://api-voice.neurotech.uz` server to server. Both domains may share one machine.

| Part | What it does |
|---|---|
| `80 → 443` | redirects to HTTPS (`/.well-known/acme-challenge/` stays open) |
| TLS + headers | TLS 1.2/1.3, HSTS, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy: microphone=(self)` |
| `/api/login` | 5 tries/minute per IP (+5 burst), then `429`; applied **on the UI server**, where real client IPs are visible |
| `/api/` | 20 requests/s (burst 40), `proxy_buffering off`, 180 s read timeout (TTS preview streams audio) |
| `/docs`, `/openapi.json`, `/admin/` | closed to the internet on the API domain (`403`) |
| `/` | API domain: `404`. UI domain: the UI build in `/var/www/neurotech-ui` (SPA fallback) |

## 1. DNS

A record for `api-voice.neurotech.uz` → API server IP, and for `voice.neurotech.uz` → UI server IP. Check with `nslookup`. Certificates cannot be issued before the records exist. Firewall: only ports 80 and 443.

## 2. API server

```bash
docker compose up -d --build                              # API on 127.0.0.1:8090 only
sudo apt install -y nginx certbot
sudo ufw allow 80,443/tcp                                  # if ufw is active
sudo systemctl stop nginx
sudo certbot certonly --standalone -d api-voice.neurotech.uz  # needs the DNS record, and port 80 reachable from the internet
sudo cp deploy/nginx/orchestrator.conf /etc/nginx/conf.d/orchestrator.conf   # ONLY after the certificate exists (the config needs its files)
sudo nginx -t && sudo systemctl start nginx
curl -s https://api-voice.neurotech.uz/health/live
```

Requests from the UI server arrive from its address. So that limits here count real users, uncomment `set_real_ip_from UI_SERVER_IP;` and `real_ip_header X-Forwarded-For;` in `orchestrator.conf` and put the UI server's IP in.

## 3. UI server

```bash
sudo apt install -y nginx certbot
sudo mkdir -p /var/www/certbot /var/www/neurotech-ui
sudo ufw allow 80,443/tcp                                  # if ufw is active
sudo systemctl stop nginx
sudo certbot certonly --standalone -d voice.neurotech.uz
sudo cp deploy/nginx/ui-domain.conf /etc/nginx/conf.d/ui-domain.conf       # ONLY after the certificate exists
sudo rsync -a --delete ../in.voice-back-frontend/dist/ /var/www/neurotech-ui/    # `dist/` after `npm run build` in in.voice-back-frontend
sudo nginx -t && sudo systemctl start nginx
curl -s https://voice.neurotech.uz/api/session
```

Renew certificates with `sudo systemctl enable --now certbot.timer`.

## If nginx will not start

- `unknown directive "http2"`: nginx older than 1.25.1 (Ubuntu 24.04 has 1.24). The files in `deploy/nginx/` already use `listen 443 ssl http2;`; on an older copy replace `listen 443 ssl;` by `listen 443 ssl http2;` (and the `[::]` line) and delete the `http2 on;` line.
- `cannot load certificate ... fullchain.pem`: the certificate was not issued yet. Remove the config (`sudo rm /etc/nginx/conf.d/orchestrator.conf`), start nginx, fix the certificate, then copy the config again.

## Before going public

- API server `.env`: **`UI_SECURE_COOKIE=true`** (it may be `false` locally; then the login cookie has no `Secure` flag). After changing: `docker compose up -d --force-recreate api`.
- `ADMIN_PASSWORD` long and unique (≥ 16 characters); `LIVEKIT_URL` a `wss://` address reachable from browsers; rotate any key that appeared in a chat or screenshot.
- `curl -sI https://api-voice.neurotech.uz/docs` must answer `403`.

Tested (throw-away certificates, against the running API): syntax of both files (nginx 1.31), UI and static files, `/api` forwarding, login cookie, creating a conversation through the UI domain, the login rate limit on the UI domain (`429`), `/` → `404` on the API domain. Not tested with real certificates and domains.
