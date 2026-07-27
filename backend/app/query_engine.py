"""
The agent's brain. Two-stage design, chosen deliberately over "let the
LLM write pandas code and exec() it":

  Stage 1 (Understand):  LLM reads the question + a compact schema/value
                          summary of both boards, and returns a strict
                          JSON "intent" — which board(s), which filters,
                          which metric, optional group-by, and whether it
                          needs to ask the user a clarifying question
                          instead of answering.

  Stage 2 (Execute):     Plain, deterministic pandas code applies that
                          intent to the cleaned dataframes. No LLM-written
                          code ever touches the data — safer, cheaper,
                          reproducible, and debuggable.

  Stage 3 (Explain):     LLM turns the computed numbers + data-quality
                          notes into a founder-readable answer: the
                          number, why it matters, and any caveats.

This keeps the "reasoning" (stage 1/3) separate from the "arithmetic"
(stage 2), so a wrong LLM guess can never silently corrupt a number —
worst case it filters the wrong slice, which is visible in the response.
"""
import pandas as pd

from app import llm_client
from app.data_cleaning import data_quality_report

DEALS_KEY_COLUMNS = ["Deal Status", "Sector", "Deal Value", "Deal Stage", "Tentative Close Date"]
WO_KEY_COLUMNS = ["Execution Status", "Sector", "Billing Status", "Amount Receivable (Masked)"]


def _schema_summary(deals: pd.DataFrame, wo: pd.DataFrame) -> str:
    def col_summary(df, cols):
        lines = []
        for c in cols:
            if c in df.columns:
                vals = df[c].dropna().unique().tolist()
                vals = [str(v) for v in vals][:15]
                lines.append(f"  - {c}: e.g. {vals}")
        return "\n".join(lines)

    return f"""
BOARD "deals" ({len(deals)} rows) — sales pipeline. Columns:
{col_summary(deals, ["Deal Status", "Sector", "Deal Stage", "Owner code", "Deal Value", "Tentative Close Date", "Close Date (A)", "Created Date", "Product deal"])}

BOARD "work_orders" ({len(wo)} rows) — project execution & billing. Columns:
{col_summary(wo, ["Execution Status", "Sector", "Nature of Work", "Type of Work", "Invoice Status", "Billing Status", "WO Status (billed)", "Amount in Rupees (Incl of GST) (Masked)", "Amount Receivable (Masked)", "Probable Start Date", "Probable End Date"])}

Today's business quarter context: dates are ISO (YYYY-MM-DD) where parseable, else null.
""".strip()


INTENT_SYSTEM_PROMPT = """You are the query-understanding stage of a business intelligence agent \
for Skylark Drones, a drone-survey company. Founders ask informal business questions; you convert \
them into a strict JSON "intent" object describing how to answer from two monday.com boards: \
"deals" (sales pipeline) and "work_orders" (project execution/billing).

Return ONLY valid JSON, no prose, matching this shape:
{
  "needs_clarification": false,
  "clarifying_question": null,
  "boards": ["deals"],                 // one or both of "deals", "work_orders"
  "filters": {                          // board -> {column: [allowed values]} ; omit if none
      "deals": {"Sector": ["Renewables"]}
  },
  "date_filters": {                     // board -> {column, on_or_after, on_or_before} (ISO dates), omit if none
      "deals": {"column": "Tentative Close Date", "on_or_after": "2026-01-01", "on_or_before": "2026-03-31"}
  },
  "metric": "sum",                      // one of: sum, count, avg, list
  "metric_column": "Deal Value",        // numeric column for sum/avg; omit for count
  "group_by": "Deal Status",            // optional column to break results down by
  "intent_summary": "short plain-english restatement of what will be computed"
}

Rules:
- Only set needs_clarification=true if the question is genuinely ambiguous in a way that would change \
the numeric answer (e.g. "this quarter" with no reference date and no fiscal year convention stated — \
in that case ASSUME calendar quarter from context date given and do NOT ask, unless the sector/board is \
also unclear). Prefer making a reasonable, stated assumption over asking, and note the assumption in intent_summary.
- Only use column names and values that appear in the schema summary provided. Never invent columns.
- If the question spans both pipeline and execution (e.g. "pipeline vs delivery"), set boards to both.
- If the question is a request to prepare a "leadership update" / "exec summary" / "board update", set \
metric to "summary" and boards to both — a downstream step will build a structured report, not a single number.
"""


ANSWER_SYSTEM_PROMPT = """You are a business intelligence analyst answering a founder's question at \
Skylark Drones, a drone survey/inspection company, using data pulled live from monday.com. \
You are given the computed result and known data-quality caveats. Write a concise, founder-ready answer:
- Lead with the headline number/insight.
- Add 1-3 sentences of context (trend, biggest contributor, risk) if the data supports it.
- If data-quality issues materially affect the number (e.g. many rows missing the filtered column, or \
missing key financials), state that caveat plainly and briefly.
- Never invent numbers not present in the provided result. If the result is empty, say so and suggest a next step.
- Keep it tight: no more than ~120 words unless it's a leadership update.
"""


