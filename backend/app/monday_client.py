"""
Thin, read-only client around monday.com's GraphQL API (v2).

Design notes:
- Pagination via `next_items_page` cursor (monday's recommended approach
  for boards with >100 items — Work Orders/Deals boards will exceed that
  once real data grows).
- Column values are requested as `text` (human-readable rendering) rather
  than raw `value` JSON, since founders' questions map to what's visibly
  on the board, and `text` already handles monday's per-column-type
  formatting for us.
- Raises a typed error the API layer can turn into a clean HTTP response,
  instead of leaking raw GraphQL error payloads to the frontend.
"""
import httpx
from typing import Any

from app.config import settings


class MondayAPIError(Exception):
    pass


class MondayClient:
    def __init__(self):
        if not settings.MONDAY_API_TOKEN:
            raise MondayAPIError(
                "MONDAY_API_TOKEN is not set. Add it to your .env file."
            )
        self.headers = {
            "Authorization": settings.MONDAY_API_TOKEN,
            "Content-Type": "application/json",
            "API-Version": "2024-10",
        }

    async def _post(self, query: str, variables: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                settings.MONDAY_API_URL,
                headers=self.headers,
                json={"query": query, "variables": variables or {}},
            )
        if resp.status_code != 200:
            raise MondayAPIError(f"monday.com HTTP {resp.status_code}: {resp.text}")
        payload = resp.json()
        if "errors" in payload:
            raise MondayAPIError(f"monday.com GraphQL error: {payload['errors']}")
        return payload["data"]

    async def fetch_board_items(self, board_id: str) -> list[dict[str, Any]]:
        """
        Fetch ALL items from a board, following cursor pagination.
        Returns a list of {column_title: text_value} dicts, one per item,
        plus '_item_name' and '_item_id'.
        """
        if not board_id:
            raise MondayAPIError("Board ID not configured.")

        items: list[dict[str, Any]] = []
        cursor = None

        first_query = """
        query ($boardId: ID!) {
          boards(ids: [$boardId]) {
            name
            items_page(limit: 100) {
              cursor
              items {
                id
                name
                column_values { id text column { title } }
              }
            }
          }
        }
        """
        next_query = """
        query ($cursor: String!) {
          next_items_page(limit: 100, cursor: $cursor) {
            cursor
            items {
              id
              name
              column_values { id text column { title } }
            }
          }
        }
        """

        data = await self._post(first_query, {"boardId": board_id})
        boards = data.get("boards") or []
        if not boards:
            raise MondayAPIError(
                f"Board {board_id} not found or token lacks access to it."
            )
        page = boards[0]["items_page"]
        items.extend(page["items"])
        cursor = page["cursor"]

        while cursor:
            data = await self._post(next_query, {"cursor": cursor})
            page = data["next_items_page"]
            items.extend(page["items"])
            cursor = page["cursor"]

        rows = []
        for item in items:
            row = {"_item_id": item["id"], "_item_name": item["name"]}
            for cv in item["column_values"]:
                row[cv["column"]["title"]] = cv["text"]
            rows.append(row)
        return rows

    async def test_connection(self) -> dict:
        query = "query { me { name email } account { name } }"
        return await self._post(query)


monday_client_singleton: MondayClient | None = None


def get_monday_client() -> MondayClient:
    global monday_client_singleton
    if monday_client_singleton is None:
        monday_client_singleton = MondayClient()
    return monday_client_singleton
