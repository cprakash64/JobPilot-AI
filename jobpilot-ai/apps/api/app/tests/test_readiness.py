from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.services.readiness import check_database_readiness


class BrokenSession:
    # A realistic driver failure: SQLAlchemy/psycopg connection errors
    # stringify to the DSN they failed on, credentials included.
    def connection(self):
        raise SQLAlchemyError(
            "could not connect to postgresql://jobpilot:sup3r-s3cret@db.internal:5432/jobpilot"
        )


def test_readiness_fails_clearly_when_database_is_unavailable() -> None:
    ready, checks = check_database_readiness(BrokenSession())

    assert ready is False
    assert checks["database_connected"] is False
    # The failure is reported by exception TYPE. /readyz is unauthenticated, so
    # echoing the driver message here published the database password to anyone
    # who asked; the previous assertion required exactly that behaviour.
    assert checks["error"] == "SQLAlchemyError"
    assert "sup3r-s3cret" not in repr(checks)
    assert "db.internal" not in repr(checks)


def test_initial_migration_uses_single_create_for_postgresql_enums() -> None:
    migration = Path("alembic/versions/0001_initial.py").read_text(encoding="utf-8")

    assert 'name="documenttype",\n        create_type=False' in migration
    assert 'name="documentformat",\n        create_type=False' in migration
    assert 'name="applicationstatus",\n        create_type=False' in migration
    assert "checkfirst=True" in migration
