from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""
    
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True
    
    # Provider API Keys
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    
    # Provider Model Selections
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GROQ_LLM_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_STT_MODEL: str = "whisper-large-v3"
    
    # Local STT Settings
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"
    STT_LOCAL_TIMEOUT_SECONDS: float = 3.0
    ALLOW_CLOUD_STT: bool = True
    
    # Verification Window Default
    VERIFICATION_WINDOW_MS: int = 800

    # Room / session settings (Phase 7)
    ROOM_EMPTY_EXPIRY_SECONDS: float = 300.0      # expire empty rooms after 5 min
    PLAYBACK_TIMEOUT_SECONDS: float = 30.0        # release room turn if playback never acks
    RECONNECT_GRACE_SECONDS: float = 120.0        # keep a disconnected participant rejoinable

    # Provider cooldown (Phase 8): after this many consecutive rate-limit
    # failures, a provider is skipped for COOLDOWN seconds and the fallback
    # serves traffic instead.
    PROVIDER_MAX_CONSECUTIVE_RATE_LIMITS: int = 2
    PROVIDER_COOLDOWN_SECONDS: float = 60.0

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
