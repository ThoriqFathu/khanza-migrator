# Khanza Migrator

Desktop application untuk testing/pre-migration dan final migration database MySQL/MariaDB Khanza.

## Prinsip

Khanza Migrator **tidak membuat migration SQL**. Input utamanya adalah `migration.sql` yang dihasilkan aplikasi lain.

Workflow:

```text
migration.sql
    ↓
Pre-Migration
    ↓
Validation
    ↓
Approval
    ↓
Production Backup
    ↓
Final Migration
```

Automatic rollback tidak digunakan sebagai mekanisme utama karena DDL MySQL/MariaDB tidak boleh diasumsikan transactional.

## Requirement

- Python 3.10+ (sumber acuan: `project.requires-python` di `pyproject.toml`)
- PySide6 dan python-dotenv (diinstal otomatis melalui package)
- MySQL/MariaDB client:
  - `mysql`
  - `mysqldump`
- Target database dapat diakses dari komputer aplikasi.

> Untuk production, gunakan akun database dengan hak minimum yang diperlukan. Jangan gunakan kredensial production di source code.

## Instalasi

Jalankan dari root repository. `pyproject.toml` menjadi sumber acuan versi Python
dan dependency; `requirements.txt` merujuk ke package lokal yang sama.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Perintah instalasi runtime di atas setara dengan `python -m pip install .`.
Untuk development dan test, gunakan extra `dev` (termasuk runtime dependency):

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
```

`pytest` hanya merupakan dependency development/test. Baseline test menggunakan
stub port dan direktori sementara; tidak memerlukan GUI atau MySQL sungguhan.
Python 3.10 dipilih karena source memakai union type `X | None` dan dependency
yang dideklarasikan mendukung versi tersebut; tidak ada kebutuhan source khusus 3.11.

Jalankan GUI:

```bash
python -m app.main
```

Jalankan CLI:

```bash
python -m app.cli --help
```

## Konfigurasi

Aplikasi tidak menyimpan password dalam file konfigurasi proyek.

Connection profile di GUI disimpan sebagai metadata lokal tanpa password. Password diminta saat operasi database.

## Pre-Migration

1. Pilih `migration.sql`.
2. Pilih production backup.
3. Isi konfigurasi TEST database.
4. Test connection.
5. Reset Test Database.
6. Run Migration.
7. Jika gagal, perbaiki migration SQL.
8. Reset test database dan ulangi.
9. Jika berhasil, Approve.

## Final Migration

Final migration hanya dapat berjalan jika:

- migration file ada dan dapat dibaca;
- SHA-256 cocok dengan approval;
- pre-migration sukses;
- approval valid;
- target PRODUCTION reachable;
- production backup berhasil;
- backup terverifikasi.

Jika final migration gagal, aplikasi **tidak melakukan automatic restore**.

## Catatan parser SQL

Parser mendukung:

- semicolon di quoted string;
- single/double quoted string;
- backtick identifier;
- line comment `--`;
- block comment `/* */`;
- `DELIMITER` untuk trigger/procedure/function/event secara dasar.

Untuk migration generator dengan format khusus, parser berada di satu boundary terpisah sehingga mudah diganti.

## Struktur

```text
app/
├── domains/migration/
│   ├── domain/
│   ├── application/
│   └── infrastructure/
├── shared/
│   ├── database/
│   ├── sql/
│   ├── filesystem/
│   ├── hashing/
│   └── logging/
├── presentation/qt/migration/
└── cli.py
```
