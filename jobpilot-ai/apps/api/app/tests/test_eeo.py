"""Sections C/D — voluntary EEO: typed vocabularies, consent, and legacy cleanup.

The defect being pinned: one shared option list ("Prefer not to answer / Yes /
No / Another option") served five questions with different meanings, so a
*gender* could be stored as "yes" and a race/ethnicity as "another_option".
Neither string carries any demographic meaning, and neither may ever be
reinterpreted as a real answer.
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
from app.models.entities import SensitiveDemographics
from app.profile.eeo import (
    FIELD_VOCABULARIES,
    PREFER_NOT,
    InvalidDemographicValue,
    has_any_answer,
    validate_multi,
    validate_single,
)


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        session = db_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def auth(client: TestClient, email: str = "eeo@example.com") -> dict[str, str]:
    response = client.post("/auth/signup", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------------------- #
# Vocabularies
# --------------------------------------------------------------------------- #
def test_gender_identity_has_no_yes_or_no_option() -> None:
    assert "yes" not in FIELD_VOCABULARIES["gender_identity"]
    assert "no" not in FIELD_VOCABULARIES["gender_identity"]
    assert set(FIELD_VOCABULARIES["gender_identity"]) == {
        "woman", "man", "non_binary", "self_describe", PREFER_NOT
    }


def test_every_question_offers_prefer_not_to_answer() -> None:
    for field, vocabulary in FIELD_VOCABULARIES.items():
        assert PREFER_NOT in vocabulary, f"{field} must allow declining"


def test_veteran_status_distinguishes_not_protected_from_not_a_veteran() -> None:
    assert "not_protected_veteran" in FIELD_VOCABULARIES["veteran_status"]
    assert "not_a_veteran" in FIELD_VOCABULARIES["veteran_status"]


def test_race_is_not_collapsed_to_a_single_generic_option() -> None:
    vocabulary = FIELD_VOCABULARIES["race_ethnicity"]
    assert "another_option" not in vocabulary
    assert "asian" in vocabulary and "white" in vocabulary


# --------------------------------------------------------------------------- #
# Validation — invalid combinations cannot be saved again
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["yes", "no", "another_option", "male", "Woman", "unknown"])
def test_invalid_gender_values_are_rejected_not_coerced(bad: str) -> None:
    with pytest.raises(InvalidDemographicValue):
        validate_single("gender_identity", bad)


def test_blank_means_not_answered() -> None:
    assert validate_single("gender_identity", None) is None
    assert validate_single("gender_identity", "") is None


def test_prefer_not_to_answer_is_exclusive_in_race() -> None:
    with pytest.raises(InvalidDemographicValue):
        validate_multi("race_ethnicity", [PREFER_NOT, "asian"])
    assert validate_multi("race_ethnicity", [PREFER_NOT]) == [PREFER_NOT]


def test_multiple_races_are_preserved() -> None:
    assert validate_multi("race_ethnicity", ["asian", "white"]) == ["asian", "white"]


def test_duplicate_race_values_are_deduped() -> None:
    assert validate_multi("race_ethnicity", ["asian", "asian"]) == ["asian"]


def test_a_single_value_field_rejects_a_list_and_vice_versa() -> None:
    with pytest.raises(InvalidDemographicValue):
        validate_multi("gender_identity", ["woman"])
    with pytest.raises(InvalidDemographicValue):
        validate_single("race_ethnicity", "asian")


def test_validation_errors_never_echo_the_submitted_value() -> None:
    """The message travels into logs; a demographic value must not."""
    with pytest.raises(InvalidDemographicValue) as exc:
        validate_single("gender_identity", "some-sensitive-free-text")
    assert "some-sensitive-free-text" not in str(exc.value)


def test_has_any_answer_detects_real_answers_only() -> None:
    assert has_any_answer({}) is False
    assert has_any_answer({"race_ethnicity": []}) is False
    assert has_any_answer({"gender_identity": PREFER_NOT}) is True
    assert has_any_answer({"race_ethnicity": ["asian"]}) is True
    assert has_any_answer({"gender_self_description": "text"}) is True


# --------------------------------------------------------------------------- #
# API: consent
# --------------------------------------------------------------------------- #
def test_new_user_has_no_demographics_and_nothing_preselected(client: TestClient) -> None:
    headers = auth(client)
    body = client.get("/profile/demographics", headers=headers).json()
    assert body["demographics"] is None


def test_saving_answers_without_consent_is_rejected(client: TestClient) -> None:
    headers = auth(client)
    response = client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "woman", "consent_to_store": False},
    )
    assert response.status_code == 422
    assert "consent" in response.json()["detail"].lower()
    # ...and nothing was stored.
    assert client.get("/profile/demographics", headers=headers).json()["demographics"] is None


def test_saving_with_consent_stores_the_answers(client: TestClient) -> None:
    headers = auth(client)
    response = client.put(
        "/profile/demographics",
        headers=headers,
        json={
            "gender_identity": "woman",
            "race_ethnicity": ["asian", "white"],
            "consent_to_store": True,
        },
    )
    assert response.status_code == 200
    stored = client.get("/profile/demographics", headers=headers).json()["demographics"]
    assert stored["gender_identity"] == "woman"
    # Multiple races survive the round trip — never reduced to one.
    assert stored["race_ethnicity"] == ["asian", "white"]


def test_withdrawing_consent_deletes_stored_values(client: TestClient) -> None:
    headers = auth(client)
    client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "man", "consent_to_store": True},
    )
    # An empty payload with consent off is a withdrawal, not a rejected save.
    response = client.put(
        "/profile/demographics", headers=headers, json={"consent_to_store": False}
    )
    assert response.status_code == 200
    assert client.get("/profile/demographics", headers=headers).json()["demographics"] is None


def test_delete_removes_stored_values(client: TestClient) -> None:
    headers = auth(client)
    client.put(
        "/profile/demographics",
        headers=headers,
        json={"disability_status": "no", "consent_to_store": True},
    )
    assert client.delete("/profile/demographics", headers=headers).status_code == 204
    assert client.get("/profile/demographics", headers=headers).json()["demographics"] is None


def test_an_invalid_option_is_rejected_by_the_api(client: TestClient) -> None:
    headers = auth(client)
    response = client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "yes", "consent_to_store": True},
    )
    assert response.status_code == 422


def test_self_description_is_dropped_when_its_trigger_is_deselected(client: TestClient) -> None:
    headers = auth(client)
    client.put(
        "/profile/demographics",
        headers=headers,
        json={
            "gender_identity": "self_describe",
            "gender_self_description": "example text",
            "consent_to_store": True,
        },
    )
    client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "woman", "consent_to_store": True},
    )
    stored = client.get("/profile/demographics", headers=headers).json()["demographics"]
    assert stored["gender_self_description"] is None


def test_demographics_are_absent_from_the_general_profile_endpoint(client: TestClient) -> None:
    """EEO data is exposed ONLY through its dedicated endpoint."""
    headers = auth(client)
    client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "woman", "consent_to_store": True},
    )
    profile_body = client.get("/profile", headers=headers).text
    for token in ("gender", "veteran", "disability", "ethnicity", "hispanic"):
        assert token not in profile_body.lower()


def test_legacy_free_text_columns_are_never_returned(client: TestClient, db_factory) -> None:
    """A quarantined legacy value (gender='yes') must not reach any client."""
    headers = auth(client)
    client.put(
        "/profile/demographics",
        headers=headers,
        json={"gender_identity": "woman", "consent_to_store": True},
    )
    with db_factory() as session:
        record = session.scalar(select(SensitiveDemographics))
        record.gender = "yes"  # simulate un-migrated residue
        record.ethnicity = "another_option"
        session.commit()

    body = client.get("/profile/demographics", headers=headers).json()["demographics"]
    assert "gender" not in body  # only gender_identity is serialized
    assert "ethnicity" not in body
    assert body["gender_identity"] == "woman"


# --------------------------------------------------------------------------- #
# Section D — legacy cleanup rule (migration 0015)
# --------------------------------------------------------------------------- #
def test_legacy_row_without_consent_is_deleted_not_reinterpreted() -> None:
    from app.profile.eeo import classify_legacy_row

    decision = classify_legacy_row(
        {"gender": "yes", "ethnicity": "another_option", "hispanic_latino_status": "no"},
        consented=False,
    )
    assert decision["action"] == "delete"


def test_legacy_gender_yes_is_never_converted_into_a_gender_identity() -> None:
    from app.profile.eeo import classify_legacy_row

    decision = classify_legacy_row({"gender": "yes"}, consented=True)
    assert decision["action"] == "quarantine"
    assert decision["gender_identity"] is None
    assert decision["needs_review"] is True


def test_legacy_another_option_is_never_treated_as_a_race() -> None:
    from app.profile.eeo import classify_legacy_row

    decision = classify_legacy_row({"ethnicity": "another_option"}, consented=True)
    assert decision["race_ethnicity"] is None
    assert decision["needs_review"] is True


def test_already_valid_legacy_values_are_preserved() -> None:
    from app.profile.eeo import PREFER_NOT, classify_legacy_row

    decision = classify_legacy_row(
        {
            "gender": "prefer_not_to_answer",
            "veteran_status": "prefer_not_to_answer",
            "disability_status": "prefer_not_to_answer",
            "hispanic_latino_status": "yes",
            "ethnicity": "prefer_not_to_answer",
        },
        consented=True,
    )
    assert decision["gender_identity"] == PREFER_NOT
    assert decision["veteran_status"] == PREFER_NOT
    assert decision["hispanic_or_latino"] == "yes"
    assert decision["race_ethnicity"] == [PREFER_NOT]
    # Nothing was dropped, so the user is not asked to re-answer.
    assert decision["needs_review"] is False


def test_an_empty_consented_row_is_not_flagged_for_review() -> None:
    from app.profile.eeo import classify_legacy_row

    decision = classify_legacy_row({}, consented=True)
    assert decision["needs_review"] is False
