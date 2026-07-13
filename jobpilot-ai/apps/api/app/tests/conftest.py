"""Shared pytest fixtures."""

import pytest

from app.jobs.job_ingestion_service import clear_source_cache


@pytest.fixture(autouse=True)
def _clear_source_cache() -> None:
    """The source-result cache is a module-level global; clear it between tests
    so fake adapters that reuse (provider, slug) keys never leak data."""
    clear_source_cache()
