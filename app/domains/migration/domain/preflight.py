"""Plain data for read-only migration analysis; no execution/session state."""
from dataclasses import dataclass, field
from enum import Enum


class PreflightStatus(str, Enum):
    SAFE = "SAFE"
    BLOCKER = "BLOCKER"
    ERROR = "ERROR"


class PreflightIssueType(str, Enum):
    FOREIGN_KEY_ORPHAN = "FOREIGN_KEY_ORPHAN"
    COLUMN_FOREIGN_KEY_DEPENDENCY = "COLUMN_FOREIGN_KEY_DEPENDENCY"
    UNSUPPORTED_SQL = "UNSUPPORTED_SQL"


@dataclass(frozen=True)
class ForeignKey:
    constraint_name: str
    child_table: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_columns: tuple[str, ...]
    child_schema: str = ""
    parent_schema: str = ""

    @property
    def relation(self) -> str:
        return (f"{self.child_table}({', '.join(self.child_columns)}) → "
                f"{self.parent_table}({', '.join(self.parent_columns)})")


@dataclass(frozen=True)
class PreflightOperation:
    statement_sequence: int
    kind: str  # ADD_FK, DROP_FK, MODIFY, CHANGE, UNSUPPORTED
    table: str
    original_sql: str
    schema: str = ""
    column: str = ""
    constraint_name: str = ""
    foreign_key: ForeignKey | None = None
    error: str = ""


@dataclass(frozen=True)
class PreflightFinding:
    status: PreflightStatus
    issue_type: PreflightIssueType
    statement_sequence: int
    object_name: str
    problem: str
    original_sql: str
    constraint_name: str = ""
    foreign_key: ForeignKey | None = None
    orphan_count: int | None = None
    error: str = ""


@dataclass
class MigrationPreflightResult:
    migration_file: str
    database: str
    total_statements: int
    fk_candidates: int
    column_modifications: int
    findings: list[PreflightFinding] = field(default_factory=list)

    def count(self, status: PreflightStatus, issue: PreflightIssueType) -> int:
        return sum(f.status == status and f.issue_type == issue for f in self.findings)

    @property
    def safe_fk_count(self) -> int:
        return self.count(PreflightStatus.SAFE, PreflightIssueType.FOREIGN_KEY_ORPHAN)

    @property
    def orphan_fk_count(self) -> int:
        return self.count(PreflightStatus.BLOCKER, PreflightIssueType.FOREIGN_KEY_ORPHAN)

    @property
    def dependency_issue_count(self) -> int:
        return self.count(PreflightStatus.BLOCKER, PreflightIssueType.COLUMN_FOREIGN_KEY_DEPENDENCY)

    @property
    def audit_error_count(self) -> int:
        return sum(f.status == PreflightStatus.ERROR for f in self.findings)
