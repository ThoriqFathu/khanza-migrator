"""Best-effort analysis of the restored TEST snapshot; never executes migration."""
from pathlib import Path

from app.domains.migration.domain.enums import Environment
from app.domains.migration.domain.models import DatabaseConfig
from app.domains.migration.domain.preflight import (
    ForeignKey, MigrationPreflightResult, PreflightFinding, PreflightIssueType as Issue,
    PreflightOperation, PreflightStatus as Status,
)
from .ports import MigrationParserPort, ProgressCallback
from .preflight_ports import PreflightDatabasePort, PreflightExtractorPort


def _finding(op: PreflightOperation, status: Status, issue: Issue, problem: str,
             fk: ForeignKey | None = None, count: int | None = None) -> PreflightFinding:
    return PreflightFinding(
        status, issue, op.statement_sequence,
        f"{op.table}.{op.column}" if op.column else (fk.relation if fk else op.table),
        problem, op.original_sql, fk.constraint_name if fk else op.constraint_name,
        fk, count, problem if status == Status.ERROR else '',
    )


class ForeignKeyOrphanAnalyzer:
    def __init__(self, db: PreflightDatabasePort) -> None:
        self.db = db

    def analyze(self, op: PreflightOperation, config: DatabaseConfig,
                password: str) -> PreflightFinding:
        try:
            if op.foreign_key is None:
                raise ValueError(op.error or "FK tidak dapat diekstrak")
            fk = op.foreign_key
            if any(schema and schema != config.database for schema in (fk.child_schema, fk.parent_schema)):
                raise ValueError("FK lintas database tidak didukung; tidak ada query ke schema lain")
            count = self.db.count_orphans(config, password, fk)
            if count < 0:
                raise ValueError("Orphan count tidak valid")
            return _finding(op, Status.BLOCKER if count else Status.SAFE,
                            Issue.FOREIGN_KEY_ORPHAN,
                            f"{count} orphan rows" + ("; potensi ERROR 1452" if count else " pada snapshot TEST"),
                            fk, count)
        except Exception as exc:
            return _finding(op, Status.ERROR, Issue.FOREIGN_KEY_ORPHAN, str(exc), op.foreign_key)


class ColumnForeignKeyDependencyAnalyzer:
    def __init__(self, db: PreflightDatabasePort) -> None:
        self.db = db

    def analyze(self, op: PreflightOperation, config: DatabaseConfig, password: str,
                preceding: list[PreflightOperation], same_statement: list[PreflightOperation]) -> list[PreflightFinding]:
        try:
            if any(p.kind == 'CHANGE' and not p.error and p.table == op.table
                   and (p.schema or config.database) == config.database for p in preceding):
                raise ValueError("Metadata setelah CHANGE sebelumnya belum dapat diproyeksikan; review manual diperlukan")
            dependencies = self.db.get_foreign_keys(config, password, op.table, op.column)
            active = {(fk.child_schema or config.database, fk.child_table, fk.constraint_name.casefold()): fk
                      for fk in dependencies}
            # Only earlier statements suppress existing dependencies. Same ALTER ordering
            # is deliberately not guessed; an ambiguous combination is an audit error.
            for previous in preceding:
                if previous.error:
                    continue
                key = (previous.schema or config.database, previous.table, previous.constraint_name.casefold())
                if previous.kind == 'DROP_FK':
                    active.pop(key, None)
                elif previous.kind == 'ADD_FK' and previous.foreign_key:
                    fk = previous.foreign_key
                    if self._touches(fk, op, config.database):
                        # Unnamed ADDs have no known generated constraint name.
                        add_key = key if fk.constraint_name else (*key, str(previous.statement_sequence), fk.relation)
                        active[add_key] = fk
            if any(p.kind in ('DROP_FK', 'ADD_FK', 'CHANGE') for p in same_statement if p is not op):
                raise ValueError("Gabungan perubahan FK/CHANGE dan kolom dalam satu ALTER belum didukung")
            findings = []
            for fk in active.values():
                if self._touches(fk, op, config.database):
                    findings.append(_finding(
                        op, Status.BLOCKER, Issue.COLUMN_FOREIGN_KEY_DEPENDENCY,
                        f"FK belum di-drop sebelum {op.kind}; potensi ERROR 1832. {fk.relation}", fk,
                    ))
            return findings or [_finding(op, Status.SAFE, Issue.COLUMN_FOREIGN_KEY_DEPENDENCY,
                                         "Tidak ada FK aktif pada kolom berdasarkan metadata dan DROP sebelumnya")]
        except Exception as exc:
            return [_finding(op, Status.ERROR, Issue.COLUMN_FOREIGN_KEY_DEPENDENCY, str(exc))]

    @staticmethod
    def _touches(fk: ForeignKey, op: PreflightOperation, database: str) -> bool:
        return (((fk.child_schema or database) == database and fk.child_table.casefold() == op.table.casefold()
                 and op.column.casefold() in tuple(c.casefold() for c in fk.child_columns))
                or ((fk.parent_schema or database) == database and fk.parent_table.casefold() == op.table.casefold()
                    and op.column.casefold() in tuple(c.casefold() for c in fk.parent_columns)))


class MigrationPreflight:
    def __init__(self, parser: MigrationParserPort, extractor: PreflightExtractorPort,
                 db: PreflightDatabasePort) -> None:
        self.parser = parser
        self.extractor = extractor
        self.db = db
        self.orphans = ForeignKeyOrphanAnalyzer(db)
        self.dependencies = ColumnForeignKeyDependencyAnalyzer(db)

    def execute(self, migration: Path, config: DatabaseConfig, password: str,
                progress: ProgressCallback | None = None) -> MigrationPreflightResult:
        if config.environment is not Environment.TEST:
            raise ValueError("Migration Preflight hanya diizinkan pada environment TEST")
        if not config.database:
            raise ValueError("Nama database TEST wajib diisi")
        if progress:
            progress("Parsing migration untuk preflight...", 0, 0)
        statements = self.parser.parse_file(migration)
        operations = self.extractor.extract(statements)
        self.db.test_connection(config, password)
        result = MigrationPreflightResult(str(migration), config.database, len(statements),
                                         sum(o.kind == 'ADD_FK' for o in operations),
                                         sum(o.kind in ('MODIFY', 'CHANGE') for o in operations))
        for index, op in enumerate(operations, 1):
            issue = (Issue.FOREIGN_KEY_ORPHAN if op.kind == 'ADD_FK' else
                     Issue.COLUMN_FOREIGN_KEY_DEPENDENCY if op.kind in ('MODIFY', 'CHANGE') else
                     Issue.UNSUPPORTED_SQL)
            if op.error or (op.schema and op.schema != config.database):
                result.findings.append(_finding(op, Status.ERROR, issue,
                    op.error or "Operasi lintas database tidak didukung"))
            elif op.kind == 'ADD_FK':
                result.findings.append(self.orphans.analyze(op, config, password))
            elif op.kind in ('MODIFY', 'CHANGE'):
                result.findings.extend(self.dependencies.analyze(
                    op, config, password,
                    [p for p in operations if p.statement_sequence < op.statement_sequence],
                    [p for p in operations if p.statement_sequence == op.statement_sequence],
                ))
            if progress:
                progress(f"Preflight #{op.statement_sequence}: {op.kind}", index, len(operations))
        if progress:
            progress("Migration preflight completed", len(operations), len(operations))
        return result
