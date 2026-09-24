"""Bounded ALTER TABLE extraction, not a replacement for SqlMigrationParser.

Keep unknown syntax visible as an audit error, never execute parsed SQL.
"""
import re

from app.domains.migration.domain.models import MigrationStatement
from app.domains.migration.domain.preflight import ForeignKey, PreflightOperation


# Quoted strings stay opaque so keywords/commas inside defaults are not actions.
_TOKEN = re.compile(r"\s+|--(?=\s|$)[^\n]*|\#[^\n]*|/\*.*?\*/|"
                    r"`(?:``|[^`])*`|'(?:''|\\.|[^'\\])*'|\"(?:\"\"|\\.|[^\"\\])*\"|"
                    r"[\w$]+|[^\s]", re.S)
_IDENTIFIER = re.compile(r"(?:`(?:``|[^`])+`|[\w$]+)\Z")


class _Cursor:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0

    def accept(self, word: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index].upper() == word:
            self.index += 1
            return True
        return False

    def require(self, word: str) -> None:
        if not self.accept(word):
            raise ValueError(f"Expected {word}; SQL tidak didukung atau malformed")

    def identifier(self) -> str:
        if self.index >= len(self.tokens) or not _IDENTIFIER.fullmatch(self.tokens[self.index]):
            raise ValueError("Identifier SQL tidak didukung atau malformed")
        token = self.tokens[self.index]
        self.index += 1
        return token[1:-1].replace('``', '`') if token.startswith('`') else token

    def table(self) -> tuple[str, str]:
        first = self.identifier()
        return (first, self.identifier()) if self.accept('.') else ('', first)

    def columns(self) -> tuple[str, ...]:
        self.require('(')
        columns = [self.identifier()]
        while self.accept(','):
            columns.append(self.identifier())
        self.require(')')
        return tuple(columns)

    def end(self) -> None:
        if self.index != len(self.tokens):
            raise ValueError("Suffix SQL tidak didukung atau malformed")


class MigrationPreflightExtractor:
    def extract(self, statements: list[MigrationStatement]) -> list[PreflightOperation]:
        operations: list[PreflightOperation] = []
        for statement in statements:
            operations.extend(self._statement(statement))
        return operations

    def _statement(self, statement: MigrationStatement) -> list[PreflightOperation]:
        tokens: list[str] = []
        for match in _TOKEN.finditer(statement.sql):
            token = match.group()
            if token.startswith(('/*!', '/*M!')):
                return [self._unsupported(statement, "Executable SQL comments tidak didukung")]
            if token.isspace() or token.startswith(('--', '#', '/*')):
                continue
            tokens.append(token)
        cursor = _Cursor(tokens)
        if not cursor.accept('ALTER') or not cursor.accept('TABLE'):
            # Inline CREATE TABLE FKs and other relevant syntax must not disappear.
            if any(t.upper() in ('FOREIGN', 'REFERENCES', 'MODIFY', 'CHANGE') for t in tokens):
                return [self._unsupported(statement, "Hanya ALTER TABLE FK/MODIFY/CHANGE didukung")]
            return []
        try:
            schema, table = cursor.table()
            clauses: list[list[str]] = [[]]
            depth = 0
            for token in tokens[cursor.index:]:
                if token == ',' and depth == 0:
                    clauses.append([])
                    continue
                depth += (token == '(') - (token == ')')
                if depth < 0:
                    raise ValueError("Kurung SQL tidak seimbang")
                clauses[-1].append(token)
            if depth:
                raise ValueError("Kurung SQL tidak seimbang")
        except ValueError as exc:
            return [self._unsupported(statement, str(exc))]
        result: list[PreflightOperation] = []
        for clause in clauses:
            result.append(self._clause(statement, schema, table, clause))
        return result

    @staticmethod
    def _unsupported(statement: MigrationStatement, error: str) -> PreflightOperation:
        return PreflightOperation(statement.sequence, 'UNSUPPORTED', '', statement.sql, error=error)

    def _clause(self, statement: MigrationStatement, schema: str,
                table: str, tokens: list[str]) -> PreflightOperation:
        cursor = _Cursor(tokens)
        kind, column, name = 'UNSUPPORTED', '', ''
        foreign_key = None
        try:
            if cursor.accept('ADD'):
                if cursor.accept('CONSTRAINT'):
                    name = cursor.identifier()
                cursor.require('FOREIGN')
                kind = 'ADD_FK'
                cursor.require('KEY')
                child_columns = cursor.columns()
                cursor.require('REFERENCES')
                parent_schema, parent_table = cursor.table()
                parent_columns = cursor.columns()
                if len(child_columns) != len(parent_columns):
                    raise ValueError("Jumlah kolom FK child/parent berbeda")
                seen = set()
                while cursor.accept('ON'):
                    action = 'DELETE' if cursor.accept('DELETE') else 'UPDATE'
                    if action == 'UPDATE':
                        cursor.require('UPDATE')
                    if action in seen:
                        raise ValueError("ON action duplikat")
                    seen.add(action)
                    if cursor.accept('SET'):
                        cursor.require('NULL')
                    elif cursor.accept('NO'):
                        cursor.require('ACTION')
                    elif not (cursor.accept('CASCADE') or cursor.accept('RESTRICT')):
                        raise ValueError("Referential action tidak didukung")
                cursor.end()
                foreign_key = ForeignKey(name, table, child_columns, parent_table,
                                         parent_columns, schema, parent_schema)
            elif cursor.accept('DROP'):
                cursor.require('FOREIGN')
                kind = 'DROP_FK'
                cursor.require('KEY')
                name = cursor.identifier()
                cursor.end()
            elif tokens and tokens[0].upper() in ('MODIFY', 'CHANGE'):
                kind = tokens[0].upper()
                cursor.index = 1
                cursor.accept('COLUMN')
                column = cursor.identifier()
                if kind == 'CHANGE':
                    cursor.identifier()  # Old name is the dependency lookup key.
                if cursor.index == len(tokens):
                    raise ValueError("Definisi kolom tidak tersedia")
            else:
                raise ValueError("Operasi ALTER di luar cakupan preflight")
        except ValueError as exc:
            return PreflightOperation(statement.sequence, kind, table, statement.sql,
                                      schema, column, name, error=str(exc))
        return PreflightOperation(statement.sequence, kind, table, statement.sql,
                                  schema, column, name, foreign_key)
