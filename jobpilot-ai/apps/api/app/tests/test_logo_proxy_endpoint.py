"""Section A — the company-logo endpoint degrades to a controlled response.

A logo is decoration. No upstream problem — a dead CDN, a redirect chain, an
unexpected exception from a library whose API changed under us — may produce an
unhandled 500 on a page that is otherwise fine. The httpx `URL.human_repr()`
removal did exactly that, because AttributeError was not in the handler's
`except` clause.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.jobs.safe_fetch import FetchFailedError, UnsafeUrlError
from app.models.entities import CompanyBranding
from app.routes import jobs as jobs_routes


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db: Session, tmp_path, monkeypatch) -> TestClient:
    # Isolate the on-disk logo cache so tests never read or write the real one.
    monkeypatch.setattr(jobs_routes, "_LOGO_CACHE_DIR", tmp_path / "logos")

    app = FastAPI()
    # The router already declares prefix="/jobs" — do not add it twice.
    app.include_router(jobs_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def add_branding(
    db: Session, key: str = "acme", logo_url: str = "https://cdn.example.com/acme.png"
) -> None:
    db.add(
        CompanyBranding(
            normalized_key=key, canonical_name="Acme", domain="example.com", logo_url=logo_url
        )
    )
    db.commit()


def test_unknown_company_is_a_404_not_an_error(client: TestClient) -> None:
    response = client.get("/jobs/companies/nobody/logo")
    assert response.status_code == 404


def test_company_without_a_resolved_logo_is_a_404(client: TestClient, db: Session) -> None:
    db.add(
        CompanyBranding(
            normalized_key="acme", canonical_name="Acme", domain="example.com", logo_url=None
        )
    )
    db.commit()
    assert client.get("/jobs/companies/acme/logo").status_code == 404


def test_successful_fetch_is_served_with_caching_headers(
    client: TestClient, db: Session, monkeypatch
) -> None:
    add_branding(db)
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    monkeypatch.setattr(jobs_routes, "safe_fetch_image", lambda url: _result(png, url))
    response = client.get("/jobs/companies/acme/logo")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "max-age" in response.headers.get("cache-control", "")
    assert response.content == png


def test_cache_is_invalidated_when_resolved_logo_url_changes(
    client: TestClient, db: Session, monkeypatch
) -> None:
    first_url = "https://cdn.example.com/old.png"
    second_url = "https://cdn.example.com/new.png"
    add_branding(db, logo_url=first_url)

    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        marker = b"old" if url == first_url else b"new"
        return _result(b"\x89PNG\r\n\x1a\n" + marker, url)

    monkeypatch.setattr(jobs_routes, "safe_fetch_image", fetch)
    assert client.get("/jobs/companies/acme/logo").content.endswith(b"old")

    branding = db.query(CompanyBranding).filter_by(normalized_key="acme").one()
    branding.logo_url = second_url
    db.commit()

    assert client.get("/jobs/companies/acme/logo").content.endswith(b"new")
    assert calls == [first_url, second_url]


def _result(content: bytes, url: str):
    from app.jobs.safe_fetch import SafeFetchResult

    return SafeFetchResult(content=content, content_type="image/png", final_url=url)


@pytest.mark.parametrize(
    "exc",
    [
        FetchFailedError("unexpected status 404"),
        UnsafeUrlError("target resolves to a non-public address (127.0.0.1)"),
    ],
)
def test_known_fetch_failures_return_a_controlled_502(
    client: TestClient, db: Session, monkeypatch, exc
) -> None:
    add_branding(db)
    monkeypatch.setattr(jobs_routes, "safe_fetch_image", _raiser(exc))

    response = client.get("/jobs/companies/acme/logo")
    assert response.status_code == 502
    assert response.json()["detail"] == "Logo could not be fetched"


def test_an_unexpected_exception_does_not_become_a_500(
    client: TestClient, db: Session, monkeypatch
) -> None:
    """The exact shape of the live incident: a library API changed and raised
    AttributeError, which the handler did not anticipate."""
    add_branding(db)
    monkeypatch.setattr(
        jobs_routes,
        "safe_fetch_image",
        _raiser(AttributeError("'URL' object has no attribute 'human_repr'")),
    )

    response = client.get("/jobs/companies/acme/logo")
    assert response.status_code == 502
    assert response.json()["detail"] == "Logo could not be fetched"


def test_failure_response_leaks_no_upstream_url_or_internals(
    client: TestClient, db: Session, monkeypatch
) -> None:
    add_branding(db, logo_url="https://internal-cdn.corp.example/secret/acme.png")
    monkeypatch.setattr(
        jobs_routes,
        "safe_fetch_image",
        _raiser(
            FetchFailedError("failed fetching https://internal-cdn.corp.example/secret/acme.png")
        ),
    )

    body = client.get("/jobs/companies/acme/logo").text
    assert "internal-cdn" not in body
    assert "secret" not in body
    assert "Traceback" not in body


def _raiser(exc: BaseException):
    def raise_it(_url: str):
        raise exc

    return raise_it
