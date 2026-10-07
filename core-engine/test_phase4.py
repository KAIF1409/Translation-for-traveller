import sys
import json
import wave
import asyncio
import time
import io
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

# Add core-engine to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.tts_router import TTSRouter, TTSError

print("=== Phase 4: TTS Unit & Integration Tests ===")


def test_voice_manifest_loading():
    """Verify voice manifest loads correctly."""
    router = TTSRouter()
    assert "voices" in router.voices
    assert "supported_languages" in router.voices
    assert len(router.voices["voices"]) >= 2
    print(f"✓ Voice manifest loaded with {len(router.voices['voices'])} voices.")
    print(f"  Supported languages: {router.voices['supported_languages']}")


def test_language_routing():
    """Verify language-aware voice routing."""
    router = TTSRouter()
    
    # Test supported languages
    for lang in ["en", "hi", "ja"]:
        voice = router.get_voice_for_language(lang)
        if lang in router.voices.get("supported_languages", []):
            assert voice is not None
            assert voice["language"] == lang
            print(f"✓ Voice found for '{lang}': {voice['voice_name']}")
    
    # Test invalid language
    try:
        router.get_voice_for_language("xx")
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected invalid language: {e}")


def test_input_validation():
    """Verify input validation rejects invalid inputs."""
    router = TTSRouter()
    
    # Test empty text
    try:
        asyncio.run(router.synthesize("", "en"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected empty text: {e}")
    
    # Test whitespace-only text
    try:
        asyncio.run(router.synthesize("   ", "en"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected whitespace-only text: {e}")
    
    # Test invalid language
    try:
        asyncio.run(router.synthesize("Hello", "xx"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected invalid language: {e}")
    
    # Test oversized text
    try:
        asyncio.run(router.synthesize("x" * 5001, "en"))
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        print(f"✓ Correctly rejected oversized text: {e}")


def test_synthesis_with_mock():
    """Verify synthesis produces valid WAV output with mocked Piper."""
    router = TTSRouter()
    
    # Create a mock voice that simulates writing audio to the wave file
    mock_voice = MagicMock()
    
    def mock_synthesize_wav(text, wav_file):
        """Simulate writing audio data to the wave file."""
        # Write 1000 samples of silence as fake audio
        fake_audio = b"\x00\x01" * 1000
        wav_file.writeframes(fake_audio)
    
    mock_voice.synthesize_wav = mock_synthesize_wav
    
    # Inject mock voice
    router._loaded_models["en"] = mock_voice
    
    result = asyncio.run(router.synthesize("Hello world", "en"))
    
    # Verify WAV structure
    assert len(result) > 44  # WAV header is 44 bytes
    
    # Parse WAV header
    with wave.open(io.BytesIO(result), "rb") as wf:
        assert wf.getnchannels() == 1, "Should be mono"
        assert wf.getsampwidth() == 2, "Should be 16-bit"
        assert wf.getframerate() == 22050, "Sample rate should be 22050"
        frames = wf.readframes(wf.getnframes())
        assert len(frames) > 0, "Should have audio data"
    
    print(f"✓ Synthesis produced valid WAV: {len(result)} bytes, mono, 16-bit, 22050 Hz")


def test_unsupported_language_error():
    """Verify unsupported language produces clear error."""
    router = TTSRouter()
    
    # Mock an empty manifest to simulate unsupported language
    router.voices = {"voices": [], "supported_languages": []}
    
    try:
        asyncio.run(router.synthesize("Hello", "vi"))
        raise AssertionError("Should have raised TTSError")
    except TTSError as e:
        print(f"✓ Unsupported language produced clear error: {e}")


def test_synthesis_cli(text: str = None, language: str = None, output_path: str = None):
    """CLI test mode: synthesize text to a WAV file."""
    if not text:
        text = "Hello, this is a test of the Polyglot Voice text to speech system."
    if not language:
        language = "en"
    if not output_path:
        output_path = str(Path(__file__).resolve().parent / "output_test.wav")
    
    router = TTSRouter()
    
    # Check if language is supported
    voice = router.get_voice_for_language(language)
    if not voice:
        print(f"Language '{language}' is not in the voice manifest.")
        print(f"Supported languages: {router.get_supported_languages()}")
        return
    
    print(f"\n--- TTS CLI Test ---")
    print(f"Text: {text}")
    print(f"Language: {language}")
    print(f"Voice: {voice['voice_name']}")
    print("Note: First run may take time to download voice model...")
    
    start_time = time.time()
    try:
        wav_bytes = asyncio.run(router.synthesize(text, language))
        elapsed = time.time() - start_time
        
        # Save to file
        with open(output_path, "wb") as f:
            f.write(wav_bytes)
        
        # Verify the output
        with wave.open(output_path, "rb") as wf:
            print(f"\n--- Output WAV Details ---")
            print(f"Channels: {wf.getnchannels()}")
            print(f"Sample Width: {wf.getsampwidth()} bytes")
            print(f"Sample Rate: {wf.getframerate()} Hz")
            print(f"Frames: {wf.getnframes()}")
            print(f"Duration: {wf.getnframes() / wf.getframerate():.2f}s")
            print(f"File Size: {len(wav_bytes)} bytes")
            print(f"Synthesis Time: {elapsed:.3f}s")
            print(f"Output: {output_path}")
            print("-------------------------\n")
            print("✓ TTS CLI test completed successfully!")
            
    except Exception as e:
        print(f"TTS synthesis failed: {e}")
        print("Note: This may require downloading voice models on first run.")
        print("Check your internet connection and try again.")


if __name__ == "__main__":
    test_voice_manifest_loading()
    test_language_routing()
    test_input_validation()
    test_synthesis_with_mock()
    test_unsupported_language_error()
    # Uncomment the line below to test actual synthesis (requires model download)
    # test_synthesis_cli()
    print("\nAll Phase 4 unit tests completed with 0 errors!")
    print("Run test_synthesis_cli() separately to test actual speech synthesis.")