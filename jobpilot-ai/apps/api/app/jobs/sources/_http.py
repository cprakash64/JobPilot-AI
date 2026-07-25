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


async def get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    retries: int = 1,
    max_bytes: int = 1024 * 1024,
) -> str:
    """GET a bounded public text resource with the connector retry policy.

    This helper is only for connector-owned, fixed public hosts. URLs derived
    from user input must use ``app.jobs.safe_fetch`` instead.
    """
    timeout = timeout or settings.job_discovery_timeout_seconds
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.get(url, headers=headers)
                if response.status_code in _TRANSIENT_STATUS and attempt < retries:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                response.raise_for_status()
                content = response.content
                if len(content) > max_bytes:
                    raise ValueError("response exceeds connector text size limit")
                return content.decode(response.encoding or "utf-8", errors="replace")
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < retries and _is_transient(exc):
                    await asyncio.sleep(_backoff(attempt))
                    continue
                raise
    if last_exc:  # pragma: no cover - defensive
        raise last_exc
    return ""


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _TRANSIENT_STATUS
    return False


def _backoff(attempt: int) -> float:
    return min(2.0, 0.4 * (2**attempt))
