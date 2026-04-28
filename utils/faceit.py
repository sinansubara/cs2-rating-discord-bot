"""
Async FACEIT Data API v4 wrapper.
Docs (outdated): https://developers.faceit.com/docs/tools/data-api
New docs URL: https://docs.faceit.com/docs/data-api/data/
"""

from __future__ import annotations
import aiohttp
from typing import Optional, Any


FACEIT_BASE = "https://open.faceit.com/data/v4"


class FaceitAPI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=self._headers())
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict = None) -> Optional[Any]:
        session = await self._session_get()
        url = f"{FACEIT_BASE}{path}"
        async with session.get(url, params=params) as resp:
            if resp.status == 200:
                return await resp.json()
            if resp.status == 404:
                return None
            text = await resp.text()
            raise RuntimeError(f"FACEIT API {resp.status} — {path}: {text[:200]}")

    # ── Player ──────────────────────────────────────────────────────────────

    async def get_player(self, nickname: str) -> Optional[dict]:
        """Lookup a player by FACEIT nickname (CS2 game scope)."""
        return await self._get("/players", params={"nickname": nickname, "game": "cs2"})

    async def get_player_stats(self, player_id: str) -> Optional[dict]:
        """Lifetime stats for a player in CS2."""
        return await self._get(f"/players/{player_id}/stats/cs2")

    # ── Match history ────────────────────────────────────────────────────────

    async def get_match_history(
        self, player_id: str, limit: int = 1, offset: int = 0
    ) -> Optional[dict]:
        return await self._get(
            f"/players/{player_id}/history",
            params={"game": "cs2", "limit": limit, "offset": offset},
        )

    # ── Match ────────────────────────────────────────────────────────────────

    async def get_match(self, match_id: str) -> Optional[dict]:
        """Match metadata (teams, map pool, status, etc.)."""
        return await self._get(f"/matches/{match_id}")

    async def get_match_stats(self, match_id: str) -> Optional[dict]:
        """
        Per-player stats for every map in a match.
        Response shape:
          {
            "rounds": [          ← one entry per map played
              {
                "round_stats": { "Map": "de_inferno", "Score": "13:5", ... },
                "teams": [
                  {
                    "players": [
                      { "player_id": "...", "nickname": "...", "player_stats": {...} }
                    ]
                  }
                ]
              }
            ]
          }

        CS2 player_stats keys (most recent FACEIT format):
          Kills, Deaths, Assists, Headshots, "Headshots %",
          "K/R Ratio", "K/D Ratio", ADR, KAST,
          MVPs, "Triple Kills", "Quadro Kills", "Penta Kills",
          "1v1Wins", "1v2Wins", "Flash Assists"

        NOTE: ADR and KAST were added in late 2024. Older matches may be missing them.
        """
        return await self._get(f"/matches/{match_id}/stats")
