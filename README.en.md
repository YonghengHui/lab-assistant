# Lab Assistant (实验助手)

[中文说明 →](README.md)

![vibe coded](https://img.shields.io/badge/vibe--coded-%F0%9F%A7%83-ff69b4)
![status](https://img.shields.io/badge/status-alpha-orange)
![license](https://img.shields.io/badge/license-MIT-blue)
![check](https://github.com/YonghengHui/lab-assistant/actions/workflows/check.yml/badge.svg)

> ⚠️ **Alpha / work in progress.** Built from scratch by one student for their own lab course,
> never tested by anyone else. Expect bugs and rough edges. Tinker at your own risk.
>
> 🧃 **Vibe coded**: most of the code was written by **pair-programming with an AI** (vibe coding).
> The features are real and tested on a real phone against real APIs, but the code grew
> conversation-by-conversation — inconsistent naming and structure. **Don't use it as an
> engineering reference**; treat it as a working prototype.

**A voice + vision lab assistant you point your phone at.** Open the page in a mobile browser:
aim the camera at your equipment and ask out loud. It sees what you see, hears what you say,
walks you through the steps, and reads the answer aloud.

<p align="center">
  <img src="assets/shot-main.png" width="330" alt="Asking while working, on a phone">
</p>

Self-hosted, single-file backend, runs on a 2C2G VPS. The only external deps are cloud APIs
(a chat model, an ASR model, a TTS voice). The server build also needs **ffmpeg** installed
(video sampling / transcription) and **node + npm** (only to run `scripts/fetch_vad_assets.sh` once).

## 30-second start (no server needed)

Grab [`standalone.html`](standalone.html) — **one file, almost all features**
(document library / photo Q&A / video Q&A / voiceprint / spoken answers):

1. Put it on any static host (GitHub Pages / Vercel / object storage), or serve it locally:
   `python3 -m http.server 8000` then open `http://localhost:8000/standalone.html`
2. Open it → **⚙️ paste one API key** (defaults to Alibaba DashScope; one key covers chat + vision + ASR)
3. Open it on your phone (**https or localhost required** — browsers block the camera otherwise)
   → snap a photo or record a clip → just ask

<p align="center">
  <img src="assets/shot-docs.png" width="330" alt="Document library: the active experiment's docs go into context">
</p>

Need cross-device sharing or a shared document library → use the [self-hosted version](#run-it).

## What it costs

Only model API money; a 2C2G box (or your laptop) is enough:

- **See + answer**: a few thousand tokens per question — with a cheap model (e.g. `qwen-plus`)
  that is **a fraction of a cent**; dozens of questions a day ≈ a coffee per month
- **Transcription** (video narration / voice input): billed per audio second, tiny
- **Spoken answers**: the standalone version uses your phone's built-in TTS — **free**

## Where it is useful

Nothing in the code is tied to a specific school or course — course material lives in an
external `lab_context.md` plus a runtime document library. The same shell works for:

- **Any lab course** (physics / electronics / chemistry / biology): put your equipment notes
  into `lab_context.md`, photograph the manual, and students can ask while they work
- **Repair / assembly guidance**: point the camera at a machine and ask "where does this cable go"
- **Hands-on training**: a trainee operates the instrument while the assistant watches and corrects
- Any hands-busy situation where someone needs to be guided while looking at something

> This repo is a **scrubbed shell**: code and config samples only. No keys, voiceprints,
> sessions, course material, certificates or model weights.

## Features

- **Manual / auto mode**: push-to-talk, or hands-free (browser-side VAD)
- **It can see**: camera stays on, the frame is attached when you ask; full-screen preview and 1×/2×/3× zoom
- **Video switch**: while on, 1 frame/s is pushed (unchanged frames skipped, ~zero traffic when idle);
  after you turn it off, questions still carry the last captured frames
- **Send a video in the chat** (🎬): record while narrating; frames are sampled and your speech is
  transcribed — made for "I'm stuck, look at this"
- **Document library**: photos / PDF / Word / PPT / **video** go straight into context, grouped per experiment
- **Voiceprint (optional)**: "only my voice" (sherpa-onnx + CAM++, CPU-only, ~30MB)
- Multi-session history, editable system prompt, 4 thinking levels, optional web search, filler lines

There is also a **:8900 realtime page** (full-duplex voice, relaying Qwen-Omni-Realtime): `app.py` + `index.html`.

## No server? Use the standalone build (`standalone.html`)

One file, no backend, no install — drop it on any static host and open it on your phone:

- **Document library** in the browser (IndexedDB): photos / videos / **PDF / Word / PPT** / txt,
  grouped per experiment; the active group goes into context automatically
  (photos → vision OCR; PDF → parsed locally, scanned pages rendered for the model;
  Word/PPT → unzipped locally; video → 4 frames + narration transcript)
- **Voiceprint "only my voice"** (optional): CAM++ runs *in the browser*, bit-for-bit aligned with
  the Python sherpa-onnx pipeline (measured cosine 0.9999+). Enroll 3 short clips; speech that is
  not you is refused. Needs `campplus_sv_advanced.onnx` (28MB) next to the HTML as `./campplus.onnx`,
  or a URL in settings.
- **Multi-session history**, editable prompt, **spoken answers** (phone TTS), optional web search,
  fullscreen / zoom, frame buffering
- Everything (docs, chats, voiceprint, API key) lives **in this device's browser**; no server of mine
  is involved

**Versus the server build**, what's missing:

- **Cross-device sharing** — that needs a shared home, i.e. a server;
- plus three server-only features: **auto mode (browser-side VAD)**, **4 thinking levels**, **filler lines**.

Two honest caveats: importing a PDF/Word pulls pdf.js / jszip from third-party CDNs
(jsdelivr / unpkg / bootcdn) and voiceprint inference pulls onnxruntime-web; with web search enabled
your question is sent to Tavily. The **API key sits in same-origin localStorage** (no server touches it,
but a hijacked CDN script could read it) — localize those libs or use the server build if that matters.

## FAQ

**Camera doesn't open?** Browsers require **https or localhost**; `file://` is blocked in some browsers.

**"Auto mode" says the engine failed to load?** It downloads a ~2.8MB inference engine on first use
(gzipped). On a weak link it fails — **tap it again and it retries** (no reload needed).

**Where does the voiceprint model come from?** By default `./campplus.onnx` next to the HTML
([CAM++ zh/en general model](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh_en_16k-common_advanced), 28MB).

**Can I use OpenAI / Gemini / a local model?** Yes. The standalone build has **provider presets**
(DashScope / OpenAI / DeepSeek / SiliconFlow / Zhipu / Kimi / local Ollama / custom) that fill in the
base URL and model names for you — and you can use one provider for chat and another for vision/ASR.
For the server build, edit `.env` (see the provider list in `.env.example`).

**Is my data uploaded?** Standalone: docs/chats/voiceprint/key stay in your browser; only the
current question (text + this round's frames) goes to the model API you configured — plus CDN code
fetches for PDF/Word parsing and Tavily if web search is on (see above). Same for the server build,
plus your own box in the middle; server logs don't record question text by default.

**Why not just use an existing phone app?** Because "hands on the instrument, asking while working"
needs three things: your course material in context, the ability to point a camera at the equipment,
and short precise answers. General-purpose apps don't do that — this code can be adapted per course.

**Any demo?** Not yet (the author uses it in their own lab). If you get it running, open an issue and
tell me your scenario.

## Architecture

```
phone browser ──HTTPS/WSS──> this service (single-file aiohttp) ──> cloud APIs
  · frame sampling, zoom, fullscreen     · /ask  → chat model (with images; TTS audio comes back in the response)
  · record → upload for transcription    · /stt  → speech-to-text
  · browser-side VAD (auto mode)         · /docs → library (photos via vision OCR, video = frames + transcript)
  · voiceprint check on the server (~30MB ONNX)
```

**Heavy work stays in the phone browser** (sampling, VAD, recording, playback); the server only
forwards and calls APIs — each service idles under ~20MB RSS (measured 16–19MB).

## Run it

Prerequisites: `python3` + venv, system **ffmpeg** (frame sampling / transcription), **node + npm**
(for the asset script below). Slow PyPI? add a mirror: `pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt`.

```bash
git clone https://github.com/YonghengHui/lab-assistant && cd lab-assistant
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
bash scripts/fetch_vad_assets.sh        # browser-side VAD assets (auto mode; needs npm)
cp .env.example .env && vi .env          # API keys (read automatically at startup)
cp lab_context.example.md lab_context.md # your course equipment & experiments (optional)

# Camera/mic need HTTPS. Self-sign a cert **in the repo root**, with YOUR LAN / Tailscale IP
# (copying the example IP makes the phone reject the cert):
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 3650 \
  -subj "/CN=lab" -addext "subjectAltName=IP:192.168.1.10"

./venv/bin/python lab_server.py
```

The startup log line tells you `http` vs `https`, the bind address, and whether auth is on — check it
first when the phone camera refuses to open (with no cert.pem/key.pem it silently falls back to HTTP,
where the camera is unavailable).

- Port already taken → change `LAB_PORT` in `.env`; to reach it from a phone, set `LAB_HOST` to your LAN/Tailscale IP.
- For long-running use, make it a systemd user service (`Restart=always`, `EnvironmentFile`, mode 600).

### Exposing it to the internet? Three options

1. **Recommended: a mesh VPN** (Tailscale / WireGuard) — bind to the VPN interface only; the service
   is invisible from the internet and needs no extra auth.
2. **Set an access token**: `LAB_TOKEN=<long random string>` in `.env` (e.g. `openssl rand -hex 24`).
   Pages, APIs and static assets then require it: the browser gets a minimal login page (cookie, 30 days);
   scripts can use `?token=xxx` or `Authorization: Bearer xxx`; `/health` stays open for probes.
   **Empty = no auth (default)**, behaviour is unchanged.
3. **Put a reverse proxy with auth in front** (Nginx/Caddy basic auth, Cloudflare Access, …).

⚠️ Binding `LAB_HOST=0.0.0.0` publicly **without** a token hands your API credit, document library and
voiceprint enrollment to anyone. The voiceprint is a "don't misfire" convenience, **not access control**.

## Swapping models / providers

Everything goes through env vars (see `.env.example`): `LAB_MODEL` (chat), `LAB_OCR_MODEL`
(vision OCR), `LAB_ASR_MODEL` (speech-to-text). The defaults use DeepSeek + Alibaba DashScope
(both OpenAI-compatible).

To switch **base URLs**, edit the constants at the top of `lab_server.py` (all overridable via env):
`LAB_DEEPSEEK_BASE` (used when the model name starts with `deepseek`), `LAB_VISION_BASE`,
`LAB_ASR_BASE`, plus `LAB_VISION_KEY` / `LAB_ASR_KEY` if vision/ASR live on another account.

## Known trade-offs

- **Turn-based** (not streaming): answers can take 3–9s; a cheap "filler line" covers the wait.
  Lower latency needs a realtime model.
- Auto mode downloads a ~2.8MB inference engine on first use (gzipped); on a bad link it may fail —
  it tells you and retries.
- Frame sampling adapts to clip length (every 2s ≤20s / 4s ≤60s / 8s beyond, max 12 frames; videos sent
  inside a chat are capped at 6). The standalone build always uses 4 frames.
- No on-device inference and no tool-calling: at the bench you want to be told what to do, not have the agent do it.
- Logs record lengths, not question/transcript text, by default (`LAB_LOG_CONTENT=1` to change that).

## Roadmap / help wanted

- [x] English UI + prompts (i18n: auto by browser language, switchable; add a table in `static/i18n.js` for a new language)
- [x] Standalone build: provider presets (DashScope / OpenAI / DeepSeek / SiliconFlow / Zhipu / Kimi / local Ollama / custom)
- [x] Server build: screen sharing / cast; **tablet & landscape still missing**
- [x] Optional access token (`LAB_TOKEN`, off by default)
- [ ] Server build: pluggable providers (today you edit `LAB_VISION_BASE` etc. by hand)
- [ ] Better weak-network experience (resumable uploads, offline sampling)
- [ ] One-command deploy (docker-compose / systemd template)
- [ ] A demo page / video (needs someone to run it first)

## Come hack on it (contributions welcome)

**Status**: it runs and the author uses it daily, but it has **only ever been tested in one course**.
Help of any kind is welcome — code, bug reports, "I want X", or just telling me where you use it.

**Especially wanted (claim one, or just open an issue saying "I'll do it"):**

- 🔌 **Other providers**: OpenAI / Gemini / local Ollama
- 🌐 **More languages**: add one table to `static/i18n.js` — a single-commit job
- 📶 **Weak-network UX**: resumable uploads, auto-retry, offline sampling
- 📱 **Tablet / landscape** support, screen sharing
- 🧑🏫 **Real classroom trial**: take it to one lab session and file an issue with
  "where it was dumb / wrong" — **more valuable than code**
- 🐛 **Just use it and complain**: device, browser, what broke

**Start here:** [`CONTRIBUTING.md`](CONTRIBUTING.md). Rough contributions are fine — an issue first is fine too.

## Changelog

- **2026-09-15** Fixed the blockers that stopped people getting started (`.env` is now actually read,
  `fetch_vad_assets.sh` works, missing VAD assets no longer crash the server, `/ask` with images no
  longer 500s, document ids no longer collide); added an **optional access token**; replaced PyMuPDF
  (AGPL) with pypdfium2 (permissive)
- **2026-09-12** Standalone build became **feature-complete** (local document library / sessions /
  TTS / web search / **in-browser voiceprint**); 🎬 record-and-ask video in chat
- **2026-09-12** Document library supports video; auto-mode VAD loads on demand, gzipped (11MB → 2.8MB)
- **2026-09-12** Bilingual UI (zh/en); scrubbed public shell

## License

MIT. Third-party components: see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
