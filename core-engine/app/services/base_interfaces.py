from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class BaseSTTProvider(ABC):
    """Abstract interface for Speech-to-Text providers."""
    
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000, language: Optional[str] = None) -> Dict[str, Any]:
        """Transcribe audio bytes into text."""
        pass

class BaseTranslationProvider(ABC):
    """Abstract interface for LLM Translation providers."""
    
    @abstractmethod
    async def translate(self, text: str, target_language: str, tone: str = "business", rules: Optional[Dict[str, Any]] = None) -> str:
        """Translate text to target language using tone rules."""
        pass
        
    @abstractmethod
    async def back_translate(self, text: str, source_language: str = "en") -> str:
        """Back-translate text for accuracy verification."""
        pass

class BaseTTSProvider(ABC):
    """Abstract interface for Text-to-Speech providers."""
    
    @abstractmethod
    async def synthesize(self, text: str, language: str) -> bytes:
        """Synthesize text into WAV audio bytes."""
        pass
