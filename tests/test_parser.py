from app.shared.sql.parser import SqlMigrationParser


def test_parser_handles_semicolon_inside_string():
    sql = """
    ALTER TABLE pasien ADD catatan VARCHAR(255);
    INSERT INTO pasien (nama) VALUES ('A;B');
    """
    statements = SqlMigrationParser().parse(sql)
    assert len(statements) == 2
    assert "'A;B'" in statements[1].sql


def test_parser_handles_multiline():
    sql = """
    ALTER TABLE pasien
      ADD COLUMN kode VARCHAR(20);
    ALTER TABLE dokter
      ADD COLUMN aktif TINYINT;
    """
    statements = SqlMigrationParser().parse(sql)
    assert len(statements) == 2


def test_parser_handles_delimiter():
    sql = """
    DELIMITER $$
    CREATE PROCEDURE test_proc()
    BEGIN
      SELECT 'hello;world';
    END$$
    DELIMITER ;
    ALTER TABLE pasien ADD x INT;
    """
    statements = SqlMigrationParser().parse(sql)
    assert len(statements) == 2
    assert "CREATE PROCEDURE" in statements[0].sql.upper()
