from typing import Any

import httpx


async def get_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> tuple[int | None, Any]:
    try:
        response = await client.request(method, url, **kwargs)
    except (httpx.TransportError, httpx.TimeoutException):
        return None, None
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None
