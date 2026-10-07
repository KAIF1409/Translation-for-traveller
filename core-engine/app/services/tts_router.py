"""TTS Router module implementing local Piper TTS synthesis with language-aware routing."""
import json
import logging
import asyncio
import io
import wave
from pathlib import Path
from typing import Dict, Optional, Any

from app.config import settings
from app.services.base_interfaces import BaseTTSProvider

import piper

logger = logging.getLogger(__name__)

# Allowed languages to prevent path traversal
ALLOWLIST_LANGUAGES = {"ja", "ko", "hi", "vi", "en"}


class TTSError(Exception):
    """Custom exception raised when TTS synthesis fails."""
    pass


class TTSRouter(BaseTTSProvider):
    """TTS Router using Piper TTS for local speech synthesis."""
    
    def __init__(self):
        self.model_dir = Path(__file__).resolve().parent.parent.parent / "models"
        self.manifest_path = self.model_dir / "voice_manifest.json"
        self.voices = self._load_voice_manifest()
        self._loaded_models: Dict[str, piper.PiperVoice] = {}
        self._model_lock = asyncio.Lock()
        
    def _load_voice_manifest(self) -> Dict[str, Any]:
        """Load voice manifest configuration."""
        if not self.manifest_path.exists():
            logger.warning(f"Voice manifest not found at {self.manifest_path}")
            return {"voices": [], "supported_languages": []}
        
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load voice manifest: {e}")
            return {"voices": [], "supported_languages": []}
    
    def get_voice_for_language(self, language: str) -> Optional[Dict[str, Any]]:
        """Get voice configuration for a given language."""
        if language not in ALLOWLIST_LANGUAGES:
            raise ValueError(f"Language '{language}' is not in the allowed list: {ALLOWLIST_LANGUAGES}")
        
        for voice in self.voices.get("voices", []):
            if voice["language"] == language:
                return voice
        return None
    
    def _download_voice_model(self, voice_config: Dict[str, Any]) -> None:
        """Download voice model and config files if they don't exist."""
        model_path = self.model_dir / voice_config["model_file"]
        config_path = self.model_dir / voice_config["config_file"]
        
        import urllib.request
        
        if not model_path.exists():
            logger.info(f"Downloading voice model: {voice_config['model_file']}...")
            try:
                req = urllib.request.Request(
                    voice_config["download_source"],
                    headers={"User-Agent": "PolyglotVoice/1.0"}
                )
                with urllib.request.urlopen(req) as resp, open(model_path, "wb") as out_file:
                    out_file.write(resp.read())
                logger.info(f"Voice model downloaded to {model_path}")
            except Exception as e:
                logger.error(f"Failed to download voice model: {e}")
                raise TTSError(f"Could not download voice model for {voice_config['language']}: {e}")
        
        if not config_path.exists():
            logger.info(f"Downloading voice config: {voice_config['config_file']}...")
            try:
                req = urllib.request.Request(
                    voice_config["config_download_source"],
                    headers={"User-Agent": "PolyglotVoice/1.0"}
                )
                with urllib.request.urlopen(req) as resp, open(config_path, "wb") as out_file:
                    out_file.write(resp.read())
                logger.info(f"Voice config downloaded to {config_path}")
            except Exception as e:
                logger.error(f"Failed to download voice config: {e}")
                raise TTSError(f"Could not download voice config for {voice_config['language']}: {e}")
    
    def _load_voice(self, language: str) -> piper.PiperVoice:
        """Load a Piper voice model for the given language."""
        voice_config = self.get_voice_for_language(language)
        if not voice_config:
            raise TTSError(
                f"No voice available for language '{language}'. "
                f"Supported languages: {self.voices.get('supported_languages', [])}"
            )
        
        model_path = self.model_dir / voice_config["model_file"]
        config_path = self.model_dir / voice_config["config_file"]
        
        # Download if files don't exist
        if not model_path.exists() or not config_path.exists():
            self._download_voice_model(voice_config)
        
        try:
            voice = piper.PiperVoice.load(str(model_path), config_path=str(config_path))
            logger.info(f"Loaded Piper voice for {language}: {voice_config['voice_name']}")
            return voice
        except Exception as e:
            logger.error(f"Failed to load Piper voice for {language}: {e}")
            raise TTSError(f"Could not load voice model for {language}: {e}")
    
    async def synthesize(self, text: str, language: str) -> bytes:
        """Synthesize text into WAV audio bytes.
        
        Args:
            text: The text to synthesize
            language: Target language code (e.g., 'en', 'hi', 'ja')
            
        Returns:
            WAV audio bytes
            
        Raises:
            TTSError: If synthesis fails or language is unsupported
            ValueError: If input is invalid
        """
        if not text or not text.strip():
            raise ValueError("Text to synthesize cannot be empty")
        
        if len(text) > 5000:
            raise ValueError("Text exceeds maximum length of 5000 characters")
        
        if language not in ALLOWLIST_LANGUAGES:
            raise ValueError(f"Language '{language}' is not in the allowed list: {ALLOWLIST_LANGUAGES}")
        
        # Load voice model (with lock to prevent concurrent loading)
        if language not in self._loaded_models:
            async with self._model_lock:
                # Double-check after acquiring lock
                if language not in self._loaded_models:
                    # Run model loading in thread to avoid blocking
                    loop = asyncio.get_running_loop()
                    self._loaded_models[language] = await loop.run_in_executor(
                        None, self._load_voice, language
                    )
        
        voice = self._loaded_models[language]
        voice_config = self.get_voice_for_language(language)
        sample_rate = voice_config["sample_rate"] if voice_config else 22050
        
        # Run synthesis in thread to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        wav_bytes = await loop.run_in_executor(
            None, self._synthesize_sync, voice, text, sample_rate
        )
        
        return wav_bytes
    
    def _synthesize_sync(self, voice: piper.PiperVoice, text: str, sample_rate: int) -> bytes:
        """Synchronous synthesis method to run in a thread."""
        try:
            # Create WAV file in memory using synthesize_wav
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(1)  # Mono
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(sample_rate)
                # Use synthesize_wav which writes directly to the wave file
                voice.synthesize_wav(text, wf)
            
            return wav_buffer.getvalue()
            
        except TTSError:
            raise
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            raise TTSError(f"Speech synthesis failed: {e}")
    
    def get_supported_languages(self) -> list:
        """Return list of supported languages."""
        return self.voices.get("supported_languages", [])