async def extract_intent(question: str, deals: pd.DataFrame, wo: pd.DataFrame, history: list[dict]) -> dict:
    schema = _schema_summary(deals, wo)
    messages = [
        {"role": "system", "content": INTENT_SYSTEM_PROMPT},
        {"role": "system", "content": f"SCHEMA:\n{schema}"},
    ]
    for h in history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": question})
    return await llm_client.chat_json(messages)


def _apply_filters(df: pd.DataFrame, filters: dict | None, date_filters: dict | None) -> pd.DataFrame:
    out = df.copy()
    if filters:
        for col, allowed in filters.items():
            if col in out.columns:
                out = out[out[col].isin(allowed)]
    if date_filters:
        col = date_filters.get("column")
        if col and col in out.columns:
            s = pd.to_datetime(out[col], errors="coerce")
            if date_filters.get("on_or_after"):
                s_after = pd.to_datetime(date_filters["on_or_after"])
                out = out[s >= s_after]
                s = pd.to_datetime(out[col], errors="coerce")
            if date_filters.get("on_or_before"):
                s_before = pd.to_datetime(date_filters["on_or_before"])
                out = out[s <= s_before]
    return out


def execute_intent(intent: dict, deals: pd.DataFrame, wo: pd.DataFrame) -> dict:
    boards = intent.get("boards", ["deals"])
    results = {}
    caveats = []

    for board_name in boards:
        df = deals if board_name == "deals" else wo
        key_cols = DEALS_KEY_COLUMNS if board_name == "deals" else WO_KEY_COLUMNS

        f = (intent.get("filters") or {}).get(board_name)
        df_filters = (f if isinstance(f, dict) else None)
        d = (intent.get("date_filters") or {}).get(board_name)

        filtered = _apply_filters(df, df_filters, d)

        metric = intent.get("metric", "count")
        metric_col = intent.get("metric_column")
        group_by = intent.get("group_by")

        board_result: dict = {"matched_rows": len(filtered), "total_rows": len(df)}

        if metric == "summary":
            board_result["preview"] = filtered.head(10).to_dict(orient="records")
        elif group_by and group_by in filtered.columns:
            if metric == "count":
                grouped = filtered.groupby(group_by).size().sort_values(ascending=False)
            elif metric in ("sum", "avg") and metric_col in filtered.columns:
                agg = "sum" if metric == "sum" else "mean"
                grouped = filtered.groupby(group_by)[metric_col].agg(agg).sort_values(ascending=False)
            else:
                grouped = filtered.groupby(group_by).size().sort_values(ascending=False)
            board_result["breakdown"] = {str(k): (round(v, 2) if isinstance(v, float) else v)
                                          for k, v in grouped.items()}
        else:
            if metric == "sum" and metric_col in filtered.columns:
                board_result["value"] = round(float(filtered[metric_col].dropna().sum()), 2)
            elif metric == "avg" and metric_col in filtered.columns:
                board_result["value"] = round(float(filtered[metric_col].dropna().mean()), 2) if len(filtered) else None
            elif metric == "list":
                name_col = "Deal Name" if board_name == "deals" else "Deal name masked"
                board_result["items"] = filtered[name_col].dropna().tolist()[:25] if name_col in filtered.columns else []
            else:
                board_result["value"] = int(len(filtered))

        results[board_name] = board_result
        caveats.append(data_quality_report(filtered if len(filtered) else df, board_name, key_cols))

    return {"results": results, "data_quality": caveats}


async def synthesize_answer(question: str, intent: dict, execution: dict) -> str:
    messages = [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Founder's question: {question}\n\n"
            f"Interpreted intent: {intent.get('intent_summary', '')}\n\n"
            f"Computed result (JSON): {execution['results']}\n\n"
            f"Data quality notes: {execution['data_quality']}"
        )},
    ]
    return await llm_client.chat(messages)


async def answer_question(question: str, deals: pd.DataFrame, wo: pd.DataFrame, history: list[dict]) -> dict:
    intent = await extract_intent(question, deals, wo, history)

    if intent.get("needs_clarification"):
        return {
            "type": "clarification",
            "message": intent.get("clarifying_question", "Could you clarify your question?"),
            "intent": intent,
        }

    execution = execute_intent(intent, deals, wo)
    answer = await synthesize_answer(question, intent, execution)

    return {
        "type": "answer",
        "message": answer,
        "intent": intent,
        "execution": execution,
    }
