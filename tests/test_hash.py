from pathlib import Path
from app.shared.hashing.sha256 import sha256_file


def test_hash_same_file(tmp_path: Path):
    file = tmp_path / "migration.sql"
    file.write_text("ALTER TABLE pasien ADD x INT;", encoding="utf-8")
    first = sha256_file(file)
    second = sha256_file(file)
    assert first == second


def test_hash_changes_when_file_changes(tmp_path: Path):
    file = tmp_path / "migration.sql"
    file.write_text("ALTER TABLE pasien ADD x INT;", encoding="utf-8")
    first = sha256_file(file)
    file.write_text("ALTER TABLE pasien ADD y INT;", encoding="utf-8")
    second = sha256_file(file)
    assert first != second
