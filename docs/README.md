# Polyglot Voice — Documentation

**Real-time voice translation for code-switched speech — built for two phones and a laptop, on a zero-budget stack.**

Polyglot Voice is an open-source voice translator designed for speakers who mix languages mid-sentence (Hinglish, Vietnamese-English, and similar code-switched speech). Two people each run a lightweight client; a self-hosted backend listens, transcribes, translates, and speaks the translation back — with a human verification step before anything is said aloud.

> **Status: v0.1.0 — working portfolio build.** All eight build phases are implemented and tested. See [Status & Limitations](#status--limitations) for what is honestly *not* supported yet.

---

## Key Features

| Feature | Implementation |
|---|---|
| **Code-switched STT** | Silero VAD → faster-whisper (`small`, int8, CPU) locally; Groq `whisper-large-v3` as a cloud fallback when local inference fails or is too slow |
| **LLM translation with fallback** | Google Gemini 2.5 Flash (free tier) primary; Groq Llama 3.3 70B fallback; automatic provider cooldown after repeated rate limits |
| **Verification safety loop** | Every translation produces a **back-translation** shown to the speaker *before* speech is synthesized. Speech is never generated without explicit approval (or an expiring countdown the speaker can cancel) |
| **Cultural tone injection** | Per-language JSON configs with a three-point tone axis (`formal` / `business` / `casual`) — one file per language, not per tone |
| **Local TTS** | Piper (ONNX) voices, downloaded on demand from a declarative voice manifest |
| **Two-phone sessions** | In-memory rooms with join secrets, server-assigned participant tokens, a half-duplex turn policy, and bounded reconnect grace |
| **Provider abstraction** | All STT/LLM/TTS providers sit behind abstract interfaces with factory construction — swap providers without touching calling code |
| **Cross-platform client** | One Flutter codebase (Android today) for recording, review UI, countdown ring, and playback |

## Language Support

Support differs per pipeline stage and is reported as-is:

| Stage | Supported | Notes |
|---|---|---|
| Speech-to-text | Multilingual (Whisper `small`) | No language forcing by default, so code-switched audio is processed as-is |
| Translation | `ja`, `ko`, `hi`, `vi`, `en` | Cultural tone configs exist for all five |
| Text-to-speech | `en`, `hi`, `ja` | Vietnamese/Korean voices are **deliberately unsupported** until a real Piper voice is validated — text translation still works for them |
| Back-translation (review) | `en` review language in v1 demos | Configurable per participant |

## Repository Layout

```
polyglot-voice/
├── client/                  # Flutter app (single screen, services, widgets)
│   └── lib/{screens,services,widgets}
├── core-engine/             # FastAPI backend
│   ├── app/
│   │   ├── main.py          # REST endpoints + /ws + room reaper
│   │   ├── config.py        # All settings from env/.env (never hardcoded keys)
│   │   └── services/        # vad, stt_router, llm_orchestrator, tts_router,
│   │                        # session_manager, ws_handler, ws_protocol, turn_state,
│   │                        # base_interfaces (abstract providers)
│   ├── models/              # voice_manifest.json + downloaded models (gitignored)
│   ├── mic_client.py        # Laptop microphone test client
│   └── requirements.txt
├── cultural-matrix/         # ja.json, ko.json, hi.json, vi.json, en.json + schema.json
├── docs/                    # This documentation
└── .github/workflows/ci.yml # Secret-free CI
```

## Quick Start (30-second version)

```powershell
# 1. Backend
cd core-engine
python -m venv venv && venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then add GEMINI_API_KEY / GROQ_API_KEY
venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Laptop mic test (no phone needed)
venv\Scripts\python mic_client.py --lang ja --tone business

# 3. Phone client
cd ..\client
flutter run                 # point it at ws://<your-laptop-ip>:8000/ws
```

Full instructions: **[Setup Guide](setup-guide.md)**.

## Documentation Index

| Document | Purpose |
|---|---|
| [Architecture](architecture.md) | System design, data flow, room model, provider fallback, tech rationale |
| [Setup Guide](setup-guide.md) | Local installation, environment variables, models, devices, troubleshooting |
| [API Reference](api-reference.md) | REST endpoints and the complete WebSocket protocol |
| [SETUP.md](SETUP.md) | Original condensed setup notes (superseded by the Setup Guide) |
| [API_KEYS.md](API_KEYS.md) | How to obtain Gemini/Groq keys and free-tier notes |
| [CLIENT_SETUP.md](CLIENT_SETUP.md) | Phone/laptop connectivity details, firewall and ngrok specifics |

## Status & Limitations

This is a portfolio-grade v1 with deliberate scope boundaries — stated plainly rather than hidden:

- **No accuracy claims.** Code-switched STT accuracy requires human review of real recordings; none is claimed here.
- **No latency promises.** Two LLM calls + local inference + user verification make sub-second end-to-end translation unrealistic on typical laptops. Per-stage timing logs exist so *you* can measure real numbers (see the API Reference → Timing section).
- **No authentication on REST/WS yet.** Room join secrets and participant tokens protect room access only. Do not expose the backend to the public internet.
- **Single-process, in-memory rooms.** One Uvicorn worker; restart clears all rooms. Horizontal scaling is explicitly out of scope.
- **Half-duplex turns.** One active room turn at a time — an intentional trade to avoid echo loops and simultaneous-speaker complexity.
- **Verification is a UX safeguard, not a correctness guarantee.** A good back-translation does not prove a good forward translation.

## License & Attribution

MIT-licensed. Third-party components (Whisper, Silero VAD, Piper, voices) carry their own licenses — Piper is GPL-3.0 and individual voices are CC-BY-SA. See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
