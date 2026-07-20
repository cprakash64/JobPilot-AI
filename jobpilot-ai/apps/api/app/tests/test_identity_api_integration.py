"""Section F — structured identity and phone, end to end through the HTTP API.

Covers the round trip a real user makes: save a profile, confirm the name split,
reload, and read it back through the extension-facing session payload. Uses a
representative multi-token name rather than any specific person's identity —
production code must never contain a hard-coded name.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models import entities  # noqa: F401
from app.models.entities import UserProfile

# A representative three-token name with the same shape as the reported failure
# (multi-token given name + single-token surname).
FIRST, MIDDLE, LAST = "Chandra", "Prakash", "Pandey"
FULL = f"{FIRST} {MIDDLE} {LAST}"
RAW_PHONE = "602-816-1309"
E164 = "+16028161309"


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def auth(client: TestClient, email: str = "identity@example.com") -> dict[str, str]:
    response = client.post("/auth/signup", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def save_profile(client: TestClient, headers: dict, **overrides) -> dict:
    payload = {"full_name": FULL, "phone": RAW_PHONE, "location_country": "United States"}
    payload.update(overrides)
    response = client.put("/profile", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["profile"]


def confirm_name(client: TestClient, headers: dict, **overrides) -> dict:
    payload = {"first_name": FIRST, "middle_name": MIDDLE, "last_name": LAST}
    payload.update(overrides)
    response = client.put("/profile/name", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["profile"]


# --------------------------------------------------------------------------- #
# Structured name round trip
# --------------------------------------------------------------------------- #
def test_confirmed_name_parts_round_trip_through_the_api(client: TestClient) -> None:
    headers = auth(client)
    save_profile(client, headers)
    profile = confirm_name(client, headers)

    assert profile["first_name"] == FIRST
    assert profile["middle_name"] == MIDDLE
    assert profile["last_name"] == LAST
    assert profile["name_confirmed"] is True
    # full_name is DERIVED from the parts, not stored independently.
    assert profile["full_name"] == FULL


def test_confirmed_values_survive_a_reload(client: TestClient) -> None:
    """The "refresh the page" case — a GET must return what was confirmed."""
    headers = auth(client)
    save_profile(client, headers)
    confirm_name(client, headers)

    reloaded = client.get("/profile", headers=headers).json()["profile"]
    assert (reloaded["first_name"], reloaded["middle_name"], reloaded["last_name"]) == (
        FIRST,
        MIDDLE,
        LAST,
    )
    assert reloaded["full_name"] == FULL
    assert reloaded["name_confirmed"] is True


def test_preferred_names_default_to_the_legal_name_but_can_be_overridden(
    client: TestClient,
) -> None:
    headers = auth(client)
    save_profile(client, headers)
    profile = confirm_name(client, headers, preferred_first_name="Chan")

    assert profile["preferred_first_name"] == "Chan"
    # Not supplied → stays unset on the record; the fill layer falls back to the
    # legal name only where the form says to.
    assert profile["preferred_last_name"] is None


def test_a_later_unrelated_profile_save_does_not_clear_the_confirmed_name(
    client: TestClient,
) -> None:
    """PUT /profile is a full overwrite and deliberately does not carry the name
    parts — it must not reset them or the confirmation flag."""
    headers = auth(client)
    save_profile(client, headers)
    confirm_name(client, headers)

    save_profile(client, headers, location_city="Tempe")

    reloaded = client.get("/profile", headers=headers).json()["profile"]
    assert reloaded["first_name"] == FIRST
    assert reloaded["last_name"] == LAST
    assert reloaded["name_confirmed"] is True


def test_legacy_given_family_wire_aliases_are_still_accepted(client: TestClient) -> None:
    """An extension build predating the rename sends given_name/family_name."""
    headers = auth(client)
    save_profile(client, headers)

    response = client.put(
        "/profile/name", headers=headers, json={"given_name": FIRST, "family_name": LAST}
    )
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]

    assert profile["first_name"] == FIRST
    assert profile["last_name"] == LAST
    # ...and the legacy keys are still emitted for old readers.
    assert profile["given_name"] == FIRST
    assert profile["family_name"] == LAST


def test_a_confirmed_name_is_never_replaced_by_a_machine_split(
    client: TestClient, db_factory
) -> None:
    headers = auth(client)
    save_profile(client, headers)
    confirm_name(client, headers)

    with db_factory() as session:
        profile = session.scalar(select(UserProfile))
        # The naive split would have produced this; the confirmed value stands.
        assert profile.last_name == LAST
        assert profile.last_name != f"{MIDDLE} {LAST}"


# --------------------------------------------------------------------------- #
# Structured phone
# --------------------------------------------------------------------------- #
def test_phone_is_normalized_into_structured_columns_on_save(client: TestClient) -> None:
    headers = auth(client)
    profile = save_profile(client, headers)

    assert profile["phone_country_iso2"] == "US"
    assert profile["phone_country_code"] == "+1"
    assert profile["phone_national_number"] == "6028161309"
    assert profile["phone_e164"] == E164
    # The raw value the user typed is preserved verbatim.
    assert profile["phone"] == RAW_PHONE


@pytest.mark.parametrize(
    "written", ["602-816-1309", "(602) 816-1309", "6028161309", "+1 602 816 1309"]
)
def test_every_written_form_normalizes_to_the_same_e164(client: TestClient, written: str) -> None:
    headers = auth(client, email=f"phone-{abs(hash(written))}@example.com")
    profile = save_profile(client, headers, phone=written)
    assert profile["phone_e164"] == E164


def test_structured_phone_survives_a_reload(client: TestClient) -> None:
    headers = auth(client)
    save_profile(client, headers)

    reloaded = client.get("/profile", headers=headers).json()["profile"]
    assert reloaded["phone_e164"] == E164
    assert reloaded["phone_country_iso2"] == "US"


def test_an_invalid_legacy_phone_is_preserved_raw_and_not_corrupted(client: TestClient) -> None:
    """A number that cannot be parsed keeps the user's text and leaves the
    structured columns empty — better than storing a confidently wrong E.164."""
    headers = auth(client)
    profile = save_profile(client, headers, phone="555-000-1111")

    assert profile["phone"] == "555-000-1111"
    assert profile["phone_e164"] is None
    assert profile["phone_country_code"] is None


def test_changing_the_phone_re_derives_the_structured_columns(client: TestClient) -> None:
    headers = auth(client)
    save_profile(client, headers)
    profile = save_profile(client, headers, phone="+44 20 7946 0958")

    assert profile["phone_country_iso2"] == "GB"
    assert profile["phone_country_code"] == "+44"
    assert profile["phone_e164"] == "+442079460958"


# --------------------------------------------------------------------------- #
# What the extension receives
# --------------------------------------------------------------------------- #
def test_extension_answers_carry_every_name_and_phone_shape(client: TestClient, db_factory) -> None:
    from app.applications.answer_vault_service import build_safe_answers
    from app.models.entities import User

    headers = auth(client)
    save_profile(client, headers)
    confirm_name(client, headers)

    with db_factory() as session:
        user = session.scalar(select(User))
        answers, unresolved = build_safe_answers(session, user)
        by_key = {a["canonical_key"]: a["value"] for a in answers}

    assert by_key["first_name"] == FIRST
    assert by_key["middle_name"] == MIDDLE
    assert by_key["last_name"] == LAST
    assert by_key["full_name"] == FULL
    assert by_key["preferred_first_name"] == FIRST
    assert by_key["preferred_last_name"] == LAST
    assert by_key["phone"] == E164
    assert by_key["phone_country"] == "+1"
    assert by_key["phone_national"] == "6028161309"
    # A confirmed name is no longer an open question.
    assert not [q for q in unresolved if q.get("action") == "confirm_name"]


def test_an_unconfirmed_legacy_profile_is_asked_rather_than_guessed(
    client: TestClient, db_factory
) -> None:
    from app.applications.answer_vault_service import build_safe_answers
    from app.models.entities import User

    headers = auth(client)
    save_profile(client, headers)  # full_name only, never confirmed

    with db_factory() as session:
        user = session.scalar(select(User))
        answers, unresolved = build_safe_answers(session, user)
        by_key = {a["canonical_key"]: a["value"] for a in answers}

    assert "last_name" not in by_key
    assert f"{MIDDLE} {LAST}" not in by_key.values()
    asked = {q["canonical_key"] for q in unresolved if q.get("action") == "confirm_name"}
    assert asked == {"first_name", "middle_name", "last_name"}


def test_no_profile_response_field_contains_a_secret(client: TestClient) -> None:
    """Guards the whole serializer: profile payloads travel to the extension."""
    headers = auth(client)
    save_profile(client, headers)
    body = client.get("/profile", headers=headers).text.lower()

    for forbidden in ("password", "secret_key", "hashed_password", "api_key", "token"):
        assert forbidden not in body
