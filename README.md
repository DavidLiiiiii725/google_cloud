# Agent Greenhouse

Pixel-art AI greenhouse monitoring demo. Gemini (Vertex AI) + FastAPI + MongoDB Atlas + procedural canvas frontend.

## Setup

```powershell
# 1. Create venv & install deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Authenticate to Google Cloud (one-time)
gcloud auth application-default login
gcloud config set project centered-radio-497405-u9

# 3. Copy .env.example to .env and fill in MONGODB_URI
copy .env.example .env

# 4. Seed historical baseline (one-time)
python -m backend.seed

# 5. Run backend
uvicorn backend.main:app --reload

# 6. Open index.html in a browser (double-click works)
```

## Demo flow
See `agent_greenhouse_prompt_v3.md` § "Demo script".
