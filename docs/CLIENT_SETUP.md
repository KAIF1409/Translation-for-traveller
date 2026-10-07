# Flutter Client Setup (Phase 6)

## Prerequisites

- **Flutter SDK 3.47.x (stable)** with Dart 3.13.x
  - Install: <https://docs.flutter.dev/get-started/install/windows>
  - If installed at `C:\src\flutter`, add `C:\src\flutter\bin` to your `PATH`.
- **Android toolchain** for the physical phone test: Android Studio (or
  command-line tools) + one accepted SDK license. Verify with `flutter doctor`.
- A physical Android phone with **USB debugging** enabled (Developer options),
  or `adb` over Wi-Fi.

## Run the app

```powershell
cd client
flutter pub get
flutter run                # with the phone connected via USB
```

## Phone → Laptop connectivity (test in this order)

### 1. Same Wi-Fi (plain ws://)

- Bind the backend to all interfaces (the default `HOST=0.0.0.0` in
  `core-engine/.env` already does this).
- Find the laptop's LAN IP:

  ```powershell
  ipconfig   # look for "IPv4 Address" under your Wi-Fi adapter
  ```

- In the app, set the server URL to `ws://<LAPTOP-IP>:8000/ws`.
  **`localhost` on the phone means the phone itself** — never use
  `ws://localhost:8000/ws` on the phone.
- Allow the port through **Windows Firewall** (first `uvicorn` run usually
  pops the firewall prompt; accept "Private networks"). To open it manually:

  ```powershell
  # Run as Administrator
  New-NetFirewallRule -DisplayName "PolyglotVoice 8000" -Direction Inbound `
    -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
  ```

- Phone and laptop must be on the same Wi-Fi network; some routers run
  "AP/client isolation" which blocks device-to-device traffic.

### 2. ngrok tunnel (wss://)

Useful when Wi-Fi isolation is enabled or for a stable demo URL.

```powershell
# Terminal 1: backend (bind to localhost is fine; ngrok connects out)
cd core-engine
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2: tunnel
ngrok http 8000
```

- Copy the forwarded URL, e.g. `https://a1b2.ngrok-free.app`, and in the app
  enter `wss://a1b2.ngrok-free.app/ws` (note **wss** for https tunnels).
- **Tunnel URLs change on every free-tier restart** — re-enter after restarts.
- Free ngrok shows an interstitial warning page for browser traffic; native
  WebSocket apps are usually unaffected, but free-tier limits (connection
  counts, bandwidth) **can change at any time**. Check ngrok's current terms.
- The app accepts both `ws://` and `wss://` URLs and rejects anything else.

### Demo access token

- **Never put Gemini/Groq keys in the Flutter app.** All provider keys stay in
  `core-engine/.env` on the laptop.
- The app has an optional "Demo access token" field. If set, it is appended to
  the WebSocket URL as `?token=<value>`. The backend does not enforce it yet
  (Phase 8 adds authenticated tunnel exposure); treat it as a placeholder for
  the Phase 7/8 security work.

## What the app does (one phone flow)

1. Enter server URL → **Connect** → indicator turns green (`ready`).
2. Pick target/review languages and tone.
3. Press the mic button — the app requests **microphone permission** on first
   use and streams **PCM16 mono 16 kHz** binary frames (raw PCM, never
   AAC/M4A relabeled; unsupported devices report an explicit format error).
4. End-of-speech (backend VAD) → `processing` → `review` shows transcript,
   translation, and back-translation.
5. A **cancellable countdown ring** (default 800 ms, from the server) starts
   *after* the review is displayed. Tap the ring to cancel, or wait — expiry
   counts as approval. Manual **Approve / Cancel** buttons appear when the
   countdown is not active.
6. On approval the backend synthesizes speech and the phone plays the
   returned WAV; capture stays disabled during playback.

## Tests

```powershell
cd client
dart format lib test          # formatting
flutter analyze               # static analysis (must be clean)
flutter test                  # unit + widget tests (21 tests)
```

- `test/turn_state_service_test.dart` — turn lifecycle, one-active-turn rule,
  single-approval rule, cancel-after-approve rejection, countdown expiry and
  cancellation semantics (pure Dart logic, no plugins).
- `test/countdown_ring_test.dart` — ring expiry fires exactly once, tap
  cancels, remaining time decreases (uses an injected fake Stopwatch because
  real `Stopwatch` ignores the test clock).
- `test/widget_test.dart` — app smoke test.

## Manual checks for the physical-phone test

- Microphone permission **denied**: the status line explains it and recording
  does not start; nothing is sent to the backend.
- App **backgrounded during verification**: countdown is cancelled (no
  auto-approval while unseen); capture is stopped.
- **Disconnect during recording/playback**: connection indicator returns to
  Offline; no approval is replayed after reconnecting (approvals are
  one-shot per utterance).
- Slow network: processing state persists until backend responds; the
  turn cannot be double-started meanwhile.
