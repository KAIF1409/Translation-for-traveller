"""Phase 7: Two-phone room/session tests."""
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from app.main import app
from app.services.ws_protocol import MessageType, ErrorCode

WAV = b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00' + b'\x00' * 10


class FakeVAD:
    def reset(self):
        pass

    def process_chunk(self, chunk):
        return []

    def is_speech_ended(self):
        return True


class FakeSTT:
    async def transcribe(self, audio_bytes, sample_rate=16000, language=None):
        return {"text": "Hello world", "provider": "fake"}


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def translate(self, text, target_language, tone="business", rules=None):
        self.calls.append(("translate", target_language, tone))
        return "Konnichiwa"

    async def back_translate(self, text, source_language="en"):
        self.calls.append(("back", source_language))
        return "Hello world"


class FakeTTS:
    async def synthesize(self, text, language):
        return WAV


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def fake_providers():
    """Patch all providers with deterministic fakes for every test.

    Without this, any test that runs a turn would invoke the real
    faster-whisper model load and live LLM calls, hanging for minutes.
    """
    with patch("app.services.ws_handler.VADService", return_value=FakeVAD()), \
         patch("app.services.ws_handler.STTRouter", return_value=FakeSTT()), \
         patch("app.services.ws_handler.LLMOrchestrator", return_value=FakeLLM()), \
         patch("app.services.ws_handler.TTSRouter", return_value=FakeTTS()):
        yield


def _create_room(ws, listening="ja", review="en", tone="business"):
    ws.send_json({"type": MessageType.CREATE_ROOM,
                  "listening_language": listening,
                  "review_language": review, "tone": tone})
    data = ws.receive_json()
    assert data["type"] == MessageType.ROOM_CREATED
    return data


def _join_room(ws, room, listening="hi", review="en", tone="casual"):
    ws.send_json({"type": MessageType.JOIN_ROOM,
                  "room_id": room["room_id"], "join_secret": room["join_secret"],
                  "listening_language": listening,
                  "review_language": review, "tone": tone})
    data = ws.receive_json()
    assert data["type"] == MessageType.JOINED
    return data


def _do_turn(ws_speaker, ws_listener):
    """Run one full turn: speaker talks, listener receives audio."""
    ws_speaker.send_json({"type": MessageType.START,
                          "target_language": "ja", "tone": "business",
                          "review_language": "en"})
    ws_speaker.send_bytes(b'\x00' * 100)
    ws_speaker.receive_json()          # processing
    review = ws_speaker.receive_json() # review (speaker alone reviews)
    assert review["type"] == MessageType.REVIEW
    ws_speaker.send_json({"type": MessageType.APPROVE,
                          "utterance_id": review["utterance_id"]})
    audio = ws_listener.receive_bytes()
    assert audio[:4] == b"RIFF"
    # Listener acknowledges playback so the room turn is released.
    ws_listener.send_json({"type": MessageType.PLAYBACK_COMPLETE})
    end = ws_speaker.receive_json()    # turn_end
    assert end["type"] == MessageType.TURN_END


def test_create_and_join(client):
    """Two users join; host is notified."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()
        ws_b.receive_json()
        room = _create_room(ws_a)
        joined = _join_room(ws_b, room)
        assert joined["room_id"] == room["room_id"]
        assert joined["listening_language"] == "hi"
        peer_note = ws_a.receive_json()
        assert peer_note["type"] == MessageType.PEER_JOINED


def test_third_user_rejected(client):
    """Room limit is two participants."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b, \
         client.websocket_connect("/ws") as ws_c:
        for ws in (ws_a, ws_b, ws_c):
            ws.receive_json()
        room = _create_room(ws_a)
        _join_room(ws_b, room)
        ws_a.receive_json()  # peer_joined
        ws_c.send_json({"type": MessageType.JOIN_ROOM,
                        "room_id": room["room_id"],
                        "join_secret": room["join_secret"],
                        "listening_language": "ja",
                        "review_language": "en", "tone": "casual"})
        err = ws_c.receive_json()
        assert err["type"] == MessageType.ERROR
        assert err["code"] == ErrorCode.ROOM_FULL


