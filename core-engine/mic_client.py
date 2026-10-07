"""Local microphone test client for Polyglot Voice (Phase 5).

Connects to the FastAPI WebSocket backend, captures microphone audio,
sends PCM16 chunks, and handles the review/approval flow.

Usage:
    python mic_client.py [--server ws://localhost:8000/ws] [--lang ja] [--tone business]

Requirements:
    pip install websockets sounddevice numpy
"""
import argparse
import asyncio
import json
import logging
import sys
import time
import wave
import io
from pathlib import Path

try:
    import websockets
except ImportError:
    print("ERROR: websockets not installed. Run: pip install websockets")
    sys.exit(1)

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    print("WARNING: sounddevice not installed. Audio capture disabled.")
    print("Install with: pip install sounddevice numpy")
    AUDIO_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_MS = 100  # Send 100ms chunks
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 1600 samples


class MicClient:
    """Microphone client that talks to the Polyglot Voice backend."""
    
    def __init__(self, server_url: str, target_language: str, tone: str, review_language: str):
        self.server_url = server_url
        self.target_language = target_language
        self.tone = tone
        self.review_language = review_language
        
        self.recording = False
        self.capture_enabled = True
        self.audio_queue: asyncio.Queue = asyncio.Queue()
        self.utterance_id: str = ""
        self.countdown_mode = False
        # Client-side latency tracking (README section 11 measurements)
        self.review_received_at: float = 0.0
        self.approve_sent_at: float = 0.0
        
    async def run(self):
        """Main entry point: connect, start capture, handle messages."""
        logger.info(f"Connecting to {self.server_url}...")
        
        async with websockets.connect(self.server_url) as ws:
            # 1. Wait for ready
            ready = json.loads(await ws.recv())
            if ready.get("type") != "ready":
                logger.error(f"Expected ready, got: {ready}")
                return
            logger.info(f"Connected! Session: {ready['session_id']}")
            
            # 2. Start capture in background
            if AUDIO_AVAILABLE:
                capture_task = asyncio.create_task(self._capture_audio())
            else:
                capture_task = None
                logger.warning("Audio capture unavailable. Using simulated audio.")
                sim_task = asyncio.create_task(self._simulate_audio())
            
            # 3. Start audio sender
            sender_task = asyncio.create_task(self._send_audio(ws))
            
            # 4. Message handling loop
            receiver_task = asyncio.create_task(self._receive_messages(ws))
            
            # 5. Wait for user to quit (Ctrl+C)
            try:
                await receiver_task
            except websockets.exceptions.ConnectionClosed:
                logger.info("Connection closed by server.")
            finally:
                if capture_task:
                    capture_task.cancel()
                if not AUDIO_AVAILABLE:
                    sim_task.cancel()
                sender_task.cancel()
                self._stop_capture()
    
    async def _capture_audio(self):
        """Capture microphone audio and put chunks in the queue."""
        logger.info(f"Starting microphone capture ({SAMPLE_RATE} Hz, mono, 16-bit)...")
        logger.info("Speak now! Press Ctrl+C to stop.")
        
        loop = asyncio.get_running_loop()
        
        def audio_callback(indata, frames, time_info, status):
            """Called by sounddevice from its own thread."""
            if self.capture_enabled and self.recording:
                # Copy audio data as int16
                chunk = (indata[:, 0] * 32767).astype(np.int16).tobytes()
                loop.call_soon_threadsafe(
                    self.audio_queue.put_nowait, chunk
                )
        
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            callback=audio_callback
        ):
            # Keep stream open until cancelled
            while True:
                await asyncio.sleep(0.1)
    
    async def _simulate_audio(self):
        """Simulate audio capture when sounddevice is unavailable."""
        logger.info("Simulating 2 seconds of audio...")
        self.recording = True
        
        # Generate 2 seconds of tone as simulated speech
        t = np.linspace(0, 2.0, SAMPLE_RATE * 2, endpoint=False)
        audio = 0.3 * np.sin(2 * np.pi * 440 * t)
        samples = (audio * 32767).astype(np.int16).tobytes()
        
        # Send in chunks
        chunk_bytes = CHUNK_SIZE * 2  # 2 bytes per sample
        for i in range(0, len(samples), chunk_bytes):
            chunk = samples[i:i + chunk_bytes]
            await self.audio_queue.put(chunk)
            await asyncio.sleep(CHUNK_MS / 1000)
        
        logger.info("Simulated audio complete.")
    
    async def _send_audio(self, ws):
        """Send queued audio chunks to the server."""
        while True:
            chunk = await self.audio_queue.get()
            try:
                await ws.send(chunk)
            except websockets.exceptions.ConnectionClosed:
                break
    
    def _stop_capture(self):
        """Stop microphone capture."""
        self.recording = False
        self.capture_enabled = False
        logger.info("Capture stopped.")
    
    async def _receive_messages(self, ws):
        """Handle all messages from the server."""
        self.recording = True
        
        # Send start message
        await ws.send(json.dumps({
            "type": "start",
            "target_language": self.target_language,
            "tone": self.tone,
            "review_language": self.review_language
        }))
        logger.info(f"Turn started: target={self.target_language}, tone={self.tone}")
        
        while True:
            try:
                raw = await ws.recv()
            except websockets.exceptions.ConnectionClosed:
                logger.info("Server closed connection.")
                break
            
            # Binary = synthesized audio
            if isinstance(raw, bytes):
                if self.approve_sent_at > 0:
                    delivery_ms = (time.perf_counter() - self.approve_sent_at) * 1000.0
                    logger.info(f"TIMING-CLIENT turn={self.utterance_id} stage=delivery approve_to_audio_ms={delivery_ms:.0f}")
                    self.approve_sent_at = 0.0
                logger.info(f"Received {len(raw)} bytes of audio.")
                self._stop_capture()  # Disable capture during playback
                await self._play_audio(raw)
                continue
            
            # JSON message
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON: {raw[:100]}")
                continue
            
            msg_type = msg.get("type", "")
            
            if msg_type == "processing":
                logger.info("... processing audio ...")
                self._stop_capture()
                
            elif msg_type == "review":
                self.utterance_id = msg.get("utterance_id", "")
                self.review_received_at = time.perf_counter()
                logger.info("=" * 60)
                logger.info("REVIEW TRANSLATION")
                logger.info("=" * 60)
                logger.info(f"Transcript:       {msg['transcript']}")
                logger.info(f"Translation:      {msg['translated_text']}")
                logger.info(f"Back-translation: {msg['back_translation']}")
                logger.info("=" * 60)
                
                # Countdown or manual approval
                if self.countdown_mode:
                    window = msg.get("verification_window_ms", 800) / 1000
                    logger.info(f"Auto-approving in {window}s...")
                    await asyncio.sleep(window)
                    await self._approve(ws)
                else:
                    await self._prompt_user(ws)
                    
            elif msg_type == "turn_end":
                reason = msg.get("reason", "unknown")
                logger.info(f"Turn ended: {reason}")
                if reason in ("approved", "cancelled", "error"):
                    # Ask if user wants another turn
                    loop = asyncio.get_running_loop()
                    again = await loop.run_in_executor(
                        None, lambda: input("\nStart another turn? (y/n): ").strip().lower()
                    )
                    if again == "y":
                        self.recording = True
                        self.capture_enabled = True
                        await ws.send(json.dumps({
                            "type": "start",
                            "target_language": self.target_language,
                            "tone": self.tone,
                            "review_language": self.review_language
                        }))
                        logger.info("New turn started. Speak now!")
                    else:
                        logger.info("Goodbye!")
                        break
                        
            elif msg_type == "error":
                logger.error(f"Server error [{msg.get('code')}]: {msg.get('message')}")
                
            else:
                logger.debug(f"Unknown message: {msg}")
    
    async def _prompt_user(self, ws):
        """Prompt the user to approve or cancel."""
        loop = asyncio.get_running_loop()
        choice = await loop.run_in_executor(
            None,
            lambda: input("\n[A]pprove, [C]ancel? ").strip().lower()
        )
        
        if choice.startswith("a"):
            await self._approve(ws)
        else:
            await self._cancel(ws)
    
    async def _approve(self, ws):
        """Send approval for TTS synthesis."""
        review_wait_ms = 0.0
        if self.review_received_at > 0:
            review_wait_ms = (time.perf_counter() - self.review_received_at) * 1000.0
        logger.info("Approved! Synthesizing speech...")
        logger.info(
            f"TIMING-CLIENT turn={self.utterance_id} stage=review_wait "
            f"review_wait_ms={review_wait_ms:.0f}"
        )
        self.approve_sent_at = time.perf_counter()
        await ws.send(json.dumps({
            "type": "approve",
            "utterance_id": self.utterance_id
        }))
    
    async def _cancel(self, ws):
        """Send cancellation."""
        logger.info("Cancelled.")
        await ws.send(json.dumps({
            "type": "cancel",
            "utterance_id": self.utterance_id
        }))
    
    async def _play_audio(self, wav_bytes: bytes):
        """Play received WAV audio through the speakers."""
        if not AUDIO_AVAILABLE:
            # Save to file instead
            out_path = Path("received_audio.wav")
            out_path.write_bytes(wav_bytes)
            logger.info(f"Audio saved to {out_path} (sounddevice not available)")
            return
        
        try:
            # Parse WAV
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                sample_rate = wf.getframerate()
                channels = wf.getnchannels()
                frames = wf.readframes(wf.getnframes())
            
            # Convert to numpy
            audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            if channels > 1:
                audio = audio.reshape(-1, channels)
            
            logger.info(f"Playing audio ({len(audio)} samples @ {sample_rate} Hz)...")
            sd.play(audio, samplerate=sample_rate)
            sd.wait()  # Block until playback finishes
            logger.info("Playback complete.")
            
        except Exception as e:
            logger.error(f"Playback failed: {e}")
            # Save to file as fallback
            out_path = Path("received_audio.wav")
            out_path.write_bytes(wav_bytes)
            logger.info(f"Audio saved to {out_path}")
        finally:
            # Re-enable capture after playback
            self.capture_enabled = True
            self.recording = True


def main():
    parser = argparse.ArgumentParser(description="Polyglot Voice microphone client")
    parser.add_argument("--server", default="ws://localhost:8000/ws", help="WebSocket server URL")
    parser.add_argument("--lang", default="ja", help="Target language (ja, hi, ko, vi, en)")
    parser.add_argument("--tone", default="business", choices=["formal", "business", "casual"])
    parser.add_argument("--review-lang", default="en", help="Review language for back-translation")
    parser.add_argument("--countdown", action="store_true", help="Auto-approve after countdown")
    args = parser.parse_args()
    
    client = MicClient(
        server_url=args.server,
        target_language=args.lang,
        tone=args.tone,
        review_language=args.review_lang
    )
    client.countdown_mode = args.countdown
    
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()

