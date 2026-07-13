from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


def check_database_readiness(db: Session) -> tuple[bool, dict]:
    checks: dict[str, object] = {
        "database_connected": False,
        "alembic_version_exists": False,
        "current_revision": None,
        "head_revision": None,
        "critical_tables_exist": False,
    }
    try:
        connection = db.connection()
        connection.execute(text("select 1"))
        checks["database_connected"] = True

        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        checks["alembic_version_exists"] = "alembic_version" in table_names
        checks["critical_tables_exist"] = {"users", "user_profiles"}.issubset(table_names)

        context = MigrationContext.configure(connection)
        current_revision = context.get_current_revision()
        checks["current_revision"] = current_revision

        api_root = Path(__file__).resolve().parents[2]
        alembic_config = Config(str(api_root / "alembic.ini"))
        script = ScriptDirectory.from_config(alembic_config)
        head_revision = script.get_current_head()
        checks["head_revision"] = head_revision
        checks["schema_up_to_date"] = current_revision == head_revision

        ready = all(
            [
                checks["database_connected"],
                checks["alembic_version_exists"],
                checks["critical_tables_exist"],
                checks["schema_up_to_date"],
            ]
        )
        return ready, checks
    except SQLAlchemyError as exc:
        checks["error"] = str(exc)
        return False, checks

