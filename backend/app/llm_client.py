"""
Thin LLM wrapper so the rest of the app never cares which provider is
behind it. Two providers are supported out of the box:

  - "openai_compatible": works with Groq, OpenRouter, or OpenAI itself —
    anything exposing the /chat/completions contract. This is the
    recommended free option (Groq).
  - "gemini": Google's Generative Language API, also free-tier friendly.

Switch providers purely via .env (LLM_PROVIDER=openai_compatible|gemini).
No other code changes required.

Free-tier providers (Groq in particular) enforce tight tokens-per-minute
limits and return HTTP 429 when exceeded. Rather than surfacing that as a
hard failure, we retry with a short backoff — 429s here are typically
self-clearing within a second or two, not a real outage.
"""
import asyncio
import json
import httpx

from app.config import settings

MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 3


class LLMError(Exception):
    pass


class LLMRateLimitError(LLMError):
    pass


async def _with_retry(fn):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await fn()
        except LLMRateLimitError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (attempt + 1))
    raise last_error


async def _chat_openai_compatible(messages: list[dict], json_mode: bool = False) -> str:
    if not settings.LLM_API_KEY:
        raise LLMError("LLM_API_KEY is not set. Add it to your .env file.")
    payload = {
        "model": settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async def call():
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.LLM_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code == 429:
            raise LLMRateLimitError(f"Rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    return await _with_retry(call)


async def _chat_gemini(messages: list[dict], json_mode: bool = False) -> str:
    if not settings.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set. Add it to your .env file.")

    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    convo = [m for m in messages if m["role"] != "system"]
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in convo
    ]
    body = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
    if json_mode:
        body["generationConfig"] = {"responseMimeType": "application/json"}

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.GEMINI_MODEL}:generateContent?key={settings.GEMINI_API_KEY}"
    )

    async def call():
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
        if resp.status_code == 429:
            raise LLMRateLimitError(f"Rate limited: {resp.text}")
        if resp.status_code != 200:
            raise LLMError(f"Gemini HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    return await _with_retry(call)


async def chat(messages: list[dict], json_mode: bool = False) -> str:
    try:
        if settings.LLM_PROVIDER == "gemini":
            return await _chat_gemini(messages, json_mode=json_mode)
        return await _chat_openai_compatible(messages, json_mode=json_mode)
    except LLMRateLimitError:
        raise LLMError(
            "The LLM provider's free-tier rate limit was hit and retries were "
            "exhausted. Please wait a few seconds and try again."
        )


async def chat_json(messages: list[dict]) -> dict:
    raw = await chat(messages, json_mode=True)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if raw.lower().startswith("json") else raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model did not return valid JSON: {e}\nRaw: {raw[:500]}")
