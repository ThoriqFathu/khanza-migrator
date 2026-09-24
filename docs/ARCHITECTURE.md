# Arsitektur dan Panduan Pengembangan Khanza Migrator

Diperbarui berdasarkan source repository pada **25 September 2026**. Semua path relatif terhadap root repository. Bagian 1–10 menjelaskan implementasi saat ini; bagian 11–13 panduan pengembangan; bagian 14 masalah yang masih ada; bagian 15–17 rekomendasi dan status roadmap.

Dokumen ini juga mencakup Migration Preflight (analisis read-only pada TEST), Diagnostic Migration (actual execution continue-on-error + laporan Markdown), dan fitur resume Pre-Migration: editor SQL tersisa, checkpoint sukses, dan export migration_final.sql. Pengujian fitur menggunakan fake database serta Qt offscreen; tidak menjalankan MySQL atau migration pada database sungguhan.

## 1. Overview

Khanza Migrator menerima SQL migration dari luar, mengujinya pada database TEST, menyimpan approval, membuat backup PRODUCTION, lalu menjalankan migration final. Aplikasi tidak menghasilkan migration SQL dan tidak melakukan automatic rollback/restore ketika migration gagal.

Saat ini startup dan pembuatan dependency sudah dipisahkan dari UI. `MainWindow` mendaftarkan tiga class tab, meneruskan callback antar-tab, dan masih mengelola QThread/worker. Widget, handler, dan state migration berada di masing-masing tab. Adapter teknis masih berada di `app/shared/`.

Urutan baca untuk belajar codebase:

1. `app/main.py` → `app/presentation/qt/app.py:run_app()`.
2. `app/bootstrap.py:create_main_window()` → `app/presentation/qt/migration/main_window.py:MainWindow.__init__()`.
3. `app/presentation/qt/migration/pre_migration_tab.py:PreMigrationTab._run_pre()` → callback `MainWindow._run_worker()`.
4. `app/presentation/qt/migration/workers.py:Worker.run()` → `app/domains/migration/application/resumable_pre_migration.py:ResumablePreMigration.run()` untuk Pre; runner production tetap `RunMigrationUseCase.execute()`.
5. `app/domains/migration/application/ports.py`, model domain, lalu adapter di `app/shared/`.

Tidak ada dependency injection framework, registry tab, `TaskRunner`, atau class `MigrationService`. Dialog custom yang sudah ada adalah `PreflightDialog` untuk hasil audit dan `DiagnosticResultDialog` untuk actual failures/SQL lengkap. Orchestration aplikasi memakai class `*UseCase` dengan method `execute()`.

## 2. Application Entry Point

| Jalur | File/simbol | Perilaku |
| --- | --- | --- |
| GUI: `python -m app.main` | `app/main.py`, blok `__main__` | Memanggil `run_app()` dari `app/presentation/qt/app.py` |
| CLI: `python -m app.cli` | `app/cli.py:main()` | Subcommand `hash` dan `parse`; keluar melalui `SystemExit(main())` |
| CLI setelah instalasi | `pyproject.toml:[project.scripts]` | `khanza-migrator = "app.cli:main"`; bukan launcher GUI |

CLI langsung menggunakan `app/shared/hashing/sha256.py:sha256_file()` atau `app/shared/sql/parser.py:SqlMigrationParser.parse_file()`. CLI belum menyediakan workflow reset, approval, backup, atau eksekusi migration.

`pyproject.toml` adalah sumber acuan instalasi:

- Minimum Python `>=3.10`. Source menggunakan union type `X | None`; tidak ditemukan kebutuhan khusus Python 3.11. Verifikasi Tahap 2 berhasil pada Python 3.10.12 dengan dependency yang kompatibel.
- Runtime: `PySide6>=6.7,<7` dan `python-dotenv>=1.2,<2`. `app/config.py` mengimpor `dotenv.load_dotenv`.
- Development/test: extra `dev` berisi `pytest>=9,<10`.
- `requirements.txt` berisi referensi package lokal `.`. Dari root repository, `python -m pip install -r requirements.txt` setara dengan `python -m pip install .`.
- Development: `python -m pip install -e '.[dev]'`; test: `python -m pytest -q`.
- Operasi database memerlukan executable `mysql` dan `mysqldump`. Tidak ditemukan ORM atau driver koneksi MySQL Python.

## 3. Startup Flow

1. `app/main.py` mengimpor `app/presentation/qt/app.py:run_app()`.
2. Import berlanjut melalui bootstrap, `MainWindow`, dan module tab. Module tab mengimpor `app/config.py`, yang memanggil `load_dotenv(BASE_DIR / ".env")` dan membentuk konstanta `PRE_*`/`FINAL_*` melalui `env()` dan `env_int()`. Port invalid dapat gagal saat import.
3. `run_app()` memanggil `app/shared/logging/logger.py:configure_logging()`, membuat log di `~/.khanza-migrator/logs/application.log`.
4. `run_app()` membuat `QApplication([])`, baru memanggil `app/bootstrap.py:create_main_window()`.
5. Bootstrap membuat `MySqlClient`, `MySqlDumpBackupProvider`, `LocalHistoryRepository`, `SqlMigrationParser`, `ResumablePreMigration`, `MigrationPreflight(parser, MigrationPreflightExtractor(), MySqlPreflightReader(db))`, serta `DiagnosticMigration(parser, db)`. Service dan adapter dikirim melalui constructor `MainWindow`; MainWindow meneruskan service preflight dan diagnostic ke PreMigrationTab; DiagnosticPanel menerima service diagnostic.
6. `LocalHistoryRepository.__init__()` membuat direktori history bila belum ada. `MainWindow.__init__()` menyimpan dependency, menghubungkan progress signal, dan menginisialisasi `_active_threads`.
7. `MainWindow` membuat `PreMigrationTab`, `FinalMigrationTab`, dan `HistoryTab` secara eager, lalu mendaftarkannya ke `QTabWidget`. History langsung dimuat oleh `HistoryTab.__init__()`.
8. Setelah `setCentralWidget()`, `MainWindow._update_final_state()` meneruskan panggilan ke `FinalMigrationTab._update_final_state()`. Method ini membaca approval/hash dan menonaktifkan tombol eksekusi final.
9. `run_app()` memanggil `window.show()` lalu `app.exec()`.

```mermaid
flowchart TD
    Entry["app/main.py"] --> Qt["presentation/qt/app.py: run_app()"]
    Qt --> Log["configure_logging()"]
    Qt --> App["QApplication([])"]
    Qt --> Boot["bootstrap.py: create_main_window()"]
    Boot --> Deps["MySqlClient / MySqlDumpBackupProvider / LocalHistoryRepository / SqlMigrationParser"]
    Boot --> Window["MainWindow(dependency)"]
    Window --> Pre["PreMigrationTab"]
    Window --> Final["FinalMigrationTab"]
    Window --> History["HistoryTab"]
    History --> Load["_show_history()"]
    Window --> State["FinalMigrationTab._update_final_state()"]
    Qt --> Loop["window.show() lalu app.exec()"]
```

`run_app()` tidak lagi berada di `main_window.py`. `MainWindow()` tanpa dependency bukan cara konstruksi existing; gunakan bootstrap setelah `QApplication` tersedia. Bootstrap juga membuat `ResumablePreMigration(parser, db, history, LocalSessionRepository())`, menginjeksi MainWindow lalu PreMigrationTab. Use case production/reset/approval masih dibuat oleh handler tab saat diperlukan.

## 4. Project Directory Structure

Struktur source saat ini; file `__init__.py` tidak ditampilkan:

