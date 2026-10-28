# 10 — Public server setup: nginx for the UI and the back end

Server: `voice-ai` (Ubuntu 24.04, nginx 1.24, certbot), public IP `170.168.60.136`.
**Mac** = a terminal on your computer. **Server** = the SSH session on `voice-ai`.

| Name | Role | nginx file | Forwards to |
|---|---|---|---|
| `voice.neurotech.uz` | UI (what users open) | `deploy/nginx/ui-domain.conf` | UI container `127.0.0.1:8080`; `/api/` goes to the API domain |
| `api-voice.neurotech.uz` | Back-end API only | `deploy/nginx/orchestrator.conf` | orchestrator `127.0.0.1:8090` |

The browser only talks to `voice.neurotech.uz`. The login cookie is `SameSite=Strict`, so the UI domain must forward `/api/` itself.

## 0. DNS (do this first)

Both names need an A record to `170.168.60.136` in the `neurotech.uz` zone (hostmaster.uz). Check on the **server**:

```bash
for ns in $(dig +short NS neurotech.uz); do
  for h in voice api-voice; do echo -n "$h @$ns -> "; dig +short $h.neurotech.uz @$ns; done
done
```

Every line must print `170.168.60.136`. If a line is empty, stop: certbot would fail, and Let's Encrypt blocks a name after 5 failed attempts per hour.

## 1. Copy the files (Mac)

```bash
cd ~/Desktop/uzbek-livekit-orchestrator
scp deploy/nginx/orchestrator.conf deploy/nginx/ui-domain.conf \
  sardor_islamov011@170.168.60.136:~/workspace/uzbek-livekit-orchestrator/deploy/nginx/
```

## 2. Start the UI container (Server)

```bash
cd ~/workspace/in.voice-back-frontend
# .env must contain:  VITE_API_URL=/   API_UPSTREAM=https://api-voice.neurotech.uz   PORT=8080
sudo docker compose up -d --build
curl -s -o /dev/null -w "UI %{http_code}\n" http://127.0.0.1:8080/       # 200
```

The container starts even if the API name has no DNS yet; `/api/` answers 502 until it does.

## 3. Back end running (Server)

```bash
cd ~/workspace/uzbek-livekit-orchestrator
sudo docker compose up -d
curl -s http://127.0.0.1:8090/health/live                                # {"status":"alive"}
```

## 4. Certificates (Server, once per name)

```bash
sudo systemctl stop nginx
sudo certbot certonly --standalone -d voice.neurotech.uz
sudo certbot certonly --standalone -d api-voice.neurotech.uz
sudo systemctl start nginx
sudo ls /etc/letsencrypt/live/            # both names must be listed
```

If certbot fails, read the error and fix the cause; do not retry blindly.

## 5. Install the nginx configs (Server)

Only after step 4. The configs reference the certificate files, so installing them earlier makes `nginx -t` fail.

```bash
cd ~/workspace/uzbek-livekit-orchestrator/deploy/nginx
sudo cp orchestrator.conf ui-domain.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Secure cookie (Server)

Behind HTTPS the session cookie must be `Secure`:

```bash
cd ~/workspace/uzbek-livekit-orchestrator
sed -i 's/^UI_SECURE_COOKIE=.*/UI_SECURE_COOKIE=true/' .env
sudo docker compose up -d --force-recreate api
```

## 7. Final checks (Mac)

```bash
curl -s https://api-voice.neurotech.uz/health/live                                   # {"status":"alive"}
curl -s -o /dev/null -w "docs %{http_code}\n" https://api-voice.neurotech.uz/docs   # 403
curl -s -o /dev/null -w "UI %{http_code}\n"   https://voice.neurotech.uz/            # 200
curl -s https://voice.neurotech.uz/api/session                                       # {"authenticated":false,...}
```

Then open https://voice.neurotech.uz and log in.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `nginx -t` fails with missing `fullchain.pem` | Configs installed before the certificate exists: remove them from `/etc/nginx/conf.d/`, do step 4 first |
| certbot: `NXDOMAIN` or "secondary validation" | A record missing, or one nameserver still serves the old zone: re-run the step 0 check |
| `/api/...` returns 502 on the UI domain | `api-voice.neurotech.uz` has no DNS record yet, or the API container is down |
| Login works but the session is lost | `UI_SECURE_COOKIE` is not `true` on HTTPS, or the UI is not opened via `voice.neurotech.uz` |
| Worker `Restarting (132)` | The VM CPU (`QEMU Virtual CPU 2.5+`) has no AVX/AVX2. Ask the provider for CPU type `host` and do a full VM stop/start. Voice calls need this; the API and UI do not |
| Port 443 unreachable from outside | nothing listens until step 5; if it still fails, the provider firewall must allow inbound TCP 443 |
| `Restarting (1)` on the UI container | old `nginx.conf.template` (needs DNS at startup): `git pull` in `in.voice-back-frontend` and rebuild |

After everything works, bind the UI container to localhost only by using `'127.0.0.1:${PORT:-8080}:80'` in the UI's `docker-compose.yml`, so port 8080 is not reachable without HTTPS.
