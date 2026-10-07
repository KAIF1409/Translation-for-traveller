"""Turn state machine for WebSocket sessions.

Manages the lifecycle of a single translation turn:
idle -> listening -> processing -> review -> (approved -> synthesizing -> complete) | (cancelled -> complete)
"""
import asyncio
import logging
import time
import uuid
from enum import Enum
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class TurnState(str, Enum):
    """States for the turn state machine."""
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    REVIEW = "review"
    SYNTHESIZING = "synthesizing"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


class TurnContext:
    """Holds context for a single translation turn."""
    
    def __init__(self, utterance_id: str, target_language: str, tone: str, review_language: str):
        self.utterance_id = utterance_id
        self.target_language = target_language
        self.tone = tone
        self.review_language = review_language
        self.state = TurnState.IDLE
        self.created_at = time.monotonic()
        self.audio_buffer = bytearray()
        self.transcript: Optional[str] = None
        self.translated_text: Optional[str] = None
        self.back_translation: Optional[str] = None
        self.stt_provider: Optional[str] = None
        self.error: Optional[str] = None
        self._cancelled = False
    
    @property
    def is_active(self) -> bool:
        """Check if turn is still active (not complete or cancelled)."""
        return self.state not in (TurnState.COMPLETE, TurnState.CANCELLED)
    
    @property
    def was_cancelled(self) -> bool:
        """Check if turn was cancelled."""
        return self._cancelled or self.state == TurnState.CANCELLED
    
    def cancel(self):
        """Mark turn as cancelled."""
        self._cancelled = True
        self.state = TurnState.CANCELLED
    
    def transition_to(self, new_state: TurnState):
        """Transition to a new state."""
        old_state = self.state
        self.state = new_state
        logger.debug(f"Turn {self.utterance_id}: {old_state} -> {new_state}")


class TurnManager:
    """Manages turn state for a single WebSocket connection.
    
    Enforces one active utterance per connection.
    """
    
    def __init__(self):
        self._current_turn: Optional[TurnContext] = None
        self._lock = asyncio.Lock()
    
    @property
    def has_active_turn(self) -> bool:
        """Check if there's an active turn."""
        return self._current_turn is not None and self._current_turn.is_active
    
    @property
    def current_turn(self) -> Optional[TurnContext]:
        """Get the current turn context."""
        return self._current_turn
    
    def create_turn(self, target_language: str, tone: str, review_language: str) -> TurnContext:
        """Create a new turn. Only one turn can be active at a time.
        
        Raises:
            RuntimeError: If there's already an active turn.
        """
        if self.has_active_turn:
            raise RuntimeError("Cannot create turn: another turn is still active")
        
        utterance_id = str(uuid.uuid4())[:8]
        turn = TurnContext(
            utterance_id=utterance_id,
            target_language=target_language,
            tone=tone,
            review_language=review_language
        )
        self._current_turn = turn
        turn.transition_to(TurnState.IDLE)
        logger.info(f"Created turn {utterance_id} for {target_language} ({tone})")
        return turn
    
    def cancel_current_turn(self) -> Optional[TurnContext]:
        """Cancel the current turn if any."""
        if self._current_turn and self._current_turn.is_active:
            self._current_turn.cancel()
            logger.info(f"Cancelled turn {self._current_turn.utterance_id}")
            return self._current_turn
        return None
    
    def complete_turn(self):
        """Mark current turn as complete."""
        if self._current_turn:
            self._current_turn.transition_to(TurnState.COMPLETE)
    
    def reset(self):
        """Reset the turn manager (e.g., on disconnect)."""
        if self._current_turn:
            if self._current_turn.is_active:
                self._current_turn.cancel()
            self._current_turn = None