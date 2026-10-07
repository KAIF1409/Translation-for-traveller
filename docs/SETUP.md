# Polyglot Voice - Setup Guide

## Local Environment Prerequisites
- Python 3.10+
- Flutter SDK (for client development)
- Git

## Backend Setup (Core Engine)

1. Navigate to the backend directory:
   ```bash
   cd core-engine
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```

3. Install initial requirements:
   ```bash
   pip install -r requirements.txt
   ```

4. Configure environment variables:
   Copy `.env.example` to `.env` and fill in API keys if available:
   ```bash
   copy .env.example .env
   ```

5. Run the development server:
   ```bash
   uvicorn app.main:app --reload
   ```

6. Verify server health:
   Visit `http://localhost:8000/health` in your browser or run:
   ```bash
   curl http://localhost:8000/health
   ```
