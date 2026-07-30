"""Single-flight and bounded concurrency for paid people-provider searches.

Two callers asking for the same company and role family at the same moment
should cost one provider call, not two. The orchestration layer already holds a
Redis lock per job+fingerprint, but that does not help when ten *different* jobs
at the same company are expanded at once — the case that produced the burst of
provider requests behind the original incident.

This module sits directly around the provider call:

* an in-process single-flight keyed by canonical search identity, so concurrent
  identical searches share one awaited result, and
* a bounded semaphore so one API instance can never fan out more than the
  configured number of concurrent provider calls.

Both are per-process by design. Cross-instance protection is the job of the
budgets and the shared circuits; a distributed lock on this path would add a
Redis round trip to every search and a new failure mode.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.config import settings
from app.people.observability import metric

logger = logging.getLogger("jobpilot.people.coalescing")

T = TypeVar("T")


def search_identity(
    *,
    provider: str,
    adapter_version: str,
    company_domain: str | None,
    company_name: str,
    role_family: str | None,
    category: str,
    location: str | None = None,
    location_material: bool = False,
) -> str:
    """Canonical key for "the same search".

    Location only participates when the caller says it materially narrows the
    query; otherwise two identical searches that differ by a soft location hint
    would miss each other and pay twice.
    """

    parts = [
        provider,
        adapter_version,
        (company_domain or "").strip().lower(),
        (company_name or "").strip().lower(),
        (role_family or "").strip().lower(),
        category,
        (location or "").strip().lower() if location_material else "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


class ProviderSearchCoalescer:
    """Shares one in-flight provider call between identical concurrent searches."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future] = {}
        self._semaphores: dict[tuple[str, int], asyncio.Semaphore] = {}

    def _semaphore(self, provider: str) -> asyncio.Semaphore:
        limit = max(1, settings.people_provider_max_concurrent_calls)
        loop_id = id(asyncio.get_running_loop())
        key = (f"{provider}:{limit}", loop_id)
        existing = self._semaphores.get(key)
        if existing is None:
            existing = asyncio.Semaphore(limit)
            self._semaphores[key] = existing
        return existing

    async def run(
        self,
        key: str,
        provider: str,
        call: Callable[[], Awaitable[T]],
    ) -> T:
        """Run ``call`` under the concurrency limit, sharing an identical flight.

        A waiting caller receives the leader's completed result — including its
        exception — and never issues its own provider request.
        """

        existing = self._inflight.get(key)
        if existing is not None:
            metric("people_request_coalesced_total", provider=provider, status="waiter")
            try:
                return await asyncio.wait_for(
                    asyncio.shield(existing),
                    timeout=max(1.0, settings.people_provider_coalesce_wait_seconds),
                )
            except TimeoutError:
                # The leader is slower than the wait budget. Fall through and
                # run this caller's own request rather than failing the user.
                logger.info("people_coalesce_wait_timeout provider=%s", provider)
            except asyncio.CancelledError:
                raise

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._inflight[key] = future
        metric("people_request_coalesced_total", provider=provider, status="leader")
        try:
            async with self._semaphore(provider):
                result = await call()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            if not future.done():
                future.set_exception(exc)
            # A future whose exception is never retrieved logs a warning at GC
            # time; waiters may all have timed out, so mark it as observed.
            future.exception()
            raise
        else:
            if not future.done():
                future.set_result(result)
            return result
        finally:
            if self._inflight.get(key) is future:
                self._inflight.pop(key, None)

    def clear(self) -> None:
        self._inflight.clear()
        self._semaphores.clear()

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)


provider_search_coalescer = ProviderSearchCoalescer()
