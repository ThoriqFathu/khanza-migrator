from dataclasses import dataclass, field
from datetime import datetime

from .models import DatabaseConfig, StatementResult


@dataclass
class PreMigrationSession:
    session_id: str
    source_file: str
    test_backup: str
    target: DatabaseConfig
    started_at: datetime
    pending: list[str]
    completed: list[StatementResult] = field(default_factory=list)
    failed: StatementResult | None = None
    in_flight: bool = False
    invalidated: bool = False
    finished_at: datetime | None = None
    output_hash: str | None = None
