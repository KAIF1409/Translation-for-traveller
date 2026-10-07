import sys
import json
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Add core-engine to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.llm_orchestrator import LLMOrchestrator, TranslationError
from app.main import app
from starlette.testclient import TestClient

print("=== Phase 2 Unit & Integration Tests ===")

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    print("✓ Health endpoint passed.")

def test_cultural_matrix_loading():
    orchestrator = LLMOrchestrator()
    assert len(orchestrator.cultural_configs) >= 5
    for lang in ["ja", "ko", "hi", "vi", "en"]:
        assert lang in orchestrator.cultural_configs
        addon, rules = orchestrator.get_cultural_rules(lang, "business")
        assert isinstance(addon, str)
        assert isinstance(rules, list)
    print("✓ Cultural matrix configs loaded successfully for all allowlisted languages.")

def test_invalid_language_and_tone_rejection():
    orchestrator = LLMOrchestrator()
    # Invalid language
    try:
        asyncio.run(orchestrator.translate("Hello", target_language="xx", tone="business"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected invalid language: {e}")

    # Invalid tone
    try:
        asyncio.run(orchestrator.translate("Hello", target_language="ja", tone="invalid_tone"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected invalid tone: {e}")

def test_empty_input_rejection():
    orchestrator = LLMOrchestrator()
    try:
        asyncio.run(orchestrator.translate("", target_language="ja", tone="business"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected empty input: {e}")

def test_pipeline_with_mock_providers():
    """Verify forward and back-translation flow and isolation with deterministic mock."""
    orchestrator = LLMOrchestrator()
    orchestrator.gemini_available = True
    orchestrator._call_gemini_forward = AsyncMock(return_value=("こんにちは、今日はいかがですか？", "gemini"))
    orchestrator._call_gemini_back = AsyncMock(return_value=("Hello, how are you today?", "gemini"))

    translated = asyncio.run(orchestrator.translate("Hello, how are you today?", target_language="ja", tone="business"))
    assert translated == "こんにちは、今日はいかがですか？"
    
    back = asyncio.run(orchestrator.back_translate(translated, source_language="en"))
    assert back == "Hello, how are you today?"
    
    # Verify request isolation: the back-translation call ONLY received the translated text
    back_call_arg = orchestrator._call_gemini_back.call_args[0][1]
    assert "<text_to_back_translate>\nこんにちは、今日はいかがですか？\n</text_to_back_translate>" in back_call_arg
    assert "Hello, how are you today?" not in back_call_arg
    print("✓ Request isolation verified: Reverse back-translation call did not receive original source text.")

def test_provider_fallback():
    """Verify that when Gemini fails, fallback to Groq is triggered."""
    orchestrator = LLMOrchestrator()
    orchestrator.gemini_available = True
    orchestrator.groq_available = True
    
    # Gemini throws an exception (simulating rate-limit/timeout)
    orchestrator._call_gemini_forward = AsyncMock(side_effect=Exception("Gemini quota exceeded"))
    orchestrator._call_groq_forward = AsyncMock(return_value=("こんにちは (via Groq)", "groq"))

    translated = asyncio.run(orchestrator.translate("Hello", target_language="ja", tone="business"))
    assert translated == "こんにちは (via Groq)"
    assert orchestrator._call_groq_forward.called
    print("✓ Provider fallback verified: Gemini failure triggered Groq.")

def test_both_providers_fail_typed_error():
    """Verify that when both providers fail, a typed TranslationError is raised."""
    orchestrator = LLMOrchestrator()
    orchestrator.gemini_available = True
    orchestrator.groq_available = True
    
    orchestrator._call_gemini_forward = AsyncMock(side_effect=Exception("Gemini error"))
    orchestrator._call_groq_forward = AsyncMock(side_effect=Exception("Groq error"))

    try:
        asyncio.run(orchestrator.translate("Hello", target_language="ja", tone="business"))
        raise AssertionError("Should have raised TranslationError")
    except TranslationError as e:
        print("✓ Both providers failing produced typed TranslationError as specified.")

def test_api_endpoint_via_testclient():
    from app.main import llm_engine
    with patch.object(llm_engine, 'translate', new_callable=AsyncMock) as mock_translate, \
         patch.object(llm_engine, 'back_translate', new_callable=AsyncMock) as mock_back:
        mock_translate.return_value = "こんにちは"
        mock_back.return_value = "Hello"
        
        response = client.post("/translate", json={
            "text": "Hello",
            "target_language": "ja",
            "tone": "business"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["translated_text"] == "こんにちは"
        assert data["back_translation"] == "Hello"
        print("✓ /translate REST endpoint verified successfully.")

if __name__ == "__main__":
    test_health_endpoint()
    test_cultural_matrix_loading()
    test_invalid_language_and_tone_rejection()
    test_empty_input_rejection()
    test_pipeline_with_mock_providers()
    test_provider_fallback()
    test_both_providers_fail_typed_error()
    test_api_endpoint_via_testclient()
    print("\nAll Phase 2 tests completed successfully with 0 errors!")

