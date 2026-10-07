# Third-Party Notices

Polyglot Voice integrates open-source projects and free-tier services. Each
component keeps its own license and attribution requirements. This repository's
code is MIT-licensed (see `LICENSE`), but the components below are governed by
their own terms. **Do not assume every model or voice uses this repository's
license** — check each before redistribution.

## Runtime components

| Component | Role | License | Source |
|---|---|---|---|
| FastAPI / Uvicorn | Backend framework/server | MIT / BSD-3 | https://fastapi.tiangolo.com |
| Silero VAD | Voice activity detection | MIT | https://github.com/snakers4/silero-vad |
| faster-whisper (CTranslate2) | Local speech-to-text | MIT | https://github.com/SYSTRAN/faster-whisper |
| Whisper models | STT model weights | MIT | https://github.com/openai/whisper |
| Piper TTS | Local text-to-speech | **GPL-3.0** | https://github.com/rhasspy/piper |
| Piper voices | Voice models | Per-voice — see each voice's Hugging Face page (many MIT/CC-0, some restrict commercial use) | https://huggingface.co/rhasspy |
| Flutter | Mobile client SDK | BSD-3 | https://flutter.dev |
| record | Flutter mic capture | MIT | https://pub.dev/packages/record |
| audioplayers | Flutter audio playback | MIT | https://pub.dev/packages/audioplayers |
| web_socket_channel | Flutter WebSocket | BSD-3 | https://pub.dev/packages/web_socket_channel |
| permission_handler | Flutter permissions | MIT | https://pub.dev/packages/permission_handler |

## Cloud services (free tier, optional fallbacks)

| Service | Role | Terms to review |
|---|---|---|
| Google Gemini API (Gemini 2.5 Flash) | Translation LLM | Google APIs Terms of Service; free-tier content may be used to improve Google products — confirm current terms for your region |
| Groq (Whisper large-v3, Llama 3.3) | STT / LLM fallback | Groq Terms; review data-use and rate-limit policies |

Voice/model manifests in `core-engine/models/voice_manifest.json` record the
download source and license reference for every voice this project uses. When
you add a voice, add its license/attribution entry there.

## Suggested voice attribution for the bundled manifest

- `en_US-lessac-medium` — Lessac voice (Piper samples), see its HF page for terms.
- `hi_IN-medium`, `ja_JP-medium` — community voices; check each page for
  attribution requirements and usage restrictions before public demos or
  redistribution.