def test_invalid_join_secret_rejected(client):
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()
        ws_b.receive_json()
        room = _create_room(ws_a)
        ws_b.send_json({"type": MessageType.JOIN_ROOM,
                        "room_id": room["room_id"], "join_secret": "wrong",
                        "listening_language": "hi",
                        "review_language": "en", "tone": "casual"})
        err = ws_b.receive_json()
        assert err["type"] == MessageType.ERROR


def test_audio_routed_to_peer_only(client):
    """A speaks -> only B receives audio; B speaks -> only A receives."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()
        ws_b.receive_json()
        room = _create_room(ws_a)
        _join_room(ws_b, room)
        ws_a.receive_json()  # peer_joined
        _do_turn(ws_a, ws_b)
        _do_turn(ws_b, ws_a)


def test_speaker_gets_review_listener_does_not(client):
    """Speaker alone receives pending review text; cancel delivers nothing."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()
        ws_b.receive_json()
        room = _create_room(ws_a)
        _join_room(ws_b, room)
        ws_a.receive_json()
        ws_a.send_json({"type": MessageType.START,
                        "target_language": "ja", "tone": "business",
                        "review_language": "en"})
        ws_a.send_bytes(b'\x00' * 100)
        ws_a.receive_json()  # processing
        review = ws_a.receive_json()
        assert review["type"] == MessageType.REVIEW
        assert review["back_translation"]
        ws_a.send_json({"type": MessageType.CANCEL,
                        "utterance_id": review["utterance_id"]})
        end = ws_a.receive_json()
        assert end["type"] == MessageType.TURN_END


def test_non_owner_cannot_approve_peer_turn(client):
    """Only the speaker can approve their own utterance."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()
        ws_b.receive_json()
        room = _create_room(ws_a)
        _join_room(ws_b, room)
        ws_a.receive_json()
        ws_a.send_json({"type": MessageType.START,
                        "target_language": "ja", "tone": "business",
                        "review_language": "en"})
        ws_a.send_bytes(b'\x00' * 100)
        ws_a.receive_json()  # processing
        review = ws_a.receive_json()
        uid = review["utterance_id"]
        ws_b.send_json({"type": MessageType.APPROVE, "utterance_id": uid})
        err = ws_b.receive_json()
        assert err["type"] == MessageType.ERROR
        ws_a.send_json({"type": MessageType.APPROVE, "utterance_id": uid})
        assert ws_b.receive_bytes()[:4] == b"RIFF"
        end = ws_a.receive_json()
        assert end["type"] == MessageType.TURN_END


def test_concurrent_turns_busy(client):
    """Second turn start while one is active gets a busy response."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()
        ws_b.receive_json()
        room = _create_room(ws_a)
        _join_room(ws_b, room)
        ws_a.receive_json()
        ws_a.send_json({"type": MessageType.START,
                        "target_language": "ja", "tone": "business",
                        "review_language": "en"})
        ws_a.send_bytes(b'\x00' * 100)
        ws_a.receive_json()  # processing
        review = ws_a.receive_json()
        ws_b.send_json({"type": MessageType.START,
                        "target_language": "ja", "tone": "business",
                        "review_language": "en"})
        busy = ws_b.receive_json()
        assert busy["type"] == MessageType.BUSY
        ws_a.send_json({"type": MessageType.CANCEL,
                        "utterance_id": review["utterance_id"]})
        ws_a.receive_json()


