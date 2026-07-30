from collections.abc import Callable

import httpx
import pytest

from app.opendota import BROWSER_USER_AGENT, OpenDotaClient


@pytest.mark.asyncio
async def test_get_recent_matches_uses_browser_user_agent(
    httpx_mock: Callable[[httpx.Request], httpx.Response],
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"match_id": 1}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        opendota = OpenDotaClient(client=client, min_interval=0)
        result = await opendota.get_recent_matches(123456789)

    assert result == [{"match_id": 1}]
    assert requests[0].headers["User-Agent"] == BROWSER_USER_AGENT


@pytest.mark.asyncio
async def test_network_failure_returns_none_after_three_attempts() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        opendota = OpenDotaClient(
            client=client,
            min_interval=0,
            backoff_base=0,
        )
        result = await opendota.get_match(8917764448)

    assert result is None
    assert attempts == 3


@pytest.mark.asyncio
async def test_request_parse_posts_to_request_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"job": {"jobId": 42}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        opendota = OpenDotaClient(client=client, min_interval=0)
        result = await opendota.request_parse(8917764448)

    assert result == {"job": {"jobId": 42}}
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/request/8917764448"
