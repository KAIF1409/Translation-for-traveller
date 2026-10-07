# Polyglot Voice

Real-time voice translation for **code-switched speech** (Hinglish,
Vietnamese-English, …) — built with a completely free / zero-budget stack.

Speak your natural mixed language; the app transcribes it, translates it with
cultural tone awareness, shows you a **back-translation to verify before it
speaks**, then voices the approved translation to the other participant.

> ⚠️ Portfolio/prototype status. Not a certified translation tool. Do not use
> for medical, legal, or safety-critical communication.

- **Client:** Flutter (one codebase, Android today)
- **Backend:** Python FastAPI + WebSockets, self-hosted on a laptop
- **STT:** Silero VAD + faster-whisper (local, CPU) → Groq Whisper fallback
- **LLM:** Google Gemini 2.5 Flash (free tier) → Groq Llama 3.3 fallback
- **TTS:** Piper (local, open-source)

## 1. What the app does

1. You speak in a code-switched language ("मुझे lagta है कि yeh kaam ho jayega").
2. Silero VAD detects the utterance; faster-whisper transcribes it locally.
3. Gemini Flash translates into the listener's language, following a
   per-language cultural tone profile (formal / business / casual).
4. A second LLM call back-translates the result into *your* review language.
5. You see the back-translation and get a short countdown (default 0.8 s, on
   the phone) to cancel if the meaning drifted. No speech is synthesized
   unless the turn is approved.
6. Piper TTS voices the approved translation to the other participant.

Two phones can pair into a session (Phase 7): each receives the other's
approved, translated audio; the speaker alone approves their own utterance.
All providers sit behind abstract interfaces (`app/services/base_interfaces.py`)
with automatic fallback. Keys are read **only** from environment variables
(`app/config.py`); `.env` is git-ignored.

## 2. Architecture diagram

```
┌──────────────┐  WS (PCM16/16k + JSON)  ┌──────────────────────────────────┐
│ Flutter app  │ ◄─────────────────────► │ FastAPI core-engine              │
│ (phone(s))   │                         │  ws_handler (turn state machine) │
│ record ▸     │  audio chunks           │    ├─ VADService (Silero)        │
│ playback ◂   │                         │    ├─ STTRouter                  │
└──────────────┘                         │    │   ├─ FasterWhisperSTT (local)│
                                         │    │   └─ GroqWhisperSTT (cloud)  │
                                         │    ├─ LLMOrchestrator            │
                                         │    │   ├─ GeminiProvider          │
                                         │    │   └─ GroqLLMProvider         │
                                         │    └─ TTSRouter (Piper, local)   │
                                         │  session_manager (rooms, tokens) │
                                         │  cultural-matrix/*.json (tones)  │
                                         └──────────────────────────────────┘
```

## 3. Demonstration

1. Start the backend (`#4`) and the phone app (`#5`).
2. Two phones join a room (`#9`).
3. Phone A speaks Hinglish; reviews the English back-translation; approves.
4. Phone B hears Japanese with business tone. Then B speaks; A hears Hindi.
5. Demo the **cancellation**: at the review ring, tap the ring — the listener
   never hears anything.
6. Demo the **fallback**: blank `GEMINI_API_KEY` in `.env`, restart, repeat —
   the backend automatically uses Groq Llama 3.3; logs show the provider used.

## 4. Exact Windows setup (backend)

Requirements: Windows 10/11, Python 3.10/3.11, ~2 GB disk for models.

```powershell
cd polyglot-voice\core-engine
python -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements.txt

Copy-Item .env.example .env   # then edit keys — see #6
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Health check: `curl http://localhost:8000/health` → `{"status": "ok", ...}`

Tests (no keys, no network — fake providers):

```powershell
.\venv\Scripts\python.exe test_phase1.py
.\venv\Scripts\python.exe -m pytest test_phase2.py test_phase5.py test_phase7.py -v
```

Live STT/TTS checks (download models; opt-in, local machine only):

```powershell
.\venv\Scripts\python.exe test_phase3.py <path\to\speech.wav>       # VAD+STT
.\venv\Scripts\python.exe test_phase4.py                            # TTS → WAV
.\venv\Scripts\python.exe mic_client.py --lang ja --tone business   # laptop mic E2E
```

Windows Firewall (first run prompts; allow on **private networks**), or:

```powershell
netsh advfirewall firewall add rule name="PolyglotVoice 8000" dir=in action=allow protocol=TCP localport=8000
```

