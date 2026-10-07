# Polyglot Voice — Setup Guide

Step-by-step local installation on Windows (the development target), with the environment-variable reference and troubleshooting. For what happens after setup, see the [API Reference](api-reference.md); for *why* the system is shaped this way, see [Architecture](architecture.md).

> **Prerequisite callouts**
> - API keys are **required for translation** (Gemini, and optionally Groq). See [API_KEYS.md](API_KEYS.md) for how to obtain them on free tiers.
> - STT and TTS run **locally** — no keys, but the first run downloads models (Whisper) / voices (Piper) from Hugging Face. Budget ~5–10 minutes and a few hundred MB on first launch.
> - A phone is optional: `mic_client.py` exercises the entire pipeline from your laptop.

## 1. Prerequisites

| Requirement | Version / Notes |
|---|---|
| OS | Windows 10/11 (commands below are PowerShell; macOS/Linux users: substitute `venv\Scripts\` → `venv/bin/`, `copy` → `cp`) |
| Python | 3.10 or 3.11 (3.12+ works but emits third-party deprecation warnings) |
| Flutter SDK | 3.x stable — only needed for the phone client |
| Android phone | Optional; USB debugging or same-Wi-Fi install |
| Ports | 8000 (backend) free |

## 2. Backend Installation

```powershell
cd core-engine
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Then edit `core-engine\.env` and fill in your keys (see §3). Start the server:

```powershell
.\venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify it's alive: open `http://localhost:8000/health` — expect `{"status":"ok",...}`. The interactive schema (OpenAPI/Swagger) is auto-served at `/docs` by FastAPI.

> **Heads-up:** the first `/ws` or STT test run downloads the Whisper `small` model; the first Japanese/Hindi synthesis downloads the matching Piper voice. Both are cached under `core-engine/models/` (gitignored) and happen **once**.

## 3. Environment Variables Reference

All settings live in `core-engine/app/config.py` and are read from the process environment or `core-engine/.env`. **API keys are never hardcoded anywhere.**

| Variable | Default | Purpose |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address when run via `python app/main.py` |
| `DEBUG` | `true` | Uvicorn reload flag in `__main__` path |
| `GEMINI_API_KEY` | *(empty)* | Google Gemini key — primary translation provider |
| `GROQ_API_KEY` | *(empty)* | Groq key — LLM fallback **and** cloud STT fallback |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Translation model id |
| `GROQ_LLM_MODEL` | `llama-3.3-70b-versatile` | Fallback LLM model id |
| `GROQ_STT_MODEL` | `whisper-large-v3` | Cloud STT model id |
| `WHISPER_MODEL` | `small` | Local faster-whisper model size |
| `WHISPER_DEVICE` | `cpu` | Local inference device |
| `WHISPER_COMPUTE_TYPE` | `int8` | Local quantization |
| `STT_LOCAL_TIMEOUT_SECONDS` | `3.0` | Bound before falling back to cloud STT |
| `ALLOW_CLOUD_STT` | `true` | Set `false` to forbid audio leaving the machine |
| `VERIFICATION_WINDOW_MS` | `800` | Countdown before auto-approval (client UX default) |
| `ROOM_EMPTY_EXPIRY_SECONDS` | `300` | Delete empty rooms after this |
| `PLAYBACK_TIMEOUT_SECONDS` | `30` | Release a room turn if playback is never acknowledged |
| `RECONNECT_GRACE_SECONDS` | `120` | Rejoin window after disconnect |
| `PROVIDER_MAX_CONSECUTIVE_RATE_LIMITS` | `2` | Rate-limit strikes before cooldown |
| `PROVIDER_COOLDOWN_SECONDS` | `60` | How long a cooled-down provider is skipped |

> **Privacy callout:** `ALLOW_CLOUD_STT=false` + no Groq usage keeps *audio* entirely local. Translated *text* still goes to Gemini (or Groq) — that is the trade-off for free LLM translation, and free-tier terms may permit provider-side data use. See README §13.

## 4. Models & Voices

| Asset | Source | When it downloads | Land |
|---|---|---|---|
| faster-whisper `small` (int8, CT2) | Hugging Face (auto) | First local STT request | `core-engine/models/` |
| Piper voice `en_US-lessac-medium` | `rhasspy/piper-voices` v1.0.0 | First English synthesis | `core-engine/models/` |
| Piper voice `hi_IN-medium` | `rhasspy/piper-voices` v1.0.0 | First Hindi synthesis | `core-engine/models/` |
| Piper voice `ja_JP-medium` (takumi) | `rhasspy/piper-voices` v1.0.0 | First Japanese synthesis | `core-engine/models/` |

The voice catalog is declarative: `models/voice_manifest.json` pins provider, files, sample rate (22050 Hz), download URLs, license (CC-BY-SA 4.0), and attribution. Download-on-demand happens **only** when a request needs an un-cached voice — never as a side effect of unrelated calls. To pre-fetch, just synthesize a short string per language via `TTSRouter`.

## 5. Flutter Client

```powershell
cd client
flutter pub get
flutter run          # with a phone connected (USB debugging) or an emulator
```

Runtime dependencies (deliberately minimal — built-in Flutter state management, no extra frameworks):

| Package | Role |
|---|---|
| `web_socket_channel` | WebSocket transport to the backend |
| `permission_handler` | Microphone permission request |
| `record` | Raw PCM16 mono 16 kHz mic streaming |
| `audioplayers` | Playback of returned WAV bytes |

`android/app/src/main/AndroidManifest.xml` already declares `RECORD_AUDIO`, `INTERNET`, and `ACCESS_NETWORK_STATE`. On first run Android asks for mic permission; denial is shown in the app's status line — connection and review still work, recording does not.

## 6. One-Phone Test (Laptop Backend)

1. **Bind the backend to all interfaces:** `--host 0.0.0.0` (default in the command above).
2. **Find the laptop's LAN IP:** `ipconfig` → *IPv4 Address* (e.g. `192.168.1.42`).
3. **Firewall:** allow Python on private networks when Windows prompts, or pre-create the rule:
   ```powershell
   netsh advfirewall firewall add rule name="PolyglotVoice 8000" dir=in action=allow protocol=TCP localport=8000
   ```
4. **In the app:** enter `ws://192.168.1.42:8000/ws` → **Connect** (indicator turns green).
5. Choose target language (`ja`), review language (`en`), tone; tap the mic, speak, tap stop.
6. Watch **processing** → **review**: transcript, translation, and back-translation appear and the **countdown ring** starts (800 ms default — tap the ring to cancel). Approve, or let it expire, and the translated audio plays on the phone; the mic re-arms after playback finishes.

> **Warning — `localhost` on the phone means the phone.** `ws://localhost:8000` from a handset connects to the handset itself, never your laptop. Use the laptop's LAN IP over the same Wi-Fi, or a WSS tunnel (§7).
>
> Guest/corporate Wi-Fi networks with client isolation block phone↔laptop traffic — use the tunnel path if step 4 times out.

## 7. Two-Phone Test

1. Both phones on the same Wi-Fi as the laptop (or both through the WSS tunnel, §8).
2. **Phone A — create:** connect to `ws://<laptop-ip>:8000/ws`, open the room panel, **Create Room**. The app shows the `room_id` and `join_secret`.
3. **Phone B — join:** connect, enter the `room_id` and `join_secret`, **Join Room**. Phone A shows "Peer joined".
4. A speaks → A reviews (translation + back-translation) → A approves → **the audio plays on B only**. After B's playback finishes, the turn releases and B can speak (roles reverse).
5. While one turn is active, the other phone sees the busy state — this half-duplex policy prevents echo loops.

> **Note:** rooms are in-memory and single-worker. Restarting the backend clears all rooms; run exactly one Uvicorn process.

## 8. Tunnel Access (ngrok)

When same-Wi-Fi is blocked (client isolation) or you need WSS:

```powershell
ngrok http 8000
```

Use the forwarded URL in the app as `wss://<subdomain>.ngrok-free.app/ws` (note **wss**). Realities to expect:

- **Free tunnel URLs change every restart** — re-enter the URL in the app each session.
- Free-tier bandwidth/connection limits exist and can change; the ngrok browser interstitial may need an initial visit.
- Never put Gemini/Groq keys in the Flutter app. The app's optional *demo token* field is a placeholder for backend access control (the backend accepts `?token=` and ignores it in v1).

## 9. Verification Checklist (Manual)

| Scenario | Expected behavior |
|---|---|
| Mic permission denied | Status line explains it; connection/review still work, recording does not |
| App backgrounded during verification countdown | Countdown cancels; turn is not auto-approved while away |
| Network drop during recording/playback | Disconnect indicator; reconnect with backoff resets turn state; no stale approval replay |
| Cancel during review | `turn_end reason=cancelled`; TTS call count is zero (server log confirms) |
| Server offline | Connect fails with a clear error and retry with backoff — no crash |

## 10. Running the Test Suites

```powershell
# Backend (all offline — fake providers, no keys, no model downloads)
cd core-engine
.\venv\Scripts\python -m pytest test_phase2.py test_phase5.py test_phase7.py -v

# Scaffold + config sanity
.\venv\Scripts\python test_phase1.py

# Flutter
cd ..\client
flutter analyze
flutter test

# Cultural matrix JSON validation (what CI runs)
.\venv\Scripts\python -c "import json,glob; [json.load(open(p, encoding='utf-8')) for p in glob.glob('../cultural-matrix/*.json')]; print('all configs valid')"
```

Live-provider tests (real Gemini/Groq keys, real model downloads) are **opt-in only** and never required by CI — see `test_phase2.py`'s guarded live section.

## 11. Troubleshooting

| Symptom | Cause → Fix |
|---|---|
| `/health` unreachable | Port in use → `netstat -ano \| findstr :8000`, kill or change `--port` |
| Phone can't connect on LAN | Client isolation / wrong IP → use the laptop's current IPv4 from `ipconfig`; check the firewall rule (§6.3); try the tunnel (§8) |
| `422 Unknown target language` | Language not in the allowlist (`ja`, `ko`, `hi`, `vi`, `en`) — the cultural-matrix file must exist |
| `502 Translation failed` | Both providers failed or are rate-limited/cooled-down — check keys in `.env`, wait out `PROVIDER_COOLDOWN_SECONDS` (60 s), verify quota in the provider consoles |
| No timing lines in the server console | They are INFO-level; run uvicorn without `--log-level warning`, or set `DEBUG=true` |
| First turn is very slow | Cold start: Whisper model / Piper voice downloading once — subsequent turns are warm |
| Flutter build fails on `record`/`audioplayers` | Run `flutter pub get`; ensure the Android min SDK matches package requirements (`flutter doctor` for toolchain issues) |
| Piper voice error for `ko`/`vi` | Expected — those TTS voices are not shipped until validated; text translation still works for them |

---

*Continue reading: [API Reference](api-reference.md) · [Architecture](architecture.md) · [Overview](README.md)*