```text
app/
├── main.py
├── cli.py
├── bootstrap.py
├── config.py
├── domains/migration/
│   ├── domain/
│   │   ├── enums.py
│   │   ├── models.py
│   │   ├── session.py
│   │   ├── preflight.py
│   │   └── diagnostic.py
│   └── application/
│       ├── ports.py
│       ├── use_cases.py
│       ├── diagnostic_migration.py
│       ├── migration_preflight.py
│       ├── preflight_ports.py
│       ├── resumable_pre_migration.py
│       └── session_ports.py
├── presentation/qt/
│   ├── app.py
│   └── migration/
│       ├── main_window.py
│       ├── pre_migration_tab.py
│       ├── preflight_dialog.py
│       ├── diagnostic_panel.py
│       ├── final_migration_tab.py
│       ├── history_tab.py
│       └── workers.py
└── shared/
    ├── diagnostic_report.py
    ├── database/
    │   ├── mysql_client.py
    │   ├── preflight.py
    │   └── backup.py
    ├── filesystem/
    │   ├── history.py
    │   └── sessions.py
    ├── hashing/sha256.py
    ├── logging/logger.py
    └── sql/
        ├── parser.py
        ├── preflight_extractor.py
        └── render.py
tests/
├── conftest.py
├── test_hash.py
├── test_parser.py
├── test_safety.py
├── test_migration_use_cases.py
├── test_history_repository.py
├── test_resumable_pre_migration.py
├── test_pre_migration_resume_ui.py
├── test_preflight_extractor.py
├── test_migration_preflight.py
├── test_preflight_database.py
├── test_preflight_ui.py
├── test_diagnostic_migration.py
├── test_diagnostic_error_capture.py
├── test_diagnostic_report.py
└── test_diagnostic_ui.py
docs/ARCHITECTURE.md
pyproject.toml
requirements.txt
README.md
AGENTS.md
.env.example
```

`app/application/`, `app/infrastructure/`, `app/domains/migration/infrastructure/`, `presentation/qt/workers/`, dan subfolder `tabs/` belum ada. Ketiga class tab berada langsung di `app/presentation/qt/migration/`. Pedoman AGENTS dan struktur usulan bukan bukti bahwa directory tersebut sudah diimplementasikan.

## 5. Layer Responsibilities

| Area | Komponen | Tanggung jawab |
| --- | --- | --- |
| `app/bootstrap.py` | `create_main_window()` | Composition root: membuat adapter konkret dan menginjeksi window |
| `app/presentation/qt/app.py` | `run_app()` | Logging startup, QApplication, show, event loop |
| `app/presentation/qt/migration/main_window.py` | `MainWindow` | Registrasi tab, penghubung Pre → Final, ownership worker/thread |
| `app/presentation/qt/migration/*_tab.py` | `PreMigrationTab`, `FinalMigrationTab`, `HistoryTab` | Form, dialog, handler, state dan rendering milik tab |
| `app/domains/migration/application/diagnostic_migration.py` | `DiagnosticMigration.execute()` | Actual TEST execution, continue-on-error, result diagnostic terpisah dari PRE/approval |
| `app/shared/diagnostic_report.py` | `render_diagnostic_report()` | Pure rendering Markdown dari result, tanpa Qt/I/O/database |
| `app/domains/migration/application/migration_preflight.py` | `MigrationPreflight`, `ForeignKeyOrphanAnalyzer`, `ColumnForeignKeyDependencyAnalyzer` | Analisis TEST, agregasi temuan, tanpa Qt atau SQL execution |
| `app/domains/migration/application/preflight_ports.py` | `PreflightDatabasePort`, `PreflightExtractorPort` | Kontrak read-only database dan ekstraksi operasi SQL |
| `app/domains/migration/application/use_cases.py` | Enam class `*UseCase` | Workflow dan validasi prasyarat, tanpa Qt |
| `app/domains/migration/application/ports.py` | `DatabasePort`, `BackupPort`, `HistoryPort`, `MigrationParserPort` | Kontrak `typing.Protocol`, dipenuhi adapter secara struktural |
| `app/domains/migration/domain/` | Dataclass dan enum | Data migration, hasil, approval, backup, safety report; tanpa PySide6 |
| `app/shared/` | Adapter database/parser/history, hash/logging | Implementasi I/O dan utilitas teknis |
| `app/config.py` | `env()`, `env_int()`, konstanta | Default form dari environment/.env |

`MainWindow` menerima database/backup/parser bertipe port, tetapi history bertipe `LocalHistoryRepository`. `HistoryTab` membutuhkan `root` dan `approval_file` yang tidak tercantum di `HistoryPort`; Pre/Final menerima `HistoryPort`. Instance repository yang sama diteruskan ke semua tab.

`shared` belum sepenuhnya generik: adapter mengimpor model migration, dan `MySqlClient` menggunakan `ProgressCallback` dari application port. Use case juga langsung menggunakan helper `sha256_file()`.

## 6. UI Architecture

`MainWindow` tidak lagi memiliki field `pre_*`, `final_*`, `history_text`, `last_pre_result`, atau `last_backup`. Kepemilikannya:

| Komponen dan path | State/widget utama | Method utama |
| --- | --- | --- |
| `pre_migration_tab.py:PreMigrationTab` | `pre_*`, `session`, `completed_sql`, `pending_sql`, `output_path`, `last_pre_result` | `_build_ui()`, `_pre_config()`, `_run_pre()`, `_approve()`, `migration_path()` |
| `final_migration_tab.py:FinalMigrationTab` | `final_*`, `safety_labels`, `last_backup` | `_build_ui()`, `_final_config()`, `_validate_final()`, `_create_backup()`, `_execute_final()` |
| `history_tab.py:HistoryTab` | `history_text`, repository | `_show_history()`, `_reset_all_data()` |
| `main_window.py:MainWindow` | `tabs`, `pre_tab`, `final_tab`, `_active_threads`, dependency bersama | `_set_final_migration()`, `_update_final_state()`, `_run_worker()` |

Semua path pada tabel berada di `app/presentation/qt/migration/`. Widget dibangun langsung dengan Python/layout Qt; tidak ada Qt Designer `.ui`. `preflight_dialog.py:PreflightDialog` adalah QDialog kecil untuk summary/tabel hasil analisis.

Komunikasi Pre → Final menggunakan callback biasa, bukan event bus:

| Callback constructor | Nilai yang diinjeksi oleh `MainWindow` | Efek |
| --- | --- | --- |
| Pre `migration_selected` | `MainWindow._set_final_migration` | Meneruskan path ke `FinalMigrationTab._set_final_migration()` |
| Pre `update_final_state` | `MainWindow._update_final_state` | Meneruskan ke `FinalMigrationTab._update_final_state()` |
| Final `pre_migration_path` | `PreMigrationTab.migration_path` | Fallback path ketika field final kosong saat pembaruan status |
| Pre/Final `run_worker` | `MainWindow._run_worker` | Dispatch background function dengan widget log/progress dan callback hasil |

```mermaid
sequenceDiagram
    participant P as PreMigrationTab
    participant M as MainWindow
    participant F as FinalMigrationTab
    P->>P: _browse_file(): pilih migration
    P->>M: _migration_selected(path)
    M->>F: _set_final_migration(path)
    P->>M: _update_final_state()
    M->>F: _update_final_state()
    opt Field final kosong
        F->>P: _pre_migration_path() = migration_path()
    end
```

Pemilihan migration lewat Browse menyalin path ke Final. Browse backup hanya meminta pembaruan status. Edit path manual tidak melakukan sinkronisasi otomatis; tidak ada `textChanged` yang menginvalidasi backup. `FinalMigrationTab` tetap dibuat dengan field migration kosong meskipun konfigurasi PRE memiliki path awal.

Approval tersimpan pada repository bersama. `_approve()` meminta update final setelah penyimpanan berhasil. Callback aktif Pre sekarang `_session_finished()`. Setelah seluruh SQL sukses, callback menampilkan output dan mengirim path `migration_final.sql` ke Final. Handler hasil pre lama telah diganti oleh alur sesi ini. Approval membaca file output dan metadata target/backup sesi, bukan file input awal.

## 7. Tab Lifecycle

Registrasi dilakukan eksplisit di `app/presentation/qt/migration/main_window.py:MainWindow.__init__()`:

1. Buat `self.pre_tab = PreMigrationTab(...)`; `addTab(self.pre_tab, "Pre-Migration")`.
2. Buat `self.final_tab = FinalMigrationTab(...)`; `addTab(self.final_tab, "Final Migration")`.
3. `addTab(HistoryTab(self.history), "History")`.

