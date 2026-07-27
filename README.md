# Skylark BI Agent

A conversational business-intelligence agent for founder-level questions across
monday.com's **Deals** (sales pipeline) and **Work Orders** (project execution
& billing) boards. Read-only, dynamic — no CSV data is hardcoded; every answer
is computed live from monday.com via the GraphQL API.

## Architecture

```
frontend (React + Vite)  --HTTP-->  backend (FastAPI)  --GraphQL-->  monday.com
                                          |
                                          v
                                    LLM (Groq / Gemini)
```

**Backend** (`/backend`)
- `monday_client.py` — read-only GraphQL client, cursor-paginated, board-agnostic.
- `data_cleaning.py` — normalizes messy real-world data: drops stray embedded
  header rows, canonicalizes sector names, parses inconsistent date formats,
  strips currency/units from numeric fields, treats `"NONE"/"N/A"/"-"` etc. as
  null. Also produces a `data_quality_report` used to caveat every answer.
- `query_engine.py` — the agent's core loop, deliberately split into three
  stages so the LLM never touches raw numbers directly:
  1. **Understand** — LLM converts the founder's question into a strict JSON
     "intent" (board(s), filters, metric, group-by), using only column names
     it was actually shown, or asks a clarifying question if genuinely ambiguous.
  2. **Execute** — plain deterministic pandas applies that intent. No
     LLM-generated code ever runs against the data.
  3. **Explain** — LLM turns the computed numbers + data-quality notes into a
     founder-readable answer.
- `leadership_report.py` — the "prepare leadership updates" feature: a
  deterministic stats block (pipeline health, execution status, billing/
  collections) wrapped in a short LLM-written narrative, output as markdown.
- `main.py` — FastAPI routes: `/chat`, `/refresh`, `/leadership-update`,
  `/health`, `/health/monday`. In-memory cache (5 min TTL) avoids hitting
  monday.com on every keystroke.

**Frontend** (`/frontend`) — a minimal React chat interface: message thread,
data-quality caveats shown inline under answers, quick-suggestion chips, a
"Refresh data" button, and a one-click "Generate leadership update" button.

## Setting up monday.com (one-time, ~10 minutes)

1. **Create two boards** in your monday.com account:
   - `Deals` (or any name — you'll reference it by ID, not name)
   - `Work Orders`
2. **Import the CSVs** provided in `/sample_data`:
   - Board → `⋯` menu (top right) → **Import data** → **Excel/CSV** → upload
     `deals.csv` / `work_orders.csv`.
   - monday.com will ask you to map each spreadsheet column to a board
     column and infer a type (Text, Status, Number, Date, etc.) — accept its
     suggestions; the app's cleaning layer is resilient to type mismatches
     since we read the rendered `text` value of every column, not the raw
     type-specific value.
   - Recommended column types for best UX in monday.com itself (optional,
     the agent doesn't require these): `Deal Status` / `Execution Status` /
     `Billing Status` as **Status** columns; `Deal Value` and money columns as
     **Numbers**; date columns as **Date**.
3. **Get an API token**: your avatar (bottom-left) → **Administration** →
   **Connections** → **API**, or **Profile** → **Developers** → *My Access
   Tokens*. Copy it.
4. **Get each board's ID**: open the board, look at the URL —
   `https://<your-account>.monday.com/boards/1234567890` → `1234567890` is
   the ID.
5. Put both IDs and the token into `backend/.env` (see below).

## Getting a free LLM key

The agent needs an LLM for query understanding and narrative answers. It's
built against the OpenAI-compatible `/chat/completions` contract so any
provider using that shape works by just changing `.env` — no code changes.

**Recommended: Groq (free, fast, no card required)**
1. Go to https://console.groq.com/keys → create a key.
2. In `backend/.env`: `LLM_PROVIDER=openai_compatible`,
   `LLM_BASE_URL=https://api.groq.com/openai/v1`,
   `LLM_MODEL=llama-3.3-70b-versatile`, `LLM_API_KEY=<your key>`.

**Alternative: Google Gemini (also free)**
1. Go to https://aistudio.google.com/apikey → create a key.
2. In `backend/.env`: `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=<your key>`,
   `GEMINI_MODEL=gemini-2.5-flash`.

## Local setup

**Backend**
```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in MONDAY_API_TOKEN, board IDs, LLM_API_KEY
uvicorn app.main:app --reload --port 8000
```
Check it worked: `curl http://localhost:8000/health/monday`

**Frontend**
```bash
cd frontend
npm install
cp .env.example .env        # set VITE_API_BASE_URL=http://localhost:8000
npm run dev
```
Open http://localhost:5173

## Deploying (for the hosted-prototype deliverable)

- **Backend** → [Render](https://render.com) (free tier): New → Web Service →
  point at this repo/`backend` folder → it will pick up the `Dockerfile`
  automatically. Add the same env vars from `.env.example` in Render's
  dashboard (Environment tab). Note the deployed URL.
- **Frontend** → [Vercel](https://vercel.com): New Project → point at
  `frontend` folder → set `VITE_API_BASE_URL` to your Render backend URL in
  Vercel's Environment Variables → deploy.
- Once both are live, update the backend's `ALLOWED_ORIGINS` env var to your
  Vercel URL (instead of `*`) and redeploy the backend.

Both have generous free tiers sufficient for this assignment.

## API reference

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | `{message, history}` → agent answer or clarifying question |
| POST | `/refresh` | Force a fresh pull from monday.com (bypass cache) |
| GET | `/leadership-update` | Markdown leadership brief, generated live |
| GET | `/health` | Liveness check |
| GET | `/health/monday` | Verifies the monday.com token/connection |

## Repo layout

```
backend/            FastAPI app, monday.com client, cleaning + agent logic
frontend/            React chat UI
sample_data/         The two source CSVs (for reference / re-import only —
                      never read at runtime, all data comes from monday.com)
DECISION_LOG.md
```

See `DECISION_LOG.md` for assumptions, trade-offs, and what I'd do with more time.
