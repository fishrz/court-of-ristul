import asyncio
import logging
from time import monotonic
from typing import Any, Self

import httpx

BASE_URL = "https://api.opendota.com/api"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

logger = logging.getLogger(__name__)


class OpenDotaClient:
    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        min_interval: float = 1.0,
        backoff_base: float = 1.0,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._min_interval = min_interval
        self._backoff_base = backoff_base
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_recent_matches(self, steam_id: int) -> Any | None:
        return await self._request("GET", f"/players/{steam_id}/recentMatches")

    async def get_player(self, steam_id: int) -> Any | None:
        return await self._request("GET", f"/players/{steam_id}")

    async def get_match(self, match_id: int) -> Any | None:
        return await self._request("GET", f"/matches/{match_id}")

    async def request_parse(self, match_id: int) -> Any | None:
        return await self._request("POST", f"/request/{match_id}")

    # ---- meta / 版本基准 ----------------------------------------------
    # 这几个端点没有 match_id，返回的是全服聚合数据，用于给个人表现
    # 提供「同分段同英雄」参照系。免费、无需 key，但体积大，调用方
    # 必须缓存（见 models.MetaSnapshot）。

    async def get_patches(self) -> Any | None:
        return await self._request("GET", "/constants/patch")

    async def get_hero_stats(self) -> Any | None:
        """全英雄 × 8 个分段的 pick/win。字段形如 7_pick / 7_win。"""
        return await self._request("GET", "/heroStats")

    async def get_hero_benchmarks(self, hero_id: int) -> Any | None:
        """该英雄各项指标的全服百分位对照表（GPM/XPM/正补/伤害…）。"""
        return await self._request("GET", f"/benchmarks?hero_id={hero_id}")

    async def get_item_timings(self, hero_id: int) -> Any | None:
        """该英雄关键物品的出装时间 × 该时间段的胜率样本。"""
        return await self._request("GET", f"/scenarios/itemTimings?hero_id={hero_id}")

    async def get_hero_matchups(self, hero_id: int) -> Any | None:
        """该英雄对线/对局各英雄的真实胜率（games_played + wins）。"""
        return await self._request("GET", f"/heroes/{hero_id}/matchups")

    async def _request(self, method: str, path: str) -> Any | None:
        for attempt in range(3):
            try:
                await self._wait_for_rate_limit()
                client = self._get_client()
                response = await client.request(
                    method,
                    f"{BASE_URL}{path}",
                    headers={"User-Agent": BROWSER_USER_AGENT},
                    timeout=30.0,
                )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 2:
                    logger.warning("OpenDota request failed after 3 attempts: %s", exc)
                    return None
                await asyncio.sleep(self._backoff_base * (2**attempt))
        return None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            elapsed = monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_at = monotonic()
