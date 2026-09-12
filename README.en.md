# Lab Assistant (实验助手)

> ⚠️ **Alpha / work in progress.** Built from scratch by one student for their own lab course,
> never tested by anyone else. Expect bugs and rough edges. Tinker at your own risk.

[中文说明 →](README.md)

**A voice + vision lab assistant you point your phone at.** Open the page in a mobile browser:
aim the camera at your equipment and ask out loud. It sees what you see, hears what you say,
walks you through the steps, and reads the answer aloud.

Self-hosted, single-file backend, runs on a 2C2G VPS. The only external deps are cloud APIs
(a chat model, an ASR model, a TTS voice).

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

## Architecture

```
phone browser ──HTTPS/WSS──> this service (single-file aiohttp) ──> cloud APIs
  · frame sampling, zoom, fullscreen     · /ask  → chat model (with images)
  · record → upload for transcription    · /stt /tts
  · browser-side VAD (auto mode)         · /docs → library (photos via vision OCR, video = frames + transcript)
  · voiceprint check on the server (~30MB ONNX)
```

**Heavy work stays in the phone browser** (sampling, VAD, recording, playback); the server only
forwards and calls APIs — each service idles below 1MB RSS.

## Run it

```bash
git clone <this-repo> && cd lab-assistant
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
bash scripts/fetch_vad_assets.sh        # browser-side VAD assets (auto mode)
cp .env.example .env && vi .env          # API keys
cp lab_context.example.md lab_context.md # your course equipment & experiments (optional)
./venv/bin/python lab_server.py
```

- Camera/mic need **HTTPS**. A self-signed cert is fine (tap "Advanced → Proceed" on the phone).
- Cert paths live in `lab_server.py` (cert.pem / key.pem); generate your own:
  `openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 3650 -subj "/CN=lab" -addext "subjectAltName=IP:192.168.1.10"`
- For long-running use, make it a systemd user service (`Restart=always`, `EnvironmentFile`, mode 600).

## Swapping models / providers

Everything goes through env vars (see `.env.example`): `LAB_MODEL` (chat), `LAB_OCR_MODEL`
(vision OCR), `LAB_ASR_MODEL` (speech-to-text). The defaults use DeepSeek + Alibaba DashScope
(both OpenAI-compatible); switching vendors means editing the base URLs and key variables
in `lab_server.py`.

## Known trade-offs

- **Turn-based** (not streaming): answers can take 3–9s; a cheap "filler line" covers the wait.
  Lower latency needs a realtime model.
- Auto mode downloads a ~2.9MB inference engine on first use (gzipped); on a bad link it may fail —
  it tells you and retries.
- Frame sampling adapts to clip length (every 2s ≤20s / 4s ≤60s / 8s beyond), at the official ~1 fps order of magnitude.
- No on-device inference and no tool-calling: at the bench you want to be told what to do, not have the agent do it.

## Roadmap / help wanted

- [ ] English UI + prompts (i18n)
- [ ] Drop-in support for other providers (OpenAI / Gemini / local Ollama)
- [ ] Better weak-network experience (resumable uploads, offline sampling)
- [ ] One-command deploy (docker-compose / systemd template)

Issues and PRs welcome. If you are a teacher or TA who wants to use this in your own course,
tell me what is missing.

## License

MIT
