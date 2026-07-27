"""
Central configuration. All secrets/IDs come from environment variables —
never hardcode tokens or board IDs.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # --- monday.com ---
    MONDAY_API_TOKEN: str = os.getenv("MONDAY_API_TOKEN", "")
    MONDAY_API_URL: str = "https://api.monday.com/v2"
    DEALS_BOARD_ID: str = os.getenv("DEALS_BOARD_ID", "")
    WORK_ORDERS_BOARD_ID: str = os.getenv("WORK_ORDERS_BOARD_ID", "")

    # --- LLM (OpenAI-compatible: works with Groq, OpenAI, OpenRouter) ---
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

    # --- Gemini fallback (optional; used if LLM_PROVIDER=gemini) ---
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai_compatible")  # or "gemini"
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # --- App ---
    CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    ALLOWED_ORIGINS: list = os.getenv("ALLOWED_ORIGINS", "*").split(",")


settings = Settings()
