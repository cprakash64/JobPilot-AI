from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.services.readiness import check_database_readiness


class BrokenSession:
    def connection(self):
        raise SQLAlchemyError("database unavailable")


def test_readiness_fails_clearly_when_database_is_unavailable() -> None:
    ready, checks = check_database_readiness(BrokenSession())

    assert ready is False
    assert checks["database_connected"] is False
    assert "database unavailable" in checks["error"]


def test_initial_migration_uses_single_create_for_postgresql_enums() -> None:
    migration = Path("alembic/versions/0001_initial.py").read_text(encoding="utf-8")

    assert 'name="documenttype",\n        create_type=False' in migration
    assert 'name="documentformat",\n        create_type=False' in migration
    assert 'name="applicationstatus",\n        create_type=False' in migration
    assert "checkfirst=True" in migration
