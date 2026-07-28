"""Shared pytest fixtures."""

import pytest

from app.jobs.job_ingestion_service import clear_source_cache


@pytest.fixture(autouse=True)
def _clear_source_cache() -> None:
    """The source-result cache is a module-level global; clear it between tests
    so fake adapters that reuse (provider, slug) keys never leak data."""
    clear_source_cache()


@pytest.fixture(autouse=True)
def _no_background_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the scoring fan-out down its INLINE path for every test.

    ``enqueue_scoring_for_jobs`` publishes to Celery when a broker is reachable
    and otherwise scores inline on the caller's session. That probe hits a real
    socket, so the suite's behaviour depended on whether the developer happened
    to have the local Redis container running: with Redis up, scoring work was
    handed to a broker that no worker drains during a test, and assertions about
    other users' scores failed; with Redis down, the same tests passed.

    Tests assert on scoring RESULTS, not on Celery delivery, so the inline path
    is the one they mean. A test that specifically covers enqueueing should
    monkeypatch ``broker_reachable`` back to True itself.
    """
    from app.jobs import job_ingestion_service

    monkeypatch.setattr(job_ingestion_service, "broker_reachable", lambda **_: False)
    # The probe memoizes its result for a few seconds; drop it so a real
    # reachable broker observed by an earlier test cannot leak into this one.
    job_ingestion_service._BROKER_PROBE.pop("v", None)


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """The applications rate limiter keys buckets by ``user.id``. Each test uses a
    fresh in-memory DB where ids restart at 1, so without a reset the ``create:1``
    bucket accumulates across tests and eventually trips the 20/60s limit. Clear
    it between tests so rate-limit state never leaks between unrelated cases."""
    from app.people.service import _RATE_BUCKETS as people_rate_buckets
    from app.routes.applications import _RATE_BUCKETS

    _RATE_BUCKETS.clear()
    people_rate_buckets.clear()


@pytest.fixture(autouse=True)
def _reset_people_bulk_capability() -> None:
    from app.people.bulk_capability import clear_local_bulk_capabilities
    from app.people.complete_person_cache import (
        clear_local_complete_person_cache,
    )

    clear_local_bulk_capabilities()
    clear_local_complete_person_cache()
