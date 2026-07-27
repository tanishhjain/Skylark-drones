import time
import traceback

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.config import settings
from app.monday_client import get_monday_client, MondayAPIError
from app.data_cleaning import clean_deals, clean_work_orders
from app import query_engine
from app.leadership_report import build_markdown_report

app = FastAPI(title="Skylark BI Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Simple in-memory cache with TTL, refreshed from monday.com on demand ---
_cache = {"deals": None, "work_orders": None, "fetched_at": 0}


async def get_clean_data(force_refresh: bool = False):
    now = time.time()
    stale = (now - _cache["fetched_at"]) > settings.CACHE_TTL_SECONDS
    if force_refresh or stale or _cache["deals"] is None:
        client = get_monday_client()
        deals_raw = await client.fetch_board_items(settings.DEALS_BOARD_ID)
        wo_raw = await client.fetch_board_items(settings.WORK_ORDERS_BOARD_ID)
        _cache["deals"] = clean_deals(deals_raw)
        _cache["work_orders"] = clean_work_orders(wo_raw)
        _cache["fetched_at"] = now
    return _cache["deals"], _cache["work_orders"]


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []  # [{"role": "user"|"assistant", "content": "..."}]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/monday")
async def health_monday():
    try:
        client = get_monday_client()
        info = await client.test_connection()
        return {"status": "connected", "account": info}
    except MondayAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/refresh")
async def refresh():
    """Force a fresh pull from monday.com, bypassing cache."""
    try:
        deals, wo = await get_clean_data(force_refresh=True)
        return {
            "status": "refreshed",
            "deals_rows": len(deals),
            "work_orders_rows": len(wo),
            "fetched_at": _cache["fetched_at"],
        }
    except MondayAPIError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        deals, wo = await get_clean_data()
    except MondayAPIError as e:
        raise HTTPException(status_code=502, detail=f"monday.com error: {e}")

    try:
        result = await query_engine.answer_question(req.message, deals, wo, req.history)
        return result
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Agent error: {e}")


@app.get("/leadership-update", response_class=PlainTextResponse)
async def leadership_update():
    try:
        deals, wo = await get_clean_data()
    except MondayAPIError as e:
        raise HTTPException(status_code=502, detail=f"monday.com error: {e}")
    try:
        return await build_markdown_report(deals, wo)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Report generation error: {e}")
