"""In-memory room/session manager for two-phone conversations.

Security model:
- Human-friendly room ID identifies the room; NOT sufficient for auth.
- High-entropy join secret returned at room creation.
- Participant-specific tokens issued after joining.
- Max 2 participants per room.
- All state in-memory; run ONE uvicorn worker.

Half-duplex turn policy:
- One active room turn at a time (acquired at turn start, released on
  playback completion / timeout / disconnect).
"""
import asyncio
import logging
import secrets
import time
import uuid
from typing import Dict, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

_JOIN_WINDOW_SECONDS = 60.0
_MAX_JOINS_PER_WINDOW = 10


class RoomError(Exception):
    """Raised for room operation failures."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class Participant:
    """A participant in a room."""

    def __init__(self, participant_id: str, token: str, websocket):
        self.participant_id = participant_id
        self.token = token
        self.websocket = websocket
        self.connected = True
        self.disconnected_at: Optional[float] = None
        # Preferences (set at join / updated via messages)
        self.listening_language: str = "ja"   # language this participant wants to HEAR
        self.review_language: str = "en"      # language for back-translation review
        self.tone: str = "business"           # outgoing tone for their own speech
        self.joined_at = time.monotonic()


class Room:
    """A two-participant conversation room."""

    def __init__(self, room_id: str, join_secret: str, host: "Participant"):
        self.room_id = room_id
        self.join_secret = join_secret
        self.participants: Dict[str, Participant] = {host.participant_id: host}
        self.last_activity = time.monotonic()
        self._turn_lock = asyncio.Lock()
        self.active_speaker_id: Optional[str] = None
        # Bumped on every successful acquire. Playback watchdogs capture the
        # generation at start and no-op if it has changed, so a stale watchdog
        # can never release a LATER turn's lock.
        self.turn_generation: int = 0

    @property
    def is_full(self) -> bool:
        return len(self.participants) >= 2

    @property
    def is_empty(self) -> bool:
        return len(self.participants) == 0

    @property
    def connected_count(self) -> int:
        return sum(1 for p in self.participants.values() if p.connected)

    def get_peer(self, participant_id: str) -> Optional[Participant]:
        for pid, p in self.participants.items():
            if pid != participant_id:
                return p
        return None

    def add_participant(self, participant: Participant) -> bool:
        if self.is_full:
            return False
        self.participants[participant.participant_id] = participant
        self.last_activity = time.monotonic()
        return True

    def remove_participant(self, participant_id: str):
        self.participants.pop(participant_id, None)
        self.last_activity = time.monotonic()
        if self.active_speaker_id == participant_id:
            self.active_speaker_id = None

    def mark_disconnected(self, participant_id: str) -> bool:
        """Keep the participant's identity/prefs but drop their socket.

        Returns True if the room still has connected participants (the
        disconnected one may rejoin within the grace period). Releases the
        room turn if the disconnecting participant held it.
        """
        p = self.participants.get(participant_id)
        if not p:
            return self.connected_count > 0
        p.connected = False
        p.disconnected_at = time.monotonic()
        p.websocket = None
        self.last_activity = time.monotonic()
        if self.active_speaker_id == participant_id:
            self.active_speaker_id = None
        return self.connected_count > 0

    def reattach(self, participant_id: str, websocket) -> Optional["Participant"]:
        """Reattach a websocket to a disconnected participant (rejoin)."""
        p = self.participants.get(participant_id)
        if not p or p.connected:
            return None
        p.websocket = websocket
        p.connected = True
        p.disconnected_at = None
        self.last_activity = time.monotonic()
        return p

    def verify_token(self, participant_id: str, token: str) -> bool:
        p = self.participants.get(participant_id)
        if not p:
            return False
        return secrets.compare_digest(p.token, token)

    async def acquire_turn(self, participant_id: str) -> bool:
        """Atomically try to acquire the room turn. Returns False if held."""
        async with self._turn_lock:
            if self.active_speaker_id is not None:
                return False
            self.active_speaker_id = participant_id
            self.turn_generation += 1
            self.last_activity = time.monotonic()
            return True

    def release_turn(self, participant_id: str):
        if self.active_speaker_id == participant_id:
            self.active_speaker_id = None
            self.last_activity = time.monotonic()

    def release_turn_on_behalf(self, participant_id: str):
        """Release the turn held by the PEER of `participant_id`.

        The playback-complete acknowledgement arrives on the listener's
        connection, but the room turn is held by the active speaker.
        Only the "caller is the peer of the active speaker" combination
        may clear the lock via this path.
        """
        peer = self.get_peer(participant_id)
        if peer and self.active_speaker_id == peer.participant_id:
            self.active_speaker_id = None
            self.last_activity = time.monotonic()

    @property
    def is_stale(self) -> bool:
        now = time.monotonic()
        if self.is_empty:
            return (now - self.last_activity) > settings.ROOM_EMPTY_EXPIRY_SECONDS
        # Everyone disconnected: honour the bounded rejoin grace window,
        # then expire the room.
        if self.connected_count == 0:
            return (now - self.last_activity) > settings.RECONNECT_GRACE_SECONDS
        return False


class SessionManager:
    """Manages all rooms. Singleton per process (one uvicorn worker only)."""

    def __init__(self):
        self._rooms: Dict[str, Room] = {}
        self._join_attempts: Dict[str, list] = {}
        self._lock = asyncio.Lock()

    def _check_join_rate(self, client_key: str) -> bool:
        now = time.monotonic()
        attempts = [t for t in self._join_attempts.get(client_key, [])
                    if now - t < _JOIN_WINDOW_SECONDS]
        if len(attempts) >= _MAX_JOINS_PER_WINDOW:
            self._join_attempts[client_key] = attempts
            return False
        attempts.append(now)
        self._join_attempts[client_key] = attempts
        return True

    async def create_room(self, host: Participant) -> Room:
        room_id = self._generate_room_id()
        join_secret = secrets.token_urlsafe(24)
        room = Room(room_id=room_id, join_secret=join_secret, host=host)
        async with self._lock:
            self._rooms[room_id] = room
        logger.info(f"Room {room_id} created by {host.participant_id}")
        return room

    async def join_room(self, room_id: str, join_secret: str, websocket,
                        client_key: str = "") -> Tuple[Room, Participant]:
        """Join a room. Raises RoomError on rate limit / not found / bad secret / full."""
        if not self._check_join_rate(client_key or "default"):
            raise RoomError("rate_limited", "Too many join attempts. Try again later.")
        room = self._rooms.get(room_id)
        if not room:
            raise RoomError("not_found", "Room not found")
        if not secrets.compare_digest(room.join_secret, join_secret):
            raise RoomError("invalid_secret", "Invalid join secret")
        if room.is_full:
            raise RoomError("room_full", "Room already has two participants")
        participant_id = f"p_{uuid.uuid4().hex[:8]}"
        participant = Participant(
            participant_id=participant_id,
            token=secrets.token_urlsafe(24),
            websocket=websocket,
        )
        room.add_participant(participant)
        logger.info(f"Participant {participant_id} joined room {room_id}")
        return room, participant

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def list_rooms(self) -> Dict[str, Room]:
        """Snapshot of all rooms (for the stale-room reaper)."""
        return dict(self._rooms)

    def mark_disconnected(self, room_id: str, participant_id: str) -> bool:
        """Mark a participant disconnected. Returns True if the room stays
        alive (some participant still connected and may accept a rejoin)."""
        room = self._rooms.get(room_id)
        if not room:
            return False
        return room.mark_disconnected(participant_id)

    def remove_room(self, room_id: str):
        """Immediately drop a room (e.g., no connected participants left)."""
        room = self._rooms.pop(room_id, None)
        if room:
            logger.info(f"Room {room_id} removed")

    async def rejoin(self, room_id: str, participant_id: str, participant_token: str,
                     websocket, client_key: str = "", prefs: Optional[dict] = None
                     ) -> Tuple[Room, Participant]:
        """Rejoin with participant credentials during the grace period.

        Rate-limited like any join. The participant token (never the
        human-friendly room ID) is the credential. Preferences may be
        refreshed by the reconnecting client; approvals/audio from before
        the disconnect are never replayed.
        """
        if not self._check_join_rate(client_key or "default"):
            raise RoomError("rate_limited", "Too many join attempts. Try again later.")
        room = self._rooms.get(room_id)
        if not room:
            raise RoomError("not_found", "Room not found")
        participant = room.participants.get(participant_id)
        if not participant or not secrets.compare_digest(participant.token, participant_token):
            raise RoomError("invalid_credentials", "Invalid participant credentials")
        if participant.connected:
            raise RoomError("already_connected", "Participant already connected")
        reattached = room.reattach(participant_id, websocket)
        if not reattached:
            raise RoomError("not_found", "Participant cannot rejoin")
        if prefs:
            if prefs.get("listening_language"):
                participant.listening_language = prefs["listening_language"]
            if prefs.get("review_language"):
                participant.review_language = prefs["review_language"]
            if prefs.get("tone"):
                participant.tone = prefs["tone"]
        logger.info(f"Participant {participant_id} rejoined room {room_id}")
        return room, participant

    def authenticate(self, room_id: str, participant_id: str, token: str) -> Optional[Participant]:
        room = self._rooms.get(room_id)
        if not room or not room.verify_token(participant_id, token):
            return None
        return room.participants.get(participant_id)

    async def leave_room(self, room_id: str, participant_id: str):
        room = self._rooms.get(room_id)
        if not room:
            return
        room.remove_participant(participant_id)
        if room.is_empty:
            async with self._lock:
                self._rooms.pop(room_id, None)
            logger.info(f"Room {room_id} deleted (empty)")

    def cleanup_stale_rooms(self) -> int:
        stale = [rid for rid, room in self._rooms.items() if room.is_stale]
        for rid in stale:
            self._rooms.pop(rid, None)
            logger.info(f"Room {rid} expired and removed")
        return len(stale)

    @staticmethod
    def _generate_room_id() -> str:
        """Human-friendly room ID (readable, e.g., 'MVKT-3842')."""
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars
        code = "".join(secrets.choice(alphabet) for _ in range(4))
        digits = "".join(secrets.choice("0123456789") for _ in range(4))
        return f"{code}-{digits}"


# Module-level singleton (one uvicorn worker only!)
session_manager = SessionManager()