def test_peer_disconnect_cancels_pending_work(client):
    """Peer leaving while speaker is at review ends the turn safely."""
    with client.websocket_connect("/ws") as ws_a:
        # Nested block: only ws_b exits; ws_a must stay open to observe
        # the safe end of its pending turn.
        with client.websocket_connect("/ws") as ws_b:
            ws_a.receive_json()
            ws_b.receive_json()
            room = _create_room(ws_a)
            _join_room(ws_b, room)
            ws_a.receive_json()
            ws_a.send_json({"type": MessageType.START,
                            "target_language": "ja", "tone": "business",
                            "review_language": "en"})
            ws_a.send_bytes(b'\x00' * 100)
            ws_a.receive_json()  # processing
        # ws_b exit = peer disconnect; A's pending turn must end without
        # hang. A may observe: review (racing), peer_left, turn_end —
        # drain until an end signal arrives (max 3 server messages).
        got = set()
        for _ in range(4):
            msg = ws_a.receive_json()
            got.add(msg["type"])
            if msg["type"] in (MessageType.TURN_END, MessageType.PEER_LEFT):
                break
        assert MessageType.TURN_END in got or MessageType.PEER_LEFT in got


def test_separate_rooms_isolated(client):
    """Rooms do not leak transcript/audio across each other."""
    with client.websocket_connect("/ws") as ws_a, \
         client.websocket_connect("/ws") as ws_b, \
         client.websocket_connect("/ws") as ws_c, \
         client.websocket_connect("/ws") as ws_d:
        for ws in (ws_a, ws_b, ws_c, ws_d):
            ws.receive_json()
        room1 = _create_room(ws_a)
        _join_room(ws_b, room1)
        ws_a.receive_json()
        room2 = _create_room(ws_c)
        _join_room(ws_d, room2)
        ws_c.receive_json()
        _do_turn(ws_a, ws_b)
        _do_turn(ws_c, ws_d)
        # A room2 member cannot join room1 (already occupied anyway).
        ws_c.send_json({"type": MessageType.JOIN_ROOM,
                        "room_id": room1["room_id"],
                        "join_secret": room1["join_secret"],
                        "listening_language": "ja",
                        "review_language": "en", "tone": "casual"})
        err = ws_c.receive_json()
        assert err["type"] == MessageType.ERROR


def test_session_manager_unit():
    """Direct unit checks on the in-memory session manager (real async API)."""
    import asyncio
    from app.services.session_manager import SessionManager, Participant, RoomError

    async def scenario():
        mgr = SessionManager()
        host = Participant("p-host", "host-token", websocket=None)
        room = await mgr.create_room(host)
        assert room.room_id and len(room.join_secret) >= 16  # high-entropy secret

        # Join: server-controlled participant identity
        joined_room, p_b = await mgr.join_room(
            room.room_id, room.join_secret, websocket=None, client_key="client-b")
        assert joined_room is room
        assert p_b.participant_id != "p-host"
        assert len(p_b.token) >= 16

        # Room limit is two: third join raises room_full
        try:
            await mgr.join_room(room.room_id, room.join_secret,
                                websocket=None, client_key="client-c")
            assert False, "expected RoomError(room_full)"
        except RoomError as e:
            assert e.code == "room_full"

        # Invalid secret rejected
        try:
            await mgr.join_room(room.room_id, "bad-secret",
                                websocket=None, client_key="client-d")
            assert False, "expected RoomError(invalid_secret)"
        except RoomError as e:
            assert e.code == "invalid_secret"

        # Token auth: only real token passes
        assert mgr.authenticate(room.room_id, p_b.participant_id, p_b.token) is p_b
        assert mgr.authenticate(room.room_id, p_b.participant_id, "forged") is None
        assert mgr.authenticate(room.room_id, "ghost", p_b.token) is None

        # Peer routing
        assert room.get_peer("p-host") is p_b
        assert room.get_peer(p_b.participant_id) is host

        # Half-duplex turn: acquire once, second acquire fails, release works
        assert await room.acquire_turn("p-host") is True
        assert await room.acquire_turn(p_b.participant_id) is False
        room.release_turn("p-host")
        assert await room.acquire_turn(p_b.participant_id) is True
        room.release_turn(p_b.participant_id)

        # Leave + empty-room cleanup
        await mgr.leave_room(room.room_id, p_b.participant_id)
        await mgr.leave_room(room.room_id, "p-host")
        assert mgr.get_room(room.room_id) is None

    asyncio.run(scenario())
