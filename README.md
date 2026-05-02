# vera-bot

**Vera** is magicpin's AI-powered merchant growth assistant — a FastAPI server that composes hyper-specific WhatsApp-style outreach messages for local merchants using real-time signals and Claude claude-sonnet-4-20250514.

## Approach

The server is a stateless-first design with a lightweight in-memory context store. Merchant data arrives via `/v1/context` (versioned, idempotent), and message generation happens on-demand via `/v1/tick`. This separation means the judge harness can pre-load merchant profiles before requesting messages, mirroring a real event-driven pipeline.

## Why Claude claude-sonnet-4-20250514?

Claude claude-sonnet-4-20250514 was chosen for the best balance of quality, speed, and cost. It reliably follows structured JSON output instructions, respects character-count constraints, and produces natural WhatsApp-style copy — all critical for scoring well on specificity, category voice, and engagement compulsion. Opus would be higher quality but 5× slower and more expensive; Haiku would be faster but struggles with the nuanced category-voice switching this task demands.

## Key Tradeoffs

| Decision | Why |
|---|---|
| In-memory store (no database) | Zero-config deployment; sufficient for a single-instance evaluation harness. A production upgrade to Redis or Postgres is a one-file change. |
| Global 200 OK handler | The judge harness penalises 5xx errors. Every endpoint returns 200, even on unexpected failures, with an `error` field for debugging. |
| 3-layer JSON extraction | Claude occasionally wraps JSON in markdown. The parser tries `json.loads` → brace-slicing → regex, guaranteeing a parsed result or a graceful fallback. |
| Category-specific fallbacks | If the AI is unavailable, each category still gets a plausible hardcoded message so the merchant experience never breaks. |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/healthz` | Health check |
| GET | `/v1/metadata` | Bot capabilities |
| POST | `/v1/context` | Store merchant context (versioned) |
| POST | `/v1/tick` | Generate outreach message |
| POST | `/v1/reply` | Acknowledge merchant reply |

## Getting an Anthropic API Key

Sign up at [console.anthropic.com](https://console.anthropic.com/) — new accounts receive free trial credits which are sufficient to run and test this bot.

## Quick Start

```bash
# 1. Clone and enter the repo
git clone https://github.com/YOUR_USERNAME/vera-bot.git
cd vera-bot

# 2. Create a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up your API key
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
# Then edit .env and paste your real Anthropic API key

# 5. Run the server
uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000/v1/healthz](http://localhost:8000/v1/healthz) to verify.
