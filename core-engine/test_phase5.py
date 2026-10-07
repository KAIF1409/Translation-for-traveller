"""Phase 5 WebSocket Pipeline Tests."""
import sys
from typing import Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from app.main import app
from app.services.ws_protocol import MessageType, ErrorCode


class FakeVAD:
    def __init__(self):
        self._detected = False
        self._ended = False
    
    def reset(self):
        self._detected = False
        self._ended = False
    
    def process_chunk(self, chunk):
        self._detected = True
        return []
    
    def is_speech_ended(self):
        if self._detected and not self._ended:
            self._ended = True
            return True
        return False


class FakeSTT:
    def __init__(self):
        self.call_count = 0
    
    async def transcribe(self, audio_bytes, sample_rate=16000, language=None):
        self.call_count += 1
        return {"text": "Hello world", "provider": "fake"}


class FakeLLM:
    def __init__(self):
        self.translate_count = 0
        self.back_count = 0
    
    async def translate(self, text, target_language, tone="business", rules=None):
        self.translate_count += 1
        return "Konnichiwa"
    
    async def back_translate(self, text, source_language="en"):
        self.back_count += 1
        return "Hello world"


class FakeTTS:
    def __init__(self):
        self.call_count = 0
    
    async def synthesize(self, text, language):
        self.call_count += 1
        return b'RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00' + b'\x00' * 10


@pytest.fixture
def client():
    return TestClient(app)
@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ws_ready(client):
    with client.websocket_connect("/ws") as ws:
        data = ws.receive_json()
        assert data["type"] == MessageType.READY
        assert "session_id" in data


def test_ws_invalid_json(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_text("not json{")
        data = ws.receive_json()
        assert data["type"] == MessageType.ERROR


def test_ws_unknown_type(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "unknown"})
        data = ws.receive_json()
        assert data["type"] == MessageType.ERROR


def test_pipeline_happy_path(client):
    """Test: start -> audio -> review -> approve -> audio out."""
    vad = FakeVAD()
    stt = FakeSTT()
    llm = FakeLLM()
    tts = FakeTTS()
    
    with patch("app.services.ws_handler.VADService", return_value=vad), \
         patch("app.services.ws_handler.STTRouter", return_value=stt), \
         patch("app.services.ws_handler.LLMOrchestrator", return_value=llm), \
         patch("app.services.ws_handler.TTSRouter", return_value=tts):
        
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
            assert data["type"] == MessageType.READY
            ws.send_json({
                "type": MessageType.START,
                "target_language": "ja",
                "tone": "business",
                "review_language": "en"
            })
            ws.send_bytes(b'\x00' * 100)
            data = ws.receive_json()
            assert data["type"] == MessageType.PROCESSING
            data = ws.receive_json()
            assert data["type"] == MessageType.REVIEW
            assert data["transcript"] == "Hello world"
            uid = data["utterance_id"]
            ws.send_json({"type": MessageType.APPROVE, "utterance_id": uid})
            audio = ws.receive_bytes()
            assert len(audio) > 0
            assert audio[:4] == b'RIFF'
            data = ws.receive_json()
            assert data["type"] == MessageType.TURN_END
            assert data["reason"] == "approved"
            assert tts.call_count == 1


def test_pipeline_cancel(client):
    """Test: cancel before approval prevents TTS."""
    vad = FakeVAD()
    stt = FakeSTT()
    llm = FakeLLM()
    tts = FakeTTS()
    
    with patch("app.services.ws_handler.VADService", return_value=vad), \
         patch("app.services.ws_handler.STTRouter", return_value=stt), \
         patch("app.services.ws_handler.LLMOrchestrator", return_value=llm), \
         patch("app.services.ws_handler.TTSRouter", return_value=tts):
        
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({
                "type": MessageType.START,
                "target_language": "ja",
                "tone": "business",
                "review_language": "en"
            })
            ws.send_bytes(b'\x00' * 100)
            ws.receive_json()
            data = ws.receive_json()
            uid = data["utterance_id"]
            ws.send_json({"type": MessageType.CANCEL, "utterance_id": uid})
            data = ws.receive_json()
            assert data["reason"] == "cancelled"
            assert tts.call_count == 0


def test_pipeline_duplicate_approval(client):
    """Test: duplicate approvals only synthesize once."""
    vad = FakeVAD()
    stt = FakeSTT()
    llm = FakeLLM()
    tts = FakeTTS()
    
    with patch("app.services.ws_handler.VADService", return_value=vad), \
         patch("app.services.ws_handler.STTRouter", return_value=stt), \
         patch("app.services.ws_handler.LLMOrchestrator", return_value=llm), \
         patch("app.services.ws_handler.TTSRouter", return_value=tts):
        
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({
                "type": MessageType.START,
                "target_language": "ja",
                "tone": "business",
                "review_language": "en"
            })
            ws.send_bytes(b'\x00' * 100)
            ws.receive_json()
            data = ws.receive_json()
            uid = data["utterance_id"]
            ws.send_json({"type": MessageType.APPROVE, "utterance_id": uid})
            ws.send_json({"type": MessageType.APPROVE, "utterance_id": uid})
            ws.receive_bytes()
            ws.receive_json()
            assert tts.call_count == 1


def test_turn_state_machine():
    """Test turn state transitions."""
    from app.services.turn_state import TurnManager, TurnState
    
    m = TurnManager()
    t = m.create_turn("ja", "business", "en")
    assert t.state == TurnState.IDLE
    assert t.is_active
    t.transition_to(TurnState.LISTENING)
    assert t.state == TurnState.LISTENING
    t.transition_to(TurnState.PROCESSING)
    assert t.state == TurnState.PROCESSING
    t.transition_to(TurnState.REVIEW)
    assert t.state == TurnState.REVIEW
    t.transition_to(TurnState.COMPLETE)
    assert not t.is_active


def test_turn_cancellation():
    """Test turn cancellation."""
    from app.services.turn_state import TurnManager, TurnState
    
    m = TurnManager()
    t = m.create_turn("ja", "business", "en")
    t.cancel()
    assert t.was_cancelled
    assert not t.is_active
    assert t.state == TurnState.CANCELLED


def test_one_active_turn_only():
    """Test that only one turn can be active at a time."""
    from app.services.turn_state import TurnManager
    
    m = TurnManager()
    m.create_turn("ja", "business", "en")
    assert m.has_active_turn
    with pytest.raises(RuntimeError):
        m.create_turn("en", "casual", "ja")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])