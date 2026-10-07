"""WebSocket protocol definitions for Polyglot Voice.

Message types exchanged between client and server.
All JSON messages follow the pattern: {"type": "...", ...}
Binary messages are raw PCM16 audio chunks.
"""
from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """WebSocket message types."""
    # Client -> Server
    START = "start"
    AUDIO = "audio"
    APPROVE = "approve"
    CANCEL = "cancel"
    ACK = "ack"
    PLAYBACK_COMPLETE = "playback_complete"
    
    # Room management (client -> server)
    CREATE_ROOM = "create_room"
    JOIN_ROOM = "join_room"
    UPDATE_PREFS = "update_prefs"
    
    # Server -> Client
    READY = "ready"
    REVIEW = "review"
    PROCESSING = "processing"
    ERROR = "error"
    TURN_END = "turn_end"
    ROOM_CREATED = "room_created"
    JOINED = "joined"
    PEER_JOINED = "peer_joined"
    PEER_LEFT = "peer_left"
    BUSY = "busy"


class StartRequest(BaseModel):
    """Client request to start a turn."""
    type: str = MessageType.START
    target_language: str = Field(..., description="Target language code (e.g., 'ja', 'hi')")
    tone: str = Field(default="business", description="Tone axis: formal, business, casual")
    review_language: str = Field(default="en", description="Language for back-translation review")
    # Room mode: identifies the speaker (server verifies token server-side)
    participant_id: str = Field(default="", description="Participant ID (room mode)")
    token: str = Field(default="", description="Participant token (room mode)")


class ApproveRequest(BaseModel):
    """Client approval of translation for TTS."""
    type: str = MessageType.APPROVE
    utterance_id: str = Field(..., description="ID of the utterance to approve")
    participant_id: str = Field(default="", description="Participant ID (room mode)")
    token: str = Field(default="", description="Participant token (room mode)")


class CancelRequest(BaseModel):
    """Client cancellation of current turn."""
    type: str = MessageType.CANCEL
    utterance_id: str = Field(default="", description="ID of the utterance to cancel")
    participant_id: str = Field(default="", description="Participant ID (room mode)")
    token: str = Field(default="", description="Participant token (room mode)")


class AckRequest(BaseModel):
    """Client acknowledgment of received message."""
    type: str = MessageType.ACK
    message_type: str = Field(..., description="Type of message being acknowledged")


class ReadyResponse(BaseModel):
    """Server ready to receive audio."""
    type: str = MessageType.READY
    session_id: str = Field(..., description="Unique session identifier")


class ReviewResponse(BaseModel):
    """Server presents translation for review."""
    type: str = MessageType.REVIEW
    utterance_id: str = Field(..., description="Unique utterance identifier")
    transcript: str = Field(..., description="Original transcribed text")
    translated_text: str = Field(..., description="Translated text in target language")
    back_translation: str = Field(..., description="Back-translated text for verification")
    target_language: str = Field(..., description="Target language code")
    verification_window_ms: int = Field(default=800, description="Countdown time for auto-approval")


class ProcessingResponse(BaseModel):
    """Server is processing audio."""
    type: str = MessageType.PROCESSING
    message: str = Field(default="Processing audio...")


class ErrorResponse(BaseModel):
    """Server error message."""
    type: str = MessageType.ERROR
    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")


class TurnEndResponse(BaseModel):
    """Server signals end of turn."""
    type: str = MessageType.TURN_END
    reason: str = Field(..., description="Reason for turn end: approved, cancelled, error, timeout")


