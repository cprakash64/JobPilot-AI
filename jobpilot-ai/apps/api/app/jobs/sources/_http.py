"""Shared HTTP helpers for source connectors: timeout + retry with backoff.

Connectors call these so every ATS request is bounded and resilient to transient
failures without any single source being able to hang or crash discovery.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import settings

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


async def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float | None = None,
    retries: int = 2,
) -> Any:
    """GET ``url`` and return parsed JSON, retrying transient failures.

    Raises the final httpx error if all attempts fail so the caller (ingestion)
    can turn it into a per-source warning.
    """
    timeout = timeout or settings.job_discovery_timeout_seconds
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code in _TRANSIENT_STATUS and attempt < retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < retries and _is_transient(exc):
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise
    if last_exc:  # pragma: no cover - defensive
        raise last_exc
    return None


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


def _backoff(attempt: int) -> float:
    return min(2.0, 0.4 * (2**attempt))
