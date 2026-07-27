# Decision Log

## Key assumptions

1. **"Text" column values, not raw typed values.** monday.com's GraphQL API
   returns each column's raw `value` (type-specific JSON) or a rendered
   `text` string. I chose `text` throughout — it already applies monday's own
   formatting per column type, and founder questions map to what's visibly on
   the board, not to internal type representations. Trade-off: slightly less
   precise for things like sub-item relations, but far simpler and more robust
   against whatever column types get chosen during board setup.

2. **"This quarter" and similar relative-time phrases resolve to calendar
   quarters from the current date**, not a fiscal year convention, since none
   was specified. The agent states this assumption in its `intent_summary`
   rather than always stopping to ask — asking on every relative date phrase
   would make the agent tedious to use. It *does* ask when a question is
   ambiguous in a way that would change which numbers get pulled (e.g. sector
   name not matching any real value in the data).

3. **Sample data quirks reflect real production quirks.** I inspected both
   CSVs directly: stray embedded header rows (a data row where e.g.
   `Deal Status == "Deal Status"`, left over from copy-pasted headers), 12
   sector-label variants that collapse to fewer canonical sectors, dates in
   mixed formats, and money fields as `"5360 HA"`-style text with units. I
   built the cleaning layer against these *actual* patterns rather than
   generic "handle nulls" boilerplate, since the assignment explicitly says
   the data is real-world messy and evaluators are likely checking whether
   the cleaning logic actually matches what's in the sheet.

4. **"Amount Receivable" and similar masked financial columns are treated as
   directly usable numbers** once cleaned, on the assumption the masking
   preserves relative magnitude (useful for founder-level trend questions)
   even if absolute values are altered.

## Architecture trade-offs

**Three-stage agent (Understand → Execute → Explain) instead of one LLM call
with function-calling, or letting the LLM write/execute pandas code directly.**
- *Why not raw code-gen:* LLM-authored pandas run against live financial data
  is a real risk — a subtly wrong `groupby` or off-by-one filter silently
  produces a wrong number that looks confident. Separating "decide what to
  compute" (LLM, stage 1) from "compute it" (deterministic Python, stage 2)
  means a bad LLM guess is visible as a wrong *filter selection*, not a
  corrupted *number* — and it's reproducible/testable without an LLM at all
  (see the pandas-only tests I ran during development).
- *Trade-off:* this constrains the agent to a fixed vocabulary of
  filter/metric/group-by operations. Genuinely novel analytical questions
  (e.g. "what's the correlation between deal size and days-to-close?")
  aren't expressible in the current intent schema. Given the 6-hour scope, I
  optimized for reliability on the founder-question patterns in the brief
  over open-ended analytical flexibility.

**JSON-mode prompting instead of native function-calling / tool schemas.**
Chosen for portability — Groq, Gemini, and OpenAI all support constrained
JSON output, but their function-calling schemas differ enough that supporting
"any free LLM the user has access to" cheaply favored a provider-agnostic
JSON contract over provider-specific tool definitions.

**In-memory cache (5 min TTL) instead of a database.** The assignment is
read-only against monday.com, so there's no write-path needing persistence.
A cache avoids hammering the API on every keystroke while staying simple to
reason about and deploy (no DB to provision). Trade-off: cache is per-process,
so a multi-instance deployment would see divergent cache windows — fine at
this scale, would move to Redis if this became a real multi-user product.

**Two-stage cleaning that never raises.** Every cleaning function degrades to
`None`/`NaN` on unparseable input rather than throwing, so one bad row never
crashes an entire query. The `data_quality_report()` function then surfaces
*exactly* which columns are how incomplete for the specific slice of data a
question touched, and that gets threaded into the final LLM answer as a
caveat — rather than a generic disclaimer, it's a report-accurate one
(e.g. "'Billing Status' is missing on 84% of matched rows").

## How I interpreted "prepare data for leadership updates"

I read this as: on request, generate a structured, close-to-paste-ready
markdown brief — pipeline health (open/won/dead counts and value, by sector
and stage), execution status breakdown, and billing/collections figures
(invoiced vs. collected vs. outstanding) — with a short LLM-written narrative
paragraph on top calling out the single biggest positive and biggest risk,
and an explicit data-quality caveats section. The numeric block is
deterministic (same three-stage discipline as the chat path — the LLM
narrates, it doesn't compute), so the report is trustworthy enough to
actually hand to a founder, not just a nicely-worded guess. It's exposed both
as its own endpoint/button and as something the chat agent can route to if
a user asks for one conversationally.

## What I'd do differently with more time

- **Real function-calling / tool-use** instead of JSON-mode prompting, once
  committing to a single provider, for more reliable structured output on
  edge-case phrasing.
- **A proper eval set**: a fixed list of ~30 founder questions with expected
  numeric answers computed by hand from the sample data, run against the
  agent on every change, to catch intent-extraction regressions automatically
  instead of spot-checking.
- **Persisted query history / feedback loop** so founders could flag a wrong
  answer and the agent could learn the correct column mapping for that phrase.
- **Redis-backed cache + webhook-based invalidation** (monday.com supports
  webhooks on item/column changes) instead of polling on a TTL, so the agent
  reflects board edits within seconds rather than up to 5 minutes.
- **Confidence signaling**: currently every answer reads with the same
  tone whether it's based on 95% complete data or 40% complete data; I'd add
  a visible confidence badge driven by the data-quality report rather than
  burying it in a caveats footnote.
- **Multi-turn filter refinement**: right now each question re-derives intent
  from scratch (with recent history as context); a proper session-scoped
  filter state (e.g. "now break that down by owner" without repeating the
  sector/quarter) would feel more natural.
