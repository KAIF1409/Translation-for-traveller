# API Keys & Configuration Guide

Polyglot Voice uses environment variables for cloud provider credentials. Never commit real keys to source control.

## Required / Optional Credentials

### 1. Gemini API Key (`GEMINI_API_KEY`)
- **Primary LLM Provider**: Google Gemini 2.5 Flash (Free Tier)
- **Get Key**: Visit [Google AI Studio](https://aistudio.google.com/)
- **Configuration**: Add `GEMINI_API_KEY=your_key_here` in `core-engine/.env`

### 2. Groq API Key (`GROQ_API_KEY`)
- **Fallback LLM Provider & Cloud Whisper STT**: Groq Free Tier
- **Get Key**: Visit [Groq Console](https://console.groq.com/)
- **Configuration**: Add `GROQ_API_KEY=your_key_here` in `core-engine/.env`

## Security Best Practices
- `.env` files are excluded in `.gitignore`.
- If an API key is omitted, the engine defaults to local-only providers or returns standard configuration warnings.
