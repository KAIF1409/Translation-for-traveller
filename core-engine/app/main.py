from fastapi import FastAPI, HTTPException, WebSocket
from pydantic import BaseModel
import logging

logger = logging.getLogger("polyglot_voice")
from app.config import settings

# Make INFO-level logs (including per-stage TIMING lines) visible when
# running under uvicorn. Without this, logger.info output is dropped by
# the default WARNING threshold and latency measurements are invisible.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
from app.services.llm_orchestrator import LLMOrchestrator
from app.services.ws_handler import WebSocketHandler

app = FastAPI(
    title="Polyglot Voice Core Engine",
    description="Real-time voice translation backend for code-switched speech",
    version="0.1.0"
)

# Initialize Orchestrator
llm_engine = LLMOrchestrator()

from app.services.session_manager import session_manager


@app.on_event("startup")
async def start_room_reaper():
    """Periodically expire rooms that are empty past the expiry window or
    fully disconnected past the rejoin grace period."""
    import asyncio
    from app.config import settings as _s

    async def _reap():
        interval = 30.0
        while True:
            await asyncio.sleep(interval)
            try:
                for room_id, room in list(session_manager.list_rooms().items()):
                    if room.is_stale:
                        session_manager.remove_room(room_id)
                        logger.info(f"Reaped stale room {room_id}")
            except Exception as e:
                logger.error(f"Room reaper error: {e}")

    asyncio.create_task(_reap())
    logger.info("Room reaper started")

class TranslationRequest(BaseModel):
    text: str
    target_language: str
    tone: str = "business"

@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {
        "status": "ok",
        "service": "polyglot-voice-engine",
        "version": "0.1.0"
    }

@app.post("/translate")
async def translate_text(request: TranslationRequest):
    """Translation endpoint."""
    try:
        translated = await llm_engine.translate(
            text=request.text,
            target_language=request.target_language,
            tone=request.tone
        )
        back_translated = await llm_engine.back_translate(
            text=translated,
            source_language="en" # Assuming English is the primary source for V1 demo
        )
        return {
            "translated_text": translated,
            "back_translation": back_translated
        }
    except ValueError as e:
        # Client-input problems (unknown language/tone, empty or oversized
        # text) are request errors -> 422, not provider failures.
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        # Never expose raw SDK exceptions (they can embed URLs/credentials).
        logger.error(f"translate endpoint failed: {type(e).__name__}")
        raise HTTPException(
            status_code=502,
            detail="Translation failed: all providers unavailable. Try again later."
        )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time voice translation.
    
    Protocol:
    1. Client connects, server sends {"type": "ready", "session_id": "..."}
    2. Client sends {"type": "start", "target_language": "ja", "tone": "business", "review_language": "en"}
    3. Client sends binary PCM16 audio chunks
    4. Server detects end-of-speech, processes audio
    5. Server sends {"type": "review", "transcript": "...", "translated_text": "...", "back_translation": "..."}
    6. Client sends {"type": "approve", "utterance_id": "..."} or {"type": "cancel", "utterance_id": "..."}
    7. If approved, server sends binary WAV audio
    8. Server sends {"type": "turn_end", "reason": "approved|cancelled|error"}
    """
    handler = WebSocketHandler(websocket)
    await handler.handle()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)

