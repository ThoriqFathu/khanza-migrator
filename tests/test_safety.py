from app.domains.migration.application.use_cases import ResetTestDatabaseUseCase
from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig


class DummyDb:
    pass


def test_reset_test_rejects_production():
    use_case = ResetTestDatabaseUseCase(DummyDb())
    config = DatabaseConfig(
        host="127.0.0.1",
        port=3306,
        database="rsudk",
        username="root",
        environment=Environment.PRODUCTION,
    )
    try:
        use_case.execute(config, "secret", __import__("pathlib").Path("/tmp/backup.sql"))
        assert False
    except ValueError as exc:
        assert "TEST" in str(exc)
