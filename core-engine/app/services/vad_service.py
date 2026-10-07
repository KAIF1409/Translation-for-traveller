"""Voice Activity Detection (VAD) service implementing Silero VAD ONNX inference."""
import numpy as np
import onnxruntime as ort
import urllib.request
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class VADService:
    """Service to load and manage the Silero VAD ONNX model."""
    
    def __init__(self):
        self.model_dir = Path(__file__).resolve().parent.parent.parent / "models"
        self.model_dir.mkdir(exist_ok=True)
        self.model_path = self.model_dir / "silero_vad.onnx"
        self.session = None
        self._ensure_model_downloaded()
        self._init_session()

    def _ensure_model_downloaded(self):
        """Downloads the Silero VAD ONNX model from GitHub if missing."""
        if not self.model_path.exists():
            logger.info("Silero VAD model not found locally. Downloading on-demand...")
            url = "https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "PolyglotVoice/1.0"})
                with urllib.request.urlopen(req) as resp, open(self.model_path, "wb") as out_file:
                    out_file.write(resp.read())
                logger.info(f"Silero VAD model downloaded to {self.model_path}")
            except Exception as e:
                logger.error(f"Failed to download Silero VAD model: {e}")
                raise RuntimeError(f"Could not retrieve Silero VAD model: {e}")


    def _init_session(self):
        """Initializes the ONNX runtime inference session."""
        try:
            self.session = ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"]
            )
            logger.info("Silero VAD ONNX Session initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize VAD ONNX session: {e}")
            raise e

    def create_stream_processor(self) -> "VADStreamProcessor":
        """Factory method to get a brand new isolated stream state processor."""
        return VADStreamProcessor(self.session)


class VADStreamProcessor:
    """Isolated VAD state processor for a single active stream."""
    
    def __init__(self, session: ort.InferenceSession):
        self.session = session
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        
        self.sample_rate = 16000
        self.threshold = 0.5
        self.min_speech_ms = 250
        self.trailing_silence_ms = 600
        
        self.audio_buffer = np.array([], dtype=np.float32)
        self.speech_started = False
        self.speech_frames = 0
        self.silence_frames = 0

    def process_chunk(self, chunk_bytes: bytes) -> bool:
        """Process an audio chunk (PCM16 mono 16kHz) and return whether speech is currently active."""
        if not chunk_bytes:
            return False
            
        pcm_data = np.frombuffer(chunk_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.audio_buffer = np.concatenate((self.audio_buffer, pcm_data))
        
        chunk_size = 512
        while len(self.audio_buffer) >= chunk_size:
            input_chunk = self.audio_buffer[:chunk_size]
            self.audio_buffer = self.audio_buffer[chunk_size:]
            
            input_tensor = np.expand_dims(input_chunk, axis=0)
            sr_tensor = np.array(self.sample_rate, dtype=np.int64)
            
            inputs = {
                "input": input_tensor,
                "state": self.state,
                "sr": sr_tensor
            }
            out, next_state = self.session.run(None, inputs)
            self.state = next_state
            
            prob = float(out[0][0])
            is_speech_frame = prob >= self.threshold
            
            if is_speech_frame:
                self.speech_frames += 1
                self.silence_frames = 0
            else:
                self.silence_frames += 1
                
            required_speech_chunks = (self.min_speech_ms * self.sample_rate) / (1000 * chunk_size)
            if not self.speech_started and self.speech_frames >= required_speech_chunks:
                self.speech_started = True
                
            required_silence_chunks = (self.trailing_silence_ms * self.sample_rate) / (1000 * chunk_size)
            if self.speech_started and self.silence_frames >= required_silence_chunks:
                self.speech_started = False
                self.speech_frames = 0
                
        return self.speech_started


