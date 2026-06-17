# IsoDAQ Signaling Server

Lightweight HTTP relay that lets two IsoDAQ Studio instances find each other
over the internet using a 6-digit code.  
**Actual serial data never passes through this server** — it only stores the
host's IP:port for ~1 hour so the viewer can connect directly.

## Deploy to Railway (free tier, ~2 min)

1. Fork or push this repo to GitHub.
2. Go to [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**.
3. Select the repo and point the **Root Directory** to `relay/`.
4. Railway auto-detects Python and uses `Procfile`. Click **Deploy**.
5. Copy the generated URL (e.g. `https://isodaq-relay-production.railway.app`).
6. In IsoDAQ Studio → **Edit → Preferences** → paste the URL into **Signaling server URL**.

## API

| Method | Path | Body / Response |
|--------|------|-----------------|
| `POST` | `/register` | `{"ip":"1.2.3.4","port":9876}` → `{"code":"481203"}` |
| `GET`  | `/lookup/481203` | `{"ip":"1.2.3.4","port":9876}` or 404 |
| `GET`  | `/health` | `{"status":"ok","sessions":<n>}` |

Sessions expire after 1 hour.  No database — in-memory only.