## 5. Flutter setup (client)

Install Flutter stable: https://docs.flutter.dev/get-started/install/windows
(with Android Studio/SDK for Android builds).

```powershell
cd polyglot-voice\client
flutter pub get
flutter analyze
flutter test
flutter run          # phone via USB + USB debugging enabled
```

In-app configuration:

- **Server URL:** same Wi-Fi → `ws://<LAPTOP-IP>:8000/ws` (find the IP with
  `ipconfig`; `localhost` on the phone means the phone itself). Tunnel →
  `wss://<tunnel-host>/ws` (ngrok free URL changes each restart; limits can
  change — see `docs/CLIENT_SETUP.md`).
- **Demo access token:** optional placeholder for backend access. No provider
  keys ever go in the app.

## 6. API-key configuration

All keys live in `core-engine/.env` (git-ignored; template in `.env.example`).
Full walkthrough incl. where to get free keys: `docs/API_KEYS.md`.

| Variable | Required | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | for translation | Google AI Studio free tier |
| `GROQ_API_KEY` | fallback LLM / STT | Groq console free tier |
| `ALLOW_CLOUD_STT` | `false` default | master switch for sending audio to Groq |
| `DEMO_ACCESS_TOKEN` | optional | simple shared token for tunnel-exposed demos |

CI never needs keys — tests use deterministic fake providers.

## 7. Model / voice downloads

| Asset | Triggered by | Lands in | Source |
|---|---|---|---|
| Silero VAD (ONNX) | first VAD use | `core-engine/models/` | silero-vad repo |
| faster-whisper `small` int8 | first STT use | `core-engine/models/` | Hugging Face (Systran) |
| Piper voices | first TTS use per language | `core-engine/models/` | Hugging Face (rhasspy) — source + license recorded in `models/voice_manifest.json` |

Model binaries are git-ignored; only `voice_manifest.json` is committed. Each
first-use download is logged once and cached; no unexpected downloads occur
mid-request.

## 8. One-phone test

1. Backend running on the laptop (`#4`).
2. Phone on the same Wi-Fi → app Server URL `ws://<LAPTOP-IP>:8000/ws` → Connect.
3. Pick target language (e.g. Japanese), review language, tone.
4. Press record, speak, stop → review the back-translation.
5. Approve (button or ring expiry) → the phone plays the translated audio.

If same-Wi-Fi is blocked (router AP isolation), follow the ngrok/WSS steps in
`docs/CLIENT_SETUP.md`.

## 9. Two-phone test

1. Backend running; both phones connected to the backend (each gets a
   participant identity on connect).
2. Phone A sends `{"type": "create_room", ...}` → server returns a
   human-friendly `room_id` and a **high-entropy join secret**.
3. Phone B sends `{"type": "join_room", "room_id": ..., "join_secret": ...}`.
   A third phone is rejected (`room_full`); wrong secrets are rejected
   (`invalid_secret`). The room ID alone is never sufficient authentication —
   participants get server-issued IDs and tokens.
4. Each phone sets its **listening language** (what it hears), **review
   language** (back-translation), and outgoing **tone**.
5. A speaks → A alone approves → B hears the audio in B's listening language.
   B acknowledges playback (`playback_complete`) → the turn is released and
   B may speak. While one turn is active, the other start attempt gets a
   typed `busy` response (half-duplex policy: no echo loops).
6. Disconnects keep the participant rejoinable for a grace window
   (rejoin with participant ID + token); interrupted work is cancelled and
   old approvals/audio are never replayed.

*(The Flutter room join/creation UI is in the app; the wire protocol is also
scriptable with any WebSocket client.)*

## 10. Supported languages

| Capability | Working (tested) | Config present, untested | Not supported |
|---|---|---|---|
| STT (faster-whisper `small`, multilingual) | en; code-switched Hinglish & Vi-English **require human review of accuracy** — no accuracy claims are made | hi, ja, ko, vi | anything else not in faster-whisper's language set |
| Translation (Gemini 2.5 Flash / Groq Llama 3.3) | ja, hi, en (+ vi, ko configs loaded by tests) | — | not in `cultural-matrix/` |
| TTS (Piper) | en, hi, ja (voices in manifest) | — | vi, ko — **deliberately unsupported until a real voice is validated**; requests fail with a clear capability error rather than speaking with the wrong-language voice |

The cultural matrix (tone rules per language) lives in `cultural-matrix/*.json`
with a JSON schema; only allowlisted language codes can be loaded (never
arbitrary filesystem paths from user input).

