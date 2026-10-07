"""STT Router implementing local faster-whisper primary and Groq cloud fallback."""
import io
import asyncio
import logging
import concurrent.futures
from typing import Optional, Dict, Any
from app.config import settings
from app.services.base_interfaces import BaseSTTProvider
from faster_whisper import WhisperModel
from groq import AsyncGroq

logger = logging.getLogger(__name__)

class STTRouter(BaseSTTProvider):
    """STT Router with local faster-whisper primary and Groq cloud fallback."""
    
    def __init__(self):
        self.model = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._local_lock = asyncio.Lock()
        
        # Initialize Groq client if key is configured
        if settings.GROQ_API_KEY:
            self.groq_client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            self.groq_available = True
        else:
            self.groq_client = None
            self.groq_available = False

    def _load_local_model(self):
        """Loads faster-whisper model lazily on first call."""
        if self.model is None:
            logger.info(f"Loading local Whisper model: {settings.WHISPER_MODEL} on {settings.WHISPER_DEVICE}...")
            try:
                self.model = WhisperModel(
                    settings.WHISPER_MODEL,
                    device=settings.WHISPER_DEVICE,
                    compute_type=settings.WHISPER_COMPUTE_TYPE
                )
                logger.info("Local Whisper model loaded successfully.")
            except Exception as e:
                logger.error(f"Failed to load local Whisper model: {e}")
                raise e

    def _run_local_transcribe(self, audio_data: bytes, language_code: Optional[str]) -> str:
        """Synchronous CPU worker method for local faster-whisper."""
        self._load_local_model()
        audio_file = io.BytesIO(audio_data)
        # Use transcription mode, not translate. Auto-detect language if None.
        segments, info = self.model.transcribe(
            audio_file,
            beam_size=5,
            language=language_code,
            task="transcribe"
        )
        # Consume segments list lazily in the thread to avoid blocking async loops
        text_segments = [s.text for s in segments]
        return " ".join(text_segments).strip()

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000, language: Optional[str] = None) -> Dict[str, Any]:
        """Transcribe audio with fallback logic."""
        if not audio_bytes:
            raise ValueError("Audio bytes to transcribe cannot be empty.")
            
        # Try local first
        local_success = False
        transcript = ""
        provider = "local-whisper"

        # Check if the local single-worker slot is currently occupied
        if not self._local_lock.locked():
            async with self._local_lock:
                try:
                    transcript = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            self.executor,
                            self._run_local_transcribe,
                            audio_bytes,
                            language
                        ),
                        timeout=settings.STT_LOCAL_TIMEOUT_SECONDS
                    )
                    local_success = True
                except asyncio.TimeoutError:
                    logger.warning("Local STT timed out. Falling back to Cloud.")
                except Exception as e:
                    logger.warning(f"Local STT error: {e}. Falling back to Cloud.")
        else:
            logger.warning("Local STT worker is currently busy. Routing request directly to Cloud fallback.")

        if not local_success:
            # Fallback to Groq cloud STT
            if settings.ALLOW_CLOUD_STT and self.groq_available and self.groq_client:
                try:
                    transcript = await self._transcribe_cloud_groq(audio_bytes, language)
                    provider = "groq-whisper"
                except Exception as e:
                    logger.error(f"Cloud STT fallback failed: {e}")
                    raise RuntimeError(f"All STT providers failed. Cloud error: {e}")
            else:
                raise RuntimeError("Local STT failed/timed out, and cloud STT fallback is disabled or unconfigured.")

        return {
            "text": transcript,
            "provider": provider
        }

    async def _transcribe_cloud_groq(self, audio_bytes: bytes, language: Optional[str]) -> str:
        """Calls Groq Cloud Whisper API."""
        if not self.groq_client:
            raise ValueError("Groq client is not initialized.")
            
        # Groq expects a file tuple: (filename, bytes, content_type)
        audio_file = ("speech.wav", audio_bytes, "audio/wav")
        response = await self.groq_client.audio.transcriptions.create(
            file=audio_file,
            model=settings.GROQ_STT_MODEL,
            language=language,
            response_format="json"
        )
        return response.text.strip()