Pre/Final menyusun layout melalui `_build_ui()` yang dipanggil constructor. History menyusun layout langsung di `__init__()` dan memanggil `_show_history()`. Urutan ini membuat callback `pre_migration_path` tersedia saat Final dibuat; callback Pre yang meneruskan ke Final baru digunakan setelah konstruksi selesai.

Tidak ada lazy loading, registry, `currentChanged`, atau hook saat tab dibuka. Berpindah tab tidak membuat ulang widget atau otomatis merefresh history. Qt mengelola page melalui widget tree. `MainWindow.closeEvent()` menolak penutupan saat `_active_threads` tidak kosong. Run/resume/reset Pre juga menonaktifkan window selama operasi; mekanisme cancellation belum ada.

`HistoryTab._show_history()` membaca `root.rglob("result.json")`, mengurutkan path secara menurun, dan membatasi ke 100 file sebelum parsing. Setiap file menampilkan path, lalu ringkasan:

```text
  SUCCESS | nama_database | success=3 failed=0
```

File JSON invalid atau field tidak lengkap tetap menyumbang baris path; exception parsing/formatting diabaikan. Jika tidak ada file: `Belum ada execution history.` Refresh dilakukan saat konstruksi, saat tombol Refresh diklik, dan setelah penghapusan data lokal berhasil.

`HistoryTab._reset_all_data()` terhubung ke tombol **Hapus Semua Data**. Setelah konfirmasi Yes (default No), method menghapus isi `history.root`, isi `~/khanza-migrator-backups`, dan `history.approval_file`, kemudian merefresh History dan menampilkan hasil. Operasi memakai `shutil.rmtree()`/`Path.unlink()` langsung di UI; tidak memanggil MySQL. Folder root, log aplikasi, folder sesi resume (`~/.khanza-migrator/sessions`), dan backup yang disimpan di luar folder default tersebut tidak ikut dibersihkan oleh method ini. Belum ada callback ke Pre/Final untuk mereset state in-memory setelah penghapusan.

## 8. Dialog Lifecycle

Sebagian besar dialog berupa pemanggilan statis Qt. `app/presentation/qt/migration/preflight_dialog.py:PreflightDialog(QDialog)` menerima `MigrationPreflightResult`; `_show_preflight()` membuat dan menampilkannya dengan `show()`. Tombol Close menutup dialog. Instance sebelumnya ditutup/dijadwalkan `deleteLater()` saat hasil dibuka lagi; parent tab mengelola lifetime-nya. Parent dialog normal adalah tab yang memanggilnya, sedangkan error worker masih memakai parent `MainWindow`.

| Pemanggil | Dialog dan alur |
| --- | --- |
| `PreMigrationTab._browse_file()`, `FinalMigrationTab._browse_file()` | `QFileDialog.getOpenFileName()`; path kosong berarti batal |
| `PreMigrationTab._test_pre_connection()` | Warning input, question membuat database TEST bila belum ada, critical jika gagal |
| `PreMigrationTab._reset_test()` | Warning Yes/No default No; Yes melanjutkan dispatch reset |
| `PreMigrationTab._run_preflight()`, `_preflight_finished()`, `_show_preflight()` | Warning input/critical fatal pada GUI thread; tombol detail membuka PreflightDialog dengan seluruh temuan |
| `PreMigrationTab._run_pre()`, `_approve()` | Warning prasyarat; approval juga information/critical |
| `FinalMigrationTab._validate_final()` | Critical jika exception; mengembalikan False |
| `FinalMigrationTab._create_backup()` | Warning prasyarat, `getSaveFileName()`, question overwrite bila file sudah ada |
| `FinalMigrationTab._execute_final()` | Pre-flight lalu warning konfirmasi PRODUCTION; default No |
| `FinalMigrationTab._final_finished()` | Information sukses atau critical gagal tanpa automatic restore |
| `HistoryTab._reset_all_data()` | Warning penghapusan lokal, information berhasil, critical exception |
| `MainWindow._run_worker()` | `worker.failed` → lambda → `QMessageBox.critical()` |

Password bukan dialog tersendiri: Pre/Final memakai `QLineEdit` dengan echo mode Password dan default dari konfigurasi. Handler membaca nilai input lalu mengarahkan operasi ke use case/adapter.

`app/presentation/qt/migration/diagnostic_panel.py:DiagnosticResultDialog` dibuka
oleh `DiagnosticPanel._show_details()` pada GUI thread. Tabel hanya berisi failure
(sequence, code, actual error); pemilihan baris memanggil `_selection_changed()` untuk
menampilkan SQL/error lengkap pada QPlainTextEdit read-only. `_export()` membuka
QFileDialog dan menulis Markdown dari result yang sudah tersedia; tidak dispatch
worker eksekusi lagi. Cancel/write failure mempertahankan result agar bisa dicoba lagi.

## 9. Application / Service Flow

Use case existing berada di `app/domains/migration/application/use_cases.py`; alur resume Pre berada di `resumable_pre_migration.py`. Bootstrap membuat adapter dan service resume; tab masih membuat use case reset/approval/production sesuai aksi pengguna.

| Use case | Dependency constructor | Alur `execute()` |
| --- | --- | --- |
| `ResetTestDatabaseUseCase` | `DatabasePort` | Wajib TEST, backup ada → connection → exists/drop → create → import → verify |
| `RunMigrationUseCase` | Parser, database, history port | Hash/parse file → execute berurutan → stop pada error pertama → simpan hasil |
| `ApproveMigrationUseCase` | `HistoryPort` | Wajib status SUCCESS dan hash sama → bentuk/simpan `Approval` |
| `ValidateFinalMigrationUseCase` | Database/history port | File, approval/hash, count pre, environment, connection, metadata backup → `SafetyReport` |
| `CreateProductionBackupUseCase` | `BackupPort` | Wajib PRODUCTION → hash migration → create backup → verify backup |
| `RunFinalMigrationUseCase` | Runner dan validator use case | Validasi ulang → blokir bila gagal → delegasi runner |

### Migration Preflight (implementasi aktual)

Preflight adalah analisis opsional sebelum sesi PRE dimulai, bukan dry-run executor
atau gate approval/production. User melakukan Reset/Restore TEST, menekan **Run
Migration Preflight**, membuka **Lihat Hasil Migration Preflight**, lalu memutuskan
perbaikan dan kapan menekan Run Migration. Aplikasi tidak otomatis menjalankan SQL
setelah audit. Tombol dinonaktifkan selama sesi resume aktif atau TEST dirty setelah Diagnostic; aplikasi tidak melacak
bukti bahwa database telah direstore oleh aplikasi atau alat eksternal.

```mermaid
flowchart TD
    Backup[Production Backup] --> Reset[ResetTestDatabaseUseCase.execute]
    Reset --> Restore[MySqlClient.import_sql: restore TEST]
    Restore --> Action[PreMigrationTab._run_preflight]
    Input[migration.sql] --> Parser[SqlMigrationParser.parse_file]
    Parser --> Extract[MigrationPreflightExtractor.extract]
    Extract --> Operations[ADD FK / DROP FK / MODIFY / CHANGE]
    Action --> Worker[MainWindow._run_worker → Worker.run]
    Worker --> Audit[MigrationPreflight.execute]
    Operations --> Audit
    Metadata[information_schema TEST] --> Reader[MySqlPreflightReader]
    Data[Data TEST: orphan count] --> Reader
    Reader --> Audit
    Audit --> Result[MigrationPreflightResult]
    Result --> Signal[preflight_completed: queued signal]
    Signal --> Render[PreMigrationTab._preflight_finished]
    Render --> Review[PreflightDialog: user review]
    Review --> Run[User klik Run Migration]
    Run --> Resume[ResumablePreMigration]
    Resume --> Output[migration_final.sql]
    Output --> Approval[ApproveMigrationUseCase]
```

Hubungan file/class/method:

| Path | Simbol | Tanggung jawab |
| --- | --- | --- |
| `app/domains/migration/domain/preflight.py` | `ForeignKey`, `PreflightOperation`, `PreflightFinding`, `MigrationPreflightResult`, `PreflightStatus`, `PreflightIssueType` | Data plain; enum SAFE/BLOCKER/ERROR, relasi dan sequence SQL, count summary; tanpa Qt |
| `app/shared/sql/preflight_extractor.py` | `MigrationPreflightExtractor.extract()` | Mengonsumsi output splitter existing; ekstraksi ALTER TABLE dengan tokenizer kecil dan pembagian clause berdasarkan depth kurung; menyimpan original_sql/sequence |
| `app/domains/migration/application/preflight_ports.py` | `PreflightExtractorPort`, `PreflightDatabasePort` | `extract()`, `test_connection()`, `count_orphans()`, `get_foreign_keys()`; tidak ada arbitrary SQL execution pada port preflight |
| `app/domains/migration/application/migration_preflight.py` | `MigrationPreflight.execute()` | Tolak non-TEST sebelum I/O, baca/parse file, tes koneksi, orchestrate dua analyzer, progress, gabungkan seluruh temuan |
| File application yang sama | `ForeignKeyOrphanAnalyzer.analyze()` | ADD FK dari migration → hitung orphan TEST → SAFE/BLOCKER (potensi 1452)/ERROR; kegagalan satu candidate tidak menghentikan yang lain |
| File application yang sama | `ColumnForeignKeyDependencyAnalyzer.analyze()` | Metadata actual child/parent, evaluasi DROP sebelumnya sesuai schema/table/constraint, perhitungkan ADD sebelumnya, laporkan setiap FK aktif (potensi 1832) |
| `app/shared/database/preflight.py` | `MySqlPreflightReader.count_orphans()`, `get_foreign_keys()` | Adapter mysql CLI read-only, komposisi dengan MySqlClient untuk konfigurasi koneksi; SELECT COUNT/NOT EXISTS dan information_schema.KEY_COLUMN_USAGE |
| `app/presentation/qt/migration/pre_migration_tab.py` | `_run_preflight()`, `_preflight_finished()`, `_clear_preflight()`, `_show_preflight()` | Snapshot path/config/password sebelum dispatch; render hasil/fatal error pada queued Qt signal; invalidasi tampilan saat input/reset/run berubah |
| `app/presentation/qt/migration/preflight_dialog.py` | `PreflightDialog.__init__()`, `preflight_summary()` | Summary dan tabel Status/Type/Statement/Object/Constraint/Problem; BLOCKER, ERROR, lalu SAFE; tooltip berisi SQL asli |
| `app/bootstrap.py` | `create_main_window()` | Membuat service/extractor/reader, injection melalui MainWindow ke tab |

Sumber analisis dibedakan tegas: **migration.sql** menentukan FK baru/operasi kolom;
**information_schema TEST** menentukan FK existing (termasuk composite dan incoming
references); **data TEST** menentukan orphan. Backup production tidak diparse untuk
FK atau validasi dump. Preflight tidak menyimpan checkpoint, history, approval,
status successful, pending SQL, atau output migration_final.sql.

Orphan dihitung di database dengan `COUNT(*)`/`NOT EXISTS`, semua pasangan kolom
harus match. Semua child component harus `IS NOT NULL` agar dihitung sebagai orphan;
row composite dengan salah satu NULL dikecualikan. Metadata diambil lengkap per FK,
diurutkan berdasarkan `ORDINAL_POSITION`, dan filtered untuk kolom yang diaudit.
Lihat referensi [KEY_COLUMN_USAGE MySQL](https://dev.mysql.com/doc/refman/8.0/en/information-schema-key-column-usage-table.html).
Identifier query dibungkus backtick dengan escape backtick ganda; literal filter
metadata memakai representasi hex UTF-8. Adapter juga memeriksa Environment.TEST,
menolak FK baru lintas schema, serta memakai database konfigurasi untuk kualifikasi
tabel. Password tetap terpisah dalam MYSQL_PWD child process. Query memakai batch,
raw, dan [binary-mode mysql](https://dev.mysql.com/doc/refman/8.0/en/mysql-command-options.html#option_mysql_binary-mode).

Error fundamental (file tidak terbaca, non-TEST, koneksi gagal) menggagalkan audit.
Error ekstraksi/query individual dicatat sebagai ERROR dan candidate berikutnya
terus diproses. Summary: total statement hasil parser, candidate ADD FK, FK SAFE,
FK orphan BLOCKER, jumlah MODIFY/CHANGE, jumlah dependency FK BLOCKER, audit ERROR.
Jumlah dependency dapat lebih banyak daripada jumlah kolom. DROP sendiri tidak
menambah baris SAFE; statement di luar cakupan tidak dianggap telah tervalidasi.

Batas dukungan saat ini:

- Mendukung single/composite ADD FK, backtick/multiline/case keyword, ON DELETE/UPDATE
  CASCADE/RESTRICT/SET NULL/NO ACTION, beberapa clause ALTER, DROP FK, MODIFY, CHANGE
  (dependency memakai nama kolom lama). Definisi tipe kolom tidak divalidasi penuh.
- DROP meniadakan dependency hanya jika ada pada **statement lebih awal** di owning
  table/schema yang sama; DROP sesudah MODIFY tidak membantu. Kombinasi FK/CHANGE
  dan modification dalam satu ALTER dilaporkan ERROR karena urutannya tidak ditebak.
- MODIFY/CHANGE pada tabel yang sudah memiliki CHANGE sebelumnya dilaporkan ERROR;
  metadata hasil rename belum diproyeksikan. ADD bernama sama setelah DROP mengaktifkan
  kembali dependency yang diprediksi; audit tetap mengasumsikan statement sebelumnya sukses.
- ALTER lain, FK inline CREATE TABLE, MATCH/prefix-index FK atau syntax relevan yang
  tidak aman diekstrak menghasilkan ERROR/unsupported. Non-ALTER biasa seperti DML
  tidak dianalisis. Keterbatasan splitter existing (termasuk leading executable
  comments yang telah dibuang splitter) tetap berlaku; ini bukan validator SQL lengkap.
- Ini pembacaan state saat audit, bukan simulasi efek DML/DDL sebelumnya. Tabel/kolom
  yang baru dibuat migration dapat menyebabkan query ERROR; data yang diubah migration
  dapat membuat hasil orphan berbeda. Renaming tabel dan perubahan jenis lain tidak
  diproyeksikan. SAFE bukan jaminan migration/production akan sukses.
- Pencocokan dependency tabel konservatif terhadap casing, sedangkan DROP menggunakan
  nama table/schema persis; pada server case-insensitive dapat muncul blocker berlebih.
  Semua dependency incoming yang terlihat akun TEST dilaporkan, termasuk metadata
  lintas schema; data schema lain tidak diaudit. Kelengkapan metadata bergantung privilege akun.
- Environment.TEST memeriksa label konfigurasi, bukan membuktikan identitas server.
  Tidak ada akses database sungguhan pada test fitur; kompatibilitas server aktual
  perlu uji pada database disposable. Belum ada cancellation/timeout atau snapshot
  transaksi bersama; perubahan eksternal selama/sesudah audit tidak terlacak.
- Hasil hanya di memori; edit file di editor luar tidak otomatis terdeteksi. Jalankan
  ulang audit setelah perbaikan. UI tidak mengunci Run Migration berdasarkan hasil audit.

### Diagnostic Migration (implementasi aktual)

| Mode | Database | Eksekusi dan hasil |
| --- | --- | --- |
| Migration Preflight | TEST, read-only | Prediksi masalah FK tertentu berdasarkan migration/metadata/data aktual |
| Diagnostic Migration | Fresh TEST, mutating | Execute semua statement secara urut, lanjut setelah SQL failure, catat actual errors, export Markdown |
| PRE Migration | TEST, mutating | Fail-fast → checkpoint → edit pending SQL → resume; output final dan hasil PRE diperlukan untuk approval |

```mermaid
flowchart TD
    SQL[migration.sql] --> P[SqlMigrationParser.parse_file]
    P --> PF[MigrationPreflight: read-only analysis]
    P --> D[DiagnosticMigration: actual run / continue-on-error]
    P --> PRE[ResumablePreMigration: actual run / fail-resume]
    Reset[Reset / Restore TEST sukses] --> D
    D --> R[DiagnosticMigrationResult: actual errors]
    R --> UI[DiagnosticPanel / DiagnosticResultDialog]
    R --> MD[render_diagnostic_report → Markdown file]
    D --> Dirty[TEST DIRTY]
    Dirty --> Again[User wajib Reset / Restore TEST]
    Again --> PRE
    PRE --> Final[migration_final.sql + approval workflow existing]
```

File dan hubungan dependency:

| Path | Class/method | Peran |
| --- | --- | --- |
| `app/domains/migration/domain/diagnostic.py` | `DiagnosticMigrationResult` | Started/finished UTC, database, nama file, total, list StatementResult, fatal_error; computed success_count/failure_count/unattempted_count/completed |
| `app/domains/migration/domain/models.py` | `StatementResult` (dipakai ulang tanpa perubahan) | Sequence, SQL, success boolean, duration_ms, error_code, error_message |
| `app/domains/migration/application/diagnostic_migration.py` | `DiagnosticMigration.execute()` | Tolak non-TEST sebelum I/O, parse file, connection check, loop execute/capture/continue; tanpa history/session/approval dependency |
| File application yang sama | `diagnostic_error_text()`, `redact_diagnostic_text()` | Memakai raw stderr bila tersedia dan menyamarkan password koneksi sebelum masuk artifact/error UI |
| `app/domains/migration/application/ports.py` | `MigrationParserPort`, `DatabasePort` | Kontrak existing; diagnostic tidak menambahkan jalur execute/database backend baru |
| `app/shared/database/mysql_client.py` | `MySqlClient.execute()`, `_run()` | Mekanisme CLI existing, satu proses per statement; nonzero tetap raise RuntimeError, kini juga membawa raw stderr dan errno dari header ERROR yang dikenali |
| `app/shared/diagnostic_report.py` | `render_diagnostic_report(result, generated_at=None)` | Pure renderer Markdown; metadata, summary, semua failed SQL/errors, disclaimer cascade; tidak melakukan execution, root-cause, recommendation, atau I/O |
| `app/presentation/qt/migration/diagnostic_panel.py` | `DiagnosticPanel._run()`, `_finished()`, `_show_details()`, `_export()` | Konfirmasi Yes/No default No, snapshot input, worker dispatch, render summary/dialog, pilih tujuan export |
| File UI yang sama | `DiagnosticResultDialog`, `_selection_changed()` | Seluruh failure pada tabel; detail SQL/actual error baris terpilih |
| `app/presentation/qt/migration/pre_migration_tab.py` | `_diagnostic_inputs()`, `_diagnostic_started()`, `_reset_finished()` | Koordinasi fresh/dirty target dan gate PRE, tanpa memindahkan PRE/session logic ke diagnostic |
| `app/bootstrap.py`, `app/presentation/qt/migration/main_window.py` | `create_main_window()`, `MainWindow.__init__()` | Bootstrap membuat DiagnosticMigration(parser, db); window meneruskan dependency ke tab |

Semantik execution:

1. Diagnostic memakai file input PRE yang dipilih, bukan editor pending atau output
   sesi. Split statement tetap dari `SqlMigrationParser`; seperti
   `ResumablePreMigration._parse_pending()`, comment-only tail diabaikan. Nomor
   execution dimulai 1 sesuai sesi PRE awal, termasuk ketika splitter memiliki
   gap sequence akibat comment-only statement. SQL tidak ditulis ulang.
2. File/parser/koneksi awal gagal menghentikan run sebelum ada statement; UI menampilkan
   fatal error, belum ada artifact yang dapat diexport. Environment selain TEST ditolak
   sebelum pembacaan file atau koneksi. File tanpa SQL juga ditolak.
3. Setiap statement dipanggil tepat sekali melalui `DatabasePort.execute()`. Exception
   SQL dicatat sebagai FAILED dengan actual text, lalu statement berikutnya tetap
   berjalan. Progress bertambah setelah setiap attempt, termasuk yang gagal.
4. Error OS seperti executable mysql hilang di tengah run menghentikan loop dengan
   partial result `ABORTED`, fatal_error, dan count Unattempted. Failure yang sudah
   tercatat tetap dapat diexport. OS errno tidak dianggap sebagai kode MySQL.
   Nonzero CLI setelah initial connection tetap dicatat per statement, termasuk jika
   server kemudian tidak dapat dihubungi; diagnostic tidak melakukan retry/auto-fix.
5. Header `ERROR 1832 (HY000): ...` / `ERROR 1452 (23000) at line 1: ...`
   dikenali adapter untuk errno. Format lain menghasilkan code None; raw stderr
   (fallback stdout) tetap dipertahankan. SQLSTATE tidak diekstrak sebagai field
   tersendiri; tetap ada dalam raw message jika CLI mengembalikannya. Pesan legacy
   `str(exc)` dan raise pada nonzero tetap sama; PRE/Final juga dapat menerima errno
   numerik baru tanpa perubahan workflow mereka.
6. Diagnostic tidak memanggil Preflight dan tidak menulis checkpoint, successful
   prefix, pending SQL, history execution, approval, atau migration_final.sql. Bahkan
   semua statement SUCCESS tidak menghasilkan approval. Tidak ada rollback global,
   disabling FK checks, SQL fix, retry, reorder, atau cleanup otomatis yang ditambahkan.

Fresh/dirty dan threading:

- `PreMigrationTab._fresh_test_target` awalnya None. Diagnostic hanya diizinkan setelah
  reset/import/verify existing **dan clear session** sukses pada konfigurasi target
  yang sama di aplikasi ini. Reset dari alat eksternal tidak menandai flag ini.
- Reset worker mengembalikan snapshot DatabaseConfig. Hasil melalui `reset_completed`
  queued signal menuju slot `_reset_finished()` pada GUI thread. Reset gagal tidak
  membuat fresh dan tidak menghapus dirty marker.
- `_diagnostic_inputs()` menolak sesi PRE aktif, dirty target, target non-TEST, belum
  reset, atau file tidak ada. Sebelum dispatch, panel menampilkan host/port/database
  TEST dan konfirmasi actual execution, continue-on-error, dan kewajiban reset.
- Setelah Yes, `DiagnosticPanel.started(target)` memanggil `_diagnostic_started()`:
  target dianggap dirty **sebelum worker**, fresh dibatalkan, hasil preflight lama
  dibersihkan. Tombol PRE/approval/diagnostic/preflight dan input target dinonaktifkan;
  handler PRE/approval juga memiliki guard, bukan hanya tombol disabled. Session PRE
  tidak dibuat/diubah. Fatal error awal pun tetap memerlukan reset secara konservatif.
- Dirty hanya dilepas oleh reset sukses pada **target yang sama**. Hasil diagnostic
  lama tetap tersedia untuk export setelah reset. Edit host/port/database/username
  atau memulai PRE membatalkan flag fresh. Sesi PRE aktif harus di-reset sebelum
  diagnostic, sehingga checkpoint/resume tidak tercampur dengan diagnostic.
- Worker menerima snapshot Path/DatabaseConfig/password/service saja. Wrapper
  mengembalikan `(result, error)` dan `DiagnosticPanel.completed` memakai queued
  connection untuk `_finished()`. Database kerja di QThread; QMessageBox, dialog hasil,
  QFileDialog, dan rendering hanya pada GUI thread. MainWindow worker framework tetap.
- State fresh/dirty dan result **hanya in-memory**, bukan checkpoint persistent.
  Setelah aplikasi ditutup, result belum diexport hilang; aplikasi tidak mengingat
  dirty status untuk PRE. User tetap wajib reset sebelum PRE setelah membuka ulang.
  Diagnostic sendiri selalu mensyaratkan reset baru pada instance aplikasi baru.
  Perubahan database oleh proses lain tidak terdeteksi.

Format export default: `diagnostic_migration_YYYYMMDD_HHMMSS.md` (timestamp selesai,
UTC). Report menyertakan Generated/Started/Finished, Environment TEST, nama database,
nama file migration (bukan path lokal penuh), total/success/failure/unattempted,
COMPLETED/ABORTED, serta setiap sequence/SQL/code/raw error yang gagal. Detail SUCCESS
statement tidak dirender. Fences Markdown diperpanjang bila SQL/error mengandung
backtick agar teks tetap berada dalam code block. Report memuat note bahwa kegagalan
lanjutan bisa merupakan **secondary/cascade failure**, tanpa menentukan root cause.
Export hanya membaca result yang sudah ada; tidak mengeksekusi database lagi.

Model tidak menyimpan password, command CLI, atau environment MYSQL_PWD. Password
koneksi yang diketahui disamarkan bila muncul dalam SQL/error/nama artifact sebelum
result dibuat, sehingga raw text hanya berubah untuk redaction tersebut; SQL yang
dikirim ke database tetap SQL input asli. Ini bukan scanner semua secret arbitrer
atau data sensitif yang mungkin tertulis dalam SQL migration. Ekspor mengandung SQL
dan actual error sebagaimana diminta; akun/host kredensial tidak ditambahkan sebagai
metadata report.

Batas integrasi: **MySQL/MariaDB nyata belum diuji** oleh suite fitur ini. Test memakai
fake database, subprocess mock, dan Qt offscreen. Sebelum pemakaian nyata, uji manual
pada fresh TEST hasil restore backup: run beberapa statement sukses/gagal, cocokkan
kode/stderr actual dan sequence laporan, pastikan statement sesudah failure dicoba,
export Markdown, lalu Reset/Restore sebelum PRE. Seperti PRE existing, satu proses
mysql per statement tidak mempertahankan session variable/transaction antarstatement.
Environment.TEST adalah gate konfigurasi, bukan sandbox terhadap SQL arbitrary atau
schema-qualified SQL di file: gunakan server/akun TEST terisolasi dan migration yang
ditujukan ke TEST. Fitur ini tidak menambahkan parser keamanan dump/SQL atau validasi
identitas server. Tidak ada timeout/cancellation framework baru.

### Resume Pre-Migration

`app/domains/migration/domain/session.py:PreMigrationSession` menyimpan target TEST,
source/backup, SQL pending, hasil sukses, error terakhir, dan status in-flight.
`app/domains/migration/application/resumable_pre_migration.py:ResumablePreMigration`
menyediakan `start()`, `run()`, `invalidate()`, dan `result()`. Port session berada
pada `application/session_ports.py`; adapter `app/shared/filesystem/sessions.py:LocalSessionRepository`
menyimpan JSON secara atomic replace di `~/.khanza-migrator/sessions/<id>/`.
Password tidak disimpan. Resume UI hanya tersedia selama aplikasi terbuka; belum
ada aksi membuka sesi lama setelah restart.

`run()` menandai in-flight sebelum SQL, menyimpan successful prefix setiap selesai,
dan berhenti pada error pertama. Editor hanya mengganti pending tail; prefix tidak
ikut dieksekusi ulang. Target berbeda, sesi invalidated, atau status in-flight yang
tidak pasti ditolak. Reset TEST menginvalidasi sesi sebelum operasi reset.

Setelah semua SQL berhasil, `app/shared/sql/render.py:render_statements()` membentuk
`migration_final.sql` dengan delimiter yang tidak bertabrakan. Parser existing
memeriksa round-trip statement; hasil agregat SUCCESS dan hash output dipakai oleh
approval existing. Kegagalan export dapat dicoba lagi tanpa mengulang SQL sukses.
File input asli tidak ditimpa. Checkpoint adalah catatan operasi, bukan bukti
bahwa database tidak diubah oleh proses lain. SQL gagal dapat memiliki efek parsial;
uji ulang output dari backup diperlukan untuk memastikan reproduksibilitas sebelum production.

Alur pre-migration:

```mermaid
sequenceDiagram
    participant P as PreMigrationTab
    participant M as MainWindow
    participant W as Worker
    participant U as ResumablePreMigration
    participant D as MySqlClient
    participant H as LocalHistoryRepository
    P->>P: _run_pre(): snapshot input dan editor SQL
    P->>M: _run_worker(function, log, progress, callback)
    M->>W: QThread.started → run()
    W->>U: start/load sesi lalu run(..., progress.emit)
    U->>U: parse pending; simpan checkpoint
    loop SQL pending sampai selesai atau error pertama
        U->>D: execute(config, password, sql)
        U-->>W: progress
        W-->>M: worker_progress → _handle_worker_progress
    end
    U->>H: save_execution(attempt atau hasil agregat)
    U-->>W: PreMigrationSession
    W-->>P: succeeded → _session_finished()
    W-->>M: finished → quit / cleanup
```

`Worker.succeeded` berarti function kembali normal, bukan semua SQL berhasil. Exception per statement ditangkap runner menjadi hasil FAILED; callback harus memeriksa `result.status`. Pada runner production, error di luar blok statement mencapai `Worker.failed`. Pada Pre, wrapper operasi mengembalikan pasangan sesi/error agar `_session_finished()` tetap dapat menampilkan checkpoint sukses saat terjadi error parsing/history.

`app/presentation/qt/migration/workers.py:Worker.run()` memanggil callable dengan `self.progress.emit`, mengirim succeeded/failed, dan selalu mengirim finished. `MainWindow._run_worker()` membuat QThread, menyimpan pasangan di `_active_threads`, menghubungkan progress melalui signal milik MainWindow, serta memasang quit/deleteLater/cleanup. Backup Final mengirim tombol backup sebagai `busy_button`; run/resume/reset Pre mengirim window agar interaksi terkunci selama pekerjaan. Operasi Final lain belum memiliki guard menyeluruh.

Reset, pre-run, backup, dan final-run memakai worker. Test connection langsung menggunakan adapter dari `PreMigrationTab._test_pre_connection()`. Validasi Final (`_validate_final()`), approval/hash, history read, dan penghapusan lokal masih sinkron di GUI. Migration Preflight dan Diagnostic Migration berjalan di worker; diagnostic memakai queued `DiagnosticPanel.completed`, dan reset sukses memakai queued `PreMigrationTab.reset_completed`. Hasil Preflight melalui `preflight_completed` dengan `Qt.ConnectionType.QueuedConnection` menuju slot tab. Wrapper worker mengembalikan pasangan result/error sehingga pesan fatal juga dibuat di GUI thread. Lambda pada `FinalMigrationTab._execute_final()` masih membaca widget saat function worker dijalankan; ekstraksi tab belum memperbaikinya.

Adapter dan persistence:

- `app/shared/sql/parser.py:SqlMigrationParser.parse_file()` membaca UTF-8 BOM, lalu `parse()` memecah statement dengan quote/comment/DELIMITER dasar. `_detect_type()` dan `_description()` membuat klasifikasi/deskripsi; bukan semantic SQL parser.
- `app/shared/database/mysql_client.py:MySqlClient.execute()` membuat proses `mysql --execute` baru per statement. State session SQL tidak dijamin bertahan antarstatement. `import_sql()` memasukkan file ke stdin satu proses dan menunggu selesai; progress mulai/selesai. `verify_database()` memeriksa koneksi dan jumlah tabel lebih dari nol.
- `app/shared/database/backup.py:MySqlDumpBackupProvider.create_backup()` menggunakan mysqldump dengan `--single-transaction`, routines/triggers/events dan nama database positional, **tanpa `--databases`**. Flag tersebut sudah tidak ada di source, sehingga mode dump yang menambahkan CREATE DATABASE/USE tidak dipilih; ini bukan validasi semantic terhadap isi backup yang dipilih user. `verify_backup()` memeriksa file/ukuran/SHA-256, bukan uji restore. Password dikirim melalui environment child process `MYSQL_PWD`.
- `app/shared/filesystem/history.py:LocalHistoryRepository.save_execution()` menulis `migration.sql`, `result.json`, dan `metadata.json` ke `~/.khanza-migrator/history/<tahun>/<bulan>/migration_<timestamp>/`. `save_approval()` menyimpan satu `~/.khanza-migrator/approval.json`; `load_approval()` mengembalikan dataclass.
- `save_execution()` menerima backup opsional, tetapi runner tidak mengirimkannya, termasuk alur final. Akibatnya metadata execution dari alur itu berisi `backup: null`.

## 10. Domain Layer

`app/domains/migration/domain/models.py` berisi:

| Model | Tanggung jawab |
| --- | --- |
| `DatabaseConfig` | Host, port, database, username, environment; password terpisah |
| `MigrationStatement` | Urutan, SQL, tipe, deskripsi |
| `StatementResult` | Keberhasilan, durasi, SQL, error satu statement |
| `MigrationExecutionResult` | Status/hash/database/waktu/hasil; property `success_count`, `failed_count`, `failed_statement` |
| `Approval` | Hash/nama migration, informasi test, count, waktu dan versi |
| `BackupMetadata` | Target, file, ukuran/hash backup, hash migration, waktu dan versi |
| `SafetyCheck` | Nama, hasil boolean, detail |
| `SafetyReport` | List check; property `passed` memakai `all()` |

`app/domains/migration/domain/enums.py` mendefinisikan `Environment`, `MigrationStatus`, dan `StatementType`. Domain tidak mengimpor PySide6. Model sebagian besar pembawa data; aturan workflow berada pada use case. Tidak ada state machine: hasil execution aktual SUCCESS/FAILED, sedangkan teks FINAL MIGRATION COMPLETED di UI tidak mengubah enum hasil menjadi COMPLETED.

## 11. How to Add a New Tab

Contoh berikut adalah panduan, bukan class yang sudah ada:

1. Buat `app/presentation/qt/migration/inspection_tab.py:InspectionTab(QWidget)`. Constructor menerima dependency/callback yang diperlukan dan membangun layout, mengikuti tab existing.
2. Daftarkan instance melalui `MainWindow.__init__():self.tabs.addTab(...)` di `main_window.py`. Tidak perlu registry atau base-tab framework.
3. Jika perlu adapter baru, buat wiring konkret pada `app/bootstrap.py:create_main_window()` lalu teruskan dependency melalui constructor MainWindow ke tab.
4. Hubungkan tombol ke handler tab, misalnya `_inspect()`. Operasi lama memakai callback `run_worker` dari MainWindow, dengan hasil diarahkan ke method tab.
5. Gunakan callback data terfokus untuk hubungan antar-tab, seperti `migration_path()` existing. Jangan mengirim seluruh MainWindow agar tab dapat mengambil semua widget/state.
6. Ambil nilai widget sebelum menjalankan function worker untuk fitur baru. Pola final existing yang belum aman bukan pola yang perlu disalin.

Tab presentasi murni tidak memerlukan domain/use case baru. Tab existing berada langsung di folder `migration/`; tidak perlu memindahkannya ke folder `tabs/` untuk menambah satu fitur.

## 12. How to Add a New Dialog

Konfirmasi sederhana tetap dapat memakai `QMessageBox` pada handler tab. Untuk form kompleks, contoh usulan:

1. Buat `app/presentation/qt/migration/dialogs/connection_dialog.py:ConnectionDialog(QDialog)` beserta package bila diperlukan.
2. `__init__()` menyusun input; OK/Cancel mengarah ke `accept()`/`reject()`. Validasi input dilakukan sebelum accept.
3. Method seperti `database_config() -> DatabaseConfig` mengembalikan data biasa; password tetap terpisah.
4. Handler pemanggil pada Pre/Final membuat dialog dengan parent tab, menjalankan `exec()`, memeriksa Accepted, lalu mengambil data untuk use case/worker.
5. Dialog tidak memasukkan Qt ke application/domain. Operasi panjang tidak dijalankan sinkron hanya karena berada di dialog.

Path/class contoh `ConnectionDialog` ini belum ada; dialog hasil existing adalah `migration/preflight_dialog.py:PreflightDialog`. Dialog baru hanya diperlukan jika kompleksitas form membutuhkannya; jangan membungkus semua message box dalam class baru.

## 13. How to Add a New Feature

Contoh preview SQL tanpa eksekusi:

| Kebutuhan | Lokasi usulan | Hubungan |
| --- | --- | --- |
| Orchestration | `app/domains/migration/application/preview_migration.py:PreviewMigrationUseCase.execute(path)` | Memakai `MigrationParserPort.parse_file()` → `list[MigrationStatement]` |
| Presentasi | Handler pada `PreMigrationTab` atau tab/dialog baru | Input path → worker/use case → tampilkan hasil |
| Wiring | `app/bootstrap.py:create_main_window()` jika ada dependency baru | Adapter → constructor MainWindow → tab |
| Test | `tests/test_preview_migration.py` | Fake parser, tanpa Qt/MySQL untuk use case |

Gunakan model dan port existing bila cukup. Aturan murni dapat menjadi function domain; orchestration I/O berada di application. Tambahkan port hanya untuk kemampuan eksternal yang belum ada, lalu adapter di lokasi existing `app/shared/`. Pemindahan adapter ke infrastructure merupakan pekerjaan terpisah.

Callback progress existing adalah `ProgressCallback = Callable[[str, int, int], None]` di `application/ports.py`. Application tidak perlu mengenal Qt signal. Buat domain baru hanya jika konsep bisnisnya berbeda, bukan sekadar karena ada tab baru. Tidak perlu event bus, base repository, atau framework service.

## 14. Current Architecture Problems

Pembuatan dependency dan layout tiga tab sudah keluar dari MainWindow. Masalah lama berupa satu class berisi seluruh UI telah dikurangi; masalah berikut masih terlihat pada source:

| Lokasi | Kondisi existing dan dampaknya |
| --- | --- |
| `main_window.py:MainWindow._run_worker()` | Lifecycle thread masih bersama shell UI. Close saat worker aktif sekarang ditolak, tetapi cancellation dan guard operasi Final belum menyeluruh |
| `final_migration_tab.py:FinalMigrationTab._execute_final()` | Function worker membaca widget dan `last_backup` saat dijalankan; belum menggunakan snapshot input seperti pre/reset/backup |
| Pre `_test_pre_connection()`, `_approve()`; Final `_validate_final()`, `_update_final_state()`; History `_show_history()`, `_reset_all_data()` | I/O masih sinkron di GUI dan dapat membekukan UI; pemisahan class tidak mengubah threading |
| `final_migration_tab.py` | Edit target/path tidak menginvalidasi metadata backup lama; pembaruan status masih berupa callback eksplisit |
| `history_tab.py:HistoryTab` | UI mengetahui root, format JSON, approval path, dan menghapus file langsung. Belum ada boundary query/delete; import widget juga masih terduplikasi |
| `HistoryTab._reset_all_data()` | Tidak mereset `PreMigrationTab.last_pre_result`/`FinalMigrationTab.last_backup` atau mengupdate status final; tidak mengunci operasi worker. Error dapat terjadi setelah sebagian file terhapus |
| `MainWindow._set_final_migration()`, `_update_final_state()` | Delegasi lintas komponen masih memanggil method Final berawalan underscore. Callback cukup sederhana, tetapi kontrak belum diekspresikan sebagai API publik |
| `use_cases.py:ValidateFinalMigrationUseCase.execute()` | Backup dianggap valid jika metadata tidak None; target/hash/file tidak diverifikasi ulang. Readable hanya is_file dan ukuran >= 0. Gate lain gagal tidak menghentikan pemeriksaan koneksi PRODUCTION |
| `ApproveMigrationUseCase.execute()` | Memeriksa status/hash, bukan konsistensi count atau provenance TEST; model hasil tidak membawa environment |
| `RunMigrationUseCase.execute()` | Nol statement dapat SUCCESS, tetapi final gate mensyaratkan success_count > 0; kegagalan persistence dapat muncul setelah SQL selesai |
| `RunFinalMigrationUseCase` → `RunMigrationUseCase` | Metadata backup tidak diteruskan ke history execution |
| `LocalHistoryRepository` | Satu approval global; timestamp folder sampai detik dapat bentrok pada execution berdekatan |
| `MySqlClient.execute()` | Proses baru per statement, sehingga state session tidak bertahan; mengganti koneksi merupakan perubahan semantik |
| `ResetTestDatabaseUseCase` → `MySqlClient.import_sql()` | Label TEST tidak memvalidasi isi dump; SQL `USE`/DDL dalam file dapat menentukan targetnya sendiri. Belum diverifikasi melalui integrasi database |
| Pre `_pre_config()`, Final `_final_config()` | Konversi `int(port)` langsung, sebagian di luar try handler |
| Worker/reset/import | Masih banyak `print()` yang tidak otomatis masuk logging file |

Semua nama file singkat pada tabel UI berada di `app/presentation/qt/migration/`; use case di `app/domains/migration/application/use_cases.py`; adapter di `app/shared/` seperti bagian 9. Temuan ini bukan perubahan yang sudah diterapkan.

Test repository terdiri dari baseline application/history, hash/parser/safety, resume, preflight, dan diagnostic. Baseline sebelum fitur resume berjumlah 57 test. Baseline sebelum Diagnostic adalah **130 passed**. Verifikasi setelah Diagnostic menghasilkan **161 passed, 0 failed, 0 skipped**, termasuk regresi resume dan Qt offscreen dengan QThread nyata/database palsu. Empat file test preflight pada bagian 4 mencakup extractor, fake-port analyzer, query adapter (subprocess stub dan SQLite in-memory untuk ekspresi orphan), serta snapshot/render/fatal error GUI thread. Empat file `test_diagnostic_*.py` menguji continue-on-error, actual CLI error capture, partial/fatal run, redaction, Markdown, fresh/dirty/reset, snapshot worker, rendering GUI thread, dan export tanpa re-run. Tidak ada MySQL sungguhan atau migration nyata dijalankan. Fitur resume menambahkan `tests/test_resumable_pre_migration.py` dan test Qt offscreen `tests/test_pre_migration_resume_ui.py`; test UI memakai QThread nyata dengan fake database dan dapat di-skip bila PySide6 tidak tersedia. Smoke test terdahulu tidak membuktikan integrasi MySQL, keamanan seluruh callback thread, atau behavior penghapusan lokal yang sekarang ada di History.

## 15. Recommended Refactor

Pembuatan composition root dan ekstraksi tiga tab sudah selesai; jangan mengulangnya sebagai pekerjaan baru. Pekerjaan lanjutan yang masih relevan:

1. Ekstrak lifecycle worker dari MainWindow ke `app/presentation/qt/workers/` dalam perubahan terfokus. `TaskRunner` kecil dapat dipertimbangkan, tetapi belum ada dan tidak perlu menjadi framework.
2. Pisahkan query history dari rendering. Rencanakan boundary operasi hapus secara tersendiri karena efek dan failure semantics berbeda dari query.
3. Perbaiki snapshot final, blocking I/O, busy/shutdown, dan invalidation state sebagai perubahan behavior eksplisit dengan test; jangan digabungkan diam-diam ke pemindahan file.
4. Kelompokkan adapter di infrastructure bila manfaat navigasinya diperlukan. Pertahankan command SQL, format persistence, dan kontrak saat hanya memindahkan lokasi.
5. Pecah use case berdasarkan operasi bila membantu; jangan menduplikasi application migration ke layer baru yang kosong.
6. Perkuat gate backup/approval dan audit metadata dengan spesifikasi terpisah. Pertahankan baseline sampai perubahan safety memang disetujui.
7. Rapikan type hint callback, API antar-tab, sisa kode/import/logging setelah pemanggil dan behavior terverifikasi.

Tidak diperlukan DDD penuh, event bus, container dependency, atau hierarki base-tab. Custom dialog dibuat ketika ada form kompleks, bukan sekadar mengganti pemanggilan statis Qt.

## 16. Proposed Directory Structure

Target opsional bertahap berikut mempertahankan lokasi tab yang sudah diekstrak. Bagian worker runner, infrastructure, dan file use case terpisah **belum diimplementasikan**. File `__init__.py` tidak ditampilkan.

```text
app/
├── main.py                         # Entry GUI, sudah ada
├── cli.py                          # CLI hash/parse
├── bootstrap.py                    # Composition root, sudah ada
├── config.py                       # Konfigurasi
├── domains/migration/
│   ├── domain/
│   │   ├── models.py               # Data dan hasil domain
│   │   └── enums.py
│   └── application/
│       ├── ports.py                # Kontrak I/O
│       ├── reset_test_database.py  # Pemecahan use_cases.py bila diperlukan
│       ├── run_migration.py
│       ├── approve_migration.py
│       ├── validate_final_migration.py
│       ├── create_production_backup.py
│       ├── run_final_migration.py
│       └── list_history.py         # Query data history terstruktur
├── infrastructure/
│   ├── database/
│   │   ├── mysql_client.py         # Adapter database
│   │   └── backup.py               # Adapter backup
│   ├── filesystem/history.py       # Persistence/query lokal
│   └── sql/parser.py               # Adapter parser
├── presentation/qt/
│   ├── app.py                      # Startup Qt, sudah ada
│   ├── workers/
│   │   ├── worker.py               # Callable dan signal
│   │   └── task_runner.py          # Ownership/lifecycle thread
│   └── migration/
│       ├── main_window.py          # Koordinasi tab
│       ├── pre_migration_tab.py     # Sudah ada
│       ├── final_migration_tab.py   # Sudah ada
│       ├── history_tab.py          # Sudah ada
│       └── dialogs/                # Opsional jika ada form kompleks
└── shared/
    ├── hashing/sha256.py           # Utility umum
    └── logging/logger.py
tests/
├── application/                    # Use case dengan fake port
├── infrastructure/                 # Kontrak adapter terisolasi
└── presentation/                   # State/signal/lifecycle UI
docs/ARCHITECTURE.md
```

Bootstrap mengenal adapter konkret dan UI; application mengatur workflow tanpa Qt; infrastructure mengimplementasikan I/O; presentation mengelola pengguna dan thread Qt; shared hanya utility umum. `app/application/` lintas domain belum diperlukan selama workflow masih khusus migration. Tree test di atas adalah pengelompokan usulan, bukan lokasi test existing atau instruksi memindahkannya sekarang.

## 17. Refactor Roadmap

| Tahap | Status berdasarkan source dan pekerjaan terdahulu | Langkah/verifikasi |
| --- | --- | --- |
| 1. Tetapkan baseline | Selesai; 57 kasus pada suite terakhir yang dilaporkan | Pertahankan baseline use case, count/hash, stop-on-first-error dan format persistence |
| 2. Selaraskan instalasi | Selesai | Python >=3.10, runtime dotenv, extra dev pytest; instalasi bersih/import/startup telah diverifikasi pada tahap tersebut |
| 3. Ekstrak composition | Selesai | `bootstrap.create_main_window()`, `qt/app.py:run_app()`, constructor injection MainWindow |
| 4. Ekstrak History | Ekstraksi UI selesai; boundary query belum dibuat | `HistoryTab` tetap membaca JSON langsung. Source saat ini juga memiliki aksi hapus lokal; dokumentasikan/uji sebelum mengubah boundary |
| 5. Ekstrak tab migration | Pre dan Final selesai | State/handler berada di tab; callback Pre → MainWindow → Final dan worker bersama tetap digunakan |
| 6. Ekstrak worker runner | Belum | Pindahkan lifecycle terpisah, verifikasi result/progress/error/cleanup tanpa sekaligus mengubah policy operasi |
| 7. Perbaiki thread/state | Belum menyeluruh; preflight/diagnostic memakai snapshot dan queued result, reset sukses juga queued | Snapshot final, I/O background, busy guard, shutdown, serta sinkronisasi state setelah reset data lokal; test sebagai perubahan behavior |
| 8. Rapikan adapter/use case | Belum | Pindahkan lokasi bila diperlukan, perbarui import dan jalankan suite tanpa mengubah backend/format |
| 9. Perkuat safety/audit | Belum | Backup-target/hash, verifikasi ulang, asal TEST, metadata history, dan validasi dump; gunakan database disposable untuk integrasi |
| 10. Bersihkan sisa kode | Belum menyeluruh | Hapus jalur usang/import duplikat, seragamkan logging/type hint setelah verifikasi pemanggil |

Pemeriksaan pada tahap ekstraksi terdahulu mencakup 57 test serta smoke Qt offscreen (tab, callback, input dan hasil dengan stub). Fitur resume memiliki test tambahan untuk successful prefix, edit tail, export/approval, checkpoint failure, dan interaksi Qt; aksi hapus data lokal tidak diuji ulang pada pekerjaan fitur ini. Setiap tahap berikutnya tetap perlu scope dan pengujian sendiri; perubahan sesi SQL, cancellation subprocess, atau model multi-approval merupakan keputusan behavior tersendiri.