# Error codes
class ErrorCode(str, Enum):
    INVALID_MESSAGE = "invalid_message"
    INVALID_AUDIO = "invalid_audio"
    NO_ACTIVE_TURN = "no_active_turn"
    INVALID_UTTERANCE = "invalid_utterance"
    STT_FAILED = "stt_failed"
    TRANSLATION_FAILED = "translation_failed"
    TTS_FAILED = "tts_failed"
    RATE_LIMITED = "rate_limited"
    SERVER_BUSY = "server_busy"
    ROOM_NOT_FOUND = "room_not_found"
    ROOM_FULL = "room_full"
    INVALID_SECRET = "invalid_secret"
    NOT_AUTHENTICATED = "not_authenticated"
    NOT_TURN_OWNER = "not_turn_owner"
    INVALID_CREDENTIALS = "invalid_credentials"


# ---- Room management messages ----

class CreateRoomRequest(BaseModel):
    """Create a new room (caller becomes host)."""
    type: str = MessageType.CREATE_ROOM
    listening_language: str = Field(default="ja", description="Language this participant wants to hear")
    review_language: str = Field(default="en", description="Language for back-translation review")
    tone: str = Field(default="business", description="Outgoing tone")


class JoinRoomRequest(BaseModel):
    """Join an existing room with room_id + join secret.

    Two modes:
    - First join: room_id + join_secret (from the room creator).
    - Rejoin after disconnect: room_id + participant_id + participant_token
      within the bounded grace period (never replays old approvals/audio).
    """
    type: str = MessageType.JOIN_ROOM
    room_id: str = Field(..., description="Human-friendly room ID")
    join_secret: str = Field(default="", description="High-entropy join secret from room creator")
    participant_id: str = Field(default="", description="Participant ID (rejoin mode)")
    participant_token: str = Field(default="", description="Participant token (rejoin mode)")
    listening_language: str = Field(default="ja", description="Language this participant wants to hear")
    review_language: str = Field(default="en", description="Language for back-translation review")
    tone: str = Field(default="business", description="Outgoing tone")


class UpdatePrefsRequest(BaseModel):
    """Update participant preferences (languages/tone)."""
    type: str = MessageType.UPDATE_PREFS
    room_id: str = Field(default="", description="Room ID (room mode)")
    participant_id: str = Field(default="", description="Participant ID (room mode)")
    token: str = Field(default="", description="Participant token (room mode)")
    listening_language: Optional[str] = Field(default=None)
    review_language: Optional[str] = Field(default=None)
    tone: Optional[str] = Field(default=None)


class RoomCreatedResponse(BaseModel):
    """Server response after creating a room."""
    type: str = MessageType.ROOM_CREATED
    room_id: str = Field(..., description="Human-friendly room ID to share")
    join_secret: str = Field(..., description="Secret for the peer to join with")
    participant_id: str = Field(..., description="Server-assigned participant ID")
    token: str = Field(..., description="Participant token for auth")


class JoinedResponse(BaseModel):
    """Server response after joining a room."""
    type: str = MessageType.JOINED
    room_id: str = Field(...)
    participant_id: str = Field(...)
    token: str = Field(...)
    listening_language: str = Field(default="ja")
    review_language: str = Field(default="en")
    tone: str = Field(default="business")
    rejoined: bool = Field(default=False, description="True if this restored a disconnected session")


class PeerJoinedResponse(BaseModel):
    """Server notification when the other participant joins."""
    type: str = MessageType.PEER_JOINED
    room_id: str = Field(default="")
    participant_id: str = Field(default="")
    rejoined: bool = Field(default=False, description="True if the peer rejoined after disconnect")


class PeerLeftResponse(BaseModel):
    """Server notification when the other participant leaves."""
    type: str = MessageType.PEER_LEFT
    room_id: str = Field(default="")
    participant_id: str = Field(default="")


class PrefsUpdatedResponse(BaseModel):
    """Server confirmation of preference update."""
    type: str = "prefs_updated"
    listening_language: str = Field(default="ja")
    review_language: str = Field(default="en")
    tone: str = Field(default="business")


class BusyResponse(BaseModel):
    """Server response when the room turn is held by the other participant."""
    type: str = MessageType.BUSY
    message: str = Field(default="Other person is speaking.")