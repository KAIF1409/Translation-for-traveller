import sys
import json
from pathlib import Path

# Add core-engine to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import settings
from app.main import app, health_check
from app.services.base_interfaces import BaseSTTProvider, BaseTranslationProvider, BaseTTSProvider
from app.services.vad_service import VADService
from app.services.stt_router import STTRouter
from app.services.llm_orchestrator import LLMOrchestrator
from app.services.tts_router import TTSRouter

print("=== Phase 1 Comprehensive Verification ===")

# 1. FastAPI and Health Check Verification
print("FastAPI Title:", app.title)
print("Default Host/Port:", f"{settings.HOST}:{settings.PORT}")
print("Configured Gemini Model:", settings.GEMINI_MODEL)
print("Configured Groq LLM Model:", settings.GROQ_LLM_MODEL)

# Direct asynchronous invocation of health check
import asyncio
async def test_health():
    res = await health_check()
    print("Health Check Response:", res)
    assert res["status"] == "ok"
    assert res["service"] == "polyglot-voice-engine"

asyncio.run(test_health())

# 2. Base Interfaces and Service Placeholders Verification
print("\nChecking Services and Interfaces:")
assert issubclass(STTRouter, BaseSTTProvider), "STTRouter must subclass BaseSTTProvider"
assert issubclass(LLMOrchestrator, BaseTranslationProvider), "LLMOrchestrator must subclass BaseTranslationProvider"
assert issubclass(TTSRouter, BaseTTSProvider), "TTSRouter must subclass BaseTTSProvider"
print("✓ All router/orchestrator classes correctly subclass their abstract base interfaces.")

# 3. Directory Layout and Key File Verification
root_dir = Path(__file__).resolve().parent.parent
expected_paths = [
    "client/lib/main.dart",
    "client/lib/screens/.gitkeep",
    "client/lib/services/.gitkeep",
    "client/lib/widgets/.gitkeep",
    "core-engine/app/main.py",
    "core-engine/app/config.py",
    "core-engine/app/services/base_interfaces.py",
    "core-engine/app/services/vad_service.py",
    "core-engine/app/services/stt_router.py",
    "core-engine/app/services/llm_orchestrator.py",
    "core-engine/app/services/tts_router.py",
    "core-engine/requirements.txt",
    "core-engine/.env.example",
    "cultural-matrix/ja.json",
    "cultural-matrix/ko.json",
    "cultural-matrix/hi.json",
    "cultural-matrix/vi.json",
    "cultural-matrix/schema.json",
    "docs/SETUP.md",
    "docs/API_KEYS.md",
    ".gitignore"
]

print("\nVerifying Scaffolding Files:")
for rel_path in expected_paths:
    p = root_dir / rel_path
    if p.exists():
        print(f"✓ Found: {rel_path}")
    else:
        raise FileNotFoundError(f"Missing required scaffold path: {rel_path}")

# 4. Cultural Matrix JSON Structure Verification
print("\nVerifying Cultural Matrix Config files:")
matrix_dir = root_dir / "cultural-matrix"
with open(matrix_dir / "schema.json", "r", encoding="utf-8") as sf:
    schema = json.load(sf)
    assert "tone_axis" in schema["properties"], "schema.json must specify tone_axis"

required_tones = ["formal", "business", "casual"]
languages = ["ja", "ko", "hi", "vi"]

for lang in languages:
    lang_file = matrix_dir / f"{lang}.json"
    with open(lang_file, "r", encoding="utf-8") as lf:
        config = json.load(lf)
        assert config["language_code"] == lang, f"language_code mismatch in {lang}.json"
        assert "language_name" in config, f"Missing language_name in {lang}.json"
        assert "tone_axis" in config, f"Missing tone_axis in {lang}.json"
        
        tone_axis = config["tone_axis"]
        for tone in required_tones:
            assert tone in tone_axis, f"Missing tone '{tone}' in tone_axis of {lang}.json"
            tone_cfg = tone_axis[tone]
            assert "system_prompt_addon" in tone_cfg, f"Missing system_prompt_addon for tone '{tone}' in {lang}.json"
            assert "rules" in tone_cfg, f"Missing rules for tone '{tone}' in {lang}.json"
            assert isinstance(tone_cfg["rules"], list), f"Rules must be a list for tone '{tone}' in {lang}.json"
            
        print(f"✓ {lang}.json validated successfully with all tones (formal, business, casual) and rules.")

print("\nPhase 1 verification completed with 0 errors!")

