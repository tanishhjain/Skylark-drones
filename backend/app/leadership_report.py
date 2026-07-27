"""
Interpretation of the optional requirement: "The agent should help prepare
data for leadership updates."

We treat this as: on request, generate a structured, ready-to-paste
markdown brief (pipeline health, won/lost, sector performance, execution
status, billing/collections risk) with explicit data-quality caveats —
something a founder could drop into a board update or Slack digest with
light editing. This is deterministic (not LLM-narrated numbers) for the
stat block, with a short LLM-written headline/narrative on top so it
reads like a human wrote it. See DECISION_LOG.md for the full rationale.
"""
import pandas as pd
from datetime import date

from app import llm_client
from app.data_cleaning import data_quality_report

NARRATIVE_PROMPT = """You are drafting the opening narrative (3-5 sentences) for a founder-facing \
weekly leadership update at Skylark Drones, a drone survey company. You are given computed pipeline \
and execution stats. Write a punchy, plain-English summary highlighting the single biggest positive \
and the single biggest risk. No fluff, no bullet points here — just the narrative paragraph. Do not \
invent any numbers beyond what's given."""


def _safe_sum(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns:
        return 0.0
    return round(float(df[col].dropna().sum()), 2)


def build_stats(deals: pd.DataFrame, wo: pd.DataFrame) -> dict:
    stats = {"generated_on": date.today().isoformat()}

    if not deals.empty:
        stats["pipeline_by_status"] = deals["Deal Status"].value_counts(dropna=False).to_dict()
        stats["pipeline_value_by_status"] = (
            deals.groupby("Deal Status")["Deal Value"].sum().round(2).to_dict()
            if "Deal Value" in deals.columns else {}
        )
        stats["pipeline_value_by_sector"] = (
            deals[deals["Deal Status"] == "Open"].groupby("Sector")["Deal Value"].sum().round(2).to_dict()
            if "Deal Value" in deals.columns and "Sector" in deals.columns else {}
        )
        stats["deals_by_stage"] = deals["Deal Stage"].value_counts(dropna=False).to_dict()
        stats["open_deal_count"] = int((deals["Deal Status"] == "Open").sum())
        stats["won_deal_count"] = int((deals["Deal Status"] == "Won").sum())
        stats["dead_deal_count"] = int((deals["Deal Status"] == "Dead").sum())

    if not wo.empty:
        stats["execution_status_breakdown"] = wo["Execution Status"].value_counts(dropna=False).to_dict()
        stats["total_invoiced_incl_gst"] = _safe_sum(wo, "Amount in Rupees (Incl of GST) (Masked)")
        stats["total_collected_incl_gst"] = _safe_sum(wo, "Collected Amount in Rupees (Incl of GST.) (Masked)")
        stats["total_amount_receivable"] = _safe_sum(wo, "Amount Receivable (Masked)")
        stats["work_orders_by_sector"] = wo["Sector"].value_counts(dropna=False).to_dict()
        if "Billing Status" in wo.columns:
            stats["billing_status_breakdown"] = wo["Billing Status"].value_counts(dropna=False).to_dict()

    stats["data_quality"] = [
        data_quality_report(deals, "deals",
                             ["Deal Status", "Sector", "Deal Value", "Deal Stage"]),
        data_quality_report(wo, "work_orders",
                             ["Execution Status", "Sector", "Billing Status", "Amount Receivable (Masked)"]),
    ]
    return stats


async def build_markdown_report(deals: pd.DataFrame, wo: pd.DataFrame) -> str:
    stats = build_stats(deals, wo)

    narrative = ""
    try:
        messages = [
            {"role": "system", "content": NARRATIVE_PROMPT},
            {"role": "user", "content": f"Stats: {stats}"},
        ]
        narrative = await llm_client.chat(messages)
    except Exception:
        narrative = "(Narrative summary unavailable — LLM call failed; see raw stats below.)"

    dq_lines = []
    for report in stats.get("data_quality", []):
        if report.get("issues"):
            dq_lines.append(f"- **{report['board']}** ({report['row_count']} rows): " + "; ".join(report["issues"]))

    md = f"""# Skylark Drones — Leadership Update
_Generated {stats['generated_on']} from live monday.com data_

## Summary
{narrative}

## Pipeline Health
- Open deals: **{stats.get('open_deal_count', 'N/A')}**
- Won deals: **{stats.get('won_deal_count', 'N/A')}**
- Dead/lost deals: **{stats.get('dead_deal_count', 'N/A')}**
- Pipeline value by status (₹, masked values): {stats.get('pipeline_value_by_status', {})}
- Open pipeline value by sector: {stats.get('pipeline_value_by_sector', {})}
- Deals by stage: {stats.get('deals_by_stage', {})}

## Execution & Delivery
- Work orders by execution status: {stats.get('execution_status_breakdown', {})}
- Work orders by sector: {stats.get('work_orders_by_sector', {})}

## Billing & Collections
- Total invoiced (incl. GST): ₹{stats.get('total_invoiced_incl_gst', 0):,.2f}
- Total collected (incl. GST): ₹{stats.get('total_collected_incl_gst', 0):,.2f}
- Total amount receivable (outstanding): ₹{stats.get('total_amount_receivable', 0):,.2f}
- Billing status breakdown: {stats.get('billing_status_breakdown', {})}

## Data Quality Caveats
{chr(10).join(dq_lines) if dq_lines else "- No material data quality issues detected in key fields."}

---
_This report was generated by the BI agent directly from monday.com. Figures reflect masked/sample \
values and should be reconciled against source-of-truth financials before external distribution._
"""
    return md
