# Skylark BI Agent — Frontend (Sahara Night theme)

Single-page React + Vite chat UI, restyled to match the "Sahara Night" design
(seed color `#C2652A`), wired directly to your existing FastAPI backend.
No other pages (Work Orders / Pipeline Health / Reports / Data Quality Hub)
are included — those aren't backed by real endpoints yet, so they were left
out on purpose rather than built as non-functional mockups.

## How to swap this in

1. Delete your existing `frontend/` folder in the project root.
2. Drop this folder in as `frontend/` in its place.
3. `cd frontend && npm install`
4. Confirm `.env` points at your backend: `VITE_API_BASE_URL=http://localhost:8000`
5. `npm run dev`

## What it wires up (matches `main.py` exactly)

| UI element | Endpoint | Method |
|---|---|---|
| Chat input + suggestion chips | `/chat` | `POST` with `{ message, history }` |
| "Refresh data from monday.com" button | `/refresh` | `POST` |
| "Generate leadership update" button | `/leadership-update` | `GET` (plain text/markdown response) |
| Connection status dot (top left) | `/health/monday` | `GET`, checked once on load |

## One assumption to verify

`main.py` doesn't fix the exact JSON shape `query_engine.answer_question()`
returns for `/chat` — that's defined inside `query_engine.py`, which wasn't
shared. `src/App.jsx` (see `extractAnswerText`) checks these keys, in order:
`answer`, `response`, `message`, `text`, `result`. If none match, it falls
back to showing the raw JSON in the chat bubble instead of silently dropping
the reply, so you'll notice immediately if this needs adjusting — it's a
one-line fix in `extractAnswerText`.

## Design tokens

All colors/spacing live as CSS variables at the top of `src/index.css`
(`:root { --primary: #c2652a; ... }`) — change them there rather than
hunting through component classes.
