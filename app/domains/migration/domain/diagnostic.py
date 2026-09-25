"""Diagnostic artifacts are deliberately separate from approval/PRE results."""
from dataclasses import dataclass, field
from datetime import datetime

from .models import StatementResult


@dataclass
class DiagnosticMigrationResult:
    started_at: datetime
    finished_at: datetime
    database: str
    migration_name: str
    total_statements: int
    statements: list[StatementResult] = field(default_factory=list)
    fatal_error: str = ""
    foreign_key_checks: bool = True

    @property
    def success_count(self) -> int:
        return sum(item.success for item in self.statements)

    @property
    def failure_count(self) -> int:
        return sum(not item.success for item in self.statements)

    @property
    def completed(self) -> bool:
        return not self.fatal_error and len(self.statements) == self.total_statements

    @property
    def unattempted_count(self) -> int:
        return self.total_statements - len(self.statements)