## 11. Actual measured latency

Measured on: *(fill in your laptop CPU/RAM, phone model, Wi-Fi class)*

| Stage | Cold | Warm |
|---|---|---|
| VAD end-of-turn detection | — ms | — ms |
| STT (faster-whisper small int8, local) | — ms | — ms |
| Forward translation (Gemini 2.5 Flash) | — ms | — ms |
| Back-translation (second LLM call) | — ms | — ms |
| User review time | human | human |
| TTS (Piper) | — ms | — ms |
| Playback start after approval | — ms | — ms |

**How to measure:** each stage logs a duration line to the backend console
(provider name + timing, no content). Run the mic client
(`mic_client.py --lang ja`) with a stopwatch, or grep the logs.

There are **no published numbers here yet** — run the pipeline on your machine
and fill the table. We deliberately do **not** promise sub-second end-to-end
translation: two LLM calls, local inference, networking, and a human review
step make that unrealistic on typical laptops.

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| Phone can't connect on Wi-Fi | Use the laptop's LAN IP, not `localhost`; check firewall allows 8000 on private networks; router AP isolation may block peer-to-peer — use the ngrok tunnel instead |
| `Cannot call receive once a disconnect message has been received` in logs | Harmless legacy path sign; current build breaks the receive loop cleanly on disconnect — update to latest |
| STT returns empty/garbage | Ensure WAV/PCM is mono 16 kHz PCM16; check the VAD didn't classify all-silence (silence never reaches Whisper by design) |
| "Model not found" on first TTS use | First use downloads the voice (~30–70 MB); verify network or pre-download via `test_phase4.py` |
| Gemini 429 / rate limits | The orchestrator retries via Groq automatically; after repeated limits a short provider cooldown engages |
| Cloud STT seems ignored | `ALLOW_CLOUD_STT` defaults to `false` — audio never leaves the machine unless you opt in |
| Countdown ring expired before I reviewed | The window is 0.8 s by default (`VERIFICATION_WINDOW_MS` in config) — cancel with the ring, or raise the value |
| Tunnel URL stopped working | Free ngrok URLs rotate on restart — re-copy the URL into the app |

## 13. Privacy and free-tier limitations

- **Local-first:** VAD, STT (Whisper), and TTS (Piper) run entirely on your
  laptop. Audio never leaves the machine unless you explicitly enable
  `ALLOW_CLOUD_STT` (Groq Whisper fallback).
- **What leaves the laptop:** transcript *text* goes to Google Gemini (or
  Groq) for translation and back-translation. No conversation content is
  stored by the backend; transcripts/audio are kept out of logs by default
  (logs carry request IDs, provider names, timings, sanitized error codes).
- **Free-tier terms change.** Google's free-service documentation states
  content may be used to improve their products — review the current Google
  APIs Terms of Service and Groq terms for your region before relying on them.
- The verification loop is a UX safeguard, **not** a guarantee of translation
  correctness. Back-translation helps you *notice* drift; it cannot prevent it.

## 14. Known limitations

- Single Uvicorn worker with in-memory rooms (horizontal scaling explicitly
  out of scope for v1); restarts wipe all rooms.
- Half-duplex turns: one room turn at a time, released on playback ack.
- No speaker diarization, simultaneous interpretation, voice cloning, or
  conversation history (deliberate v1 exclusions — see the handoff plan).
- Piper voice quality varies by language; Japanese/Hindi voices are
  serviceable but not studio-grade.
- Code-switched STT accuracy varies by speaker/accent and needs human review
  per language pair; this repo makes **no accuracy claims**.
- Windows-focused setup docs; Linux/macOS should work with minor path changes
  but are untested.

## 15. Roadmap

- [ ] Validate and add Piper Vietnamese/Korean voices (then flip them to
      "supported" in `#10`)
- [ ] Process-based killable STT worker if measurements justify it
- [ ] Per-stage latency instrumentation surfaced in the app UI
- [ ] Optional on-device review history (still no server storage)
- [ ] iOS client configuration

## 16. License and third-party attribution

This repository's code: **MIT** (see `LICENSE`).
Third-party components and their licenses — including the note that **Piper
TTS is GPL-3.0 and individual Piper voices carry their own licenses** — are
catalogued in `THIRD_PARTY_NOTICES.md`. Model/voice download sources and
license references are recorded per voice in
`core-engine/models/voice_manifest.json`. Do not assume every model or voice
uses this repository's license.