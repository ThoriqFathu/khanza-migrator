import os
import re
import subprocess
import tempfile
from pathlib import Path

from app.domains.migration.domain.models import DatabaseConfig
from app.domains.migration.application.ports import ProgressCallback


class MySqlClient:
    """
    Adapter untuk mysql/mariadb client CLI.

    Menggunakan environment variable MYSQL_PWD hanya pada child process
    sehingga password tidak dimasukkan ke command line arguments.
    """

    def _env(self, password: str) -> dict[str, str]:
        env = os.environ.copy()
        env["MYSQL_PWD"] = password
        return env

    def _base(self, config: DatabaseConfig) -> list[str]:
        return [
            "mysql",
            "--host", config.host,
            "--port", str(config.port),
            "--user", config.username,
            "--protocol=tcp",
        ]

    def test_connection(self, config: DatabaseConfig, password: str) -> None:
        command = self._base(config) + ["--database", config.database, "--execute", "SELECT 1"]
        self._run(command, password)

    def execute(self, config: DatabaseConfig, password: str, sql: str) -> None:
        command = self._base(config) + ["--database", config.database, "--execute", sql]
        self._run(command, password)

    def database_exists(self, config: DatabaseConfig, password: str) -> bool:
        command = self._base(config) + ["--batch", "--skip-column-names", "--execute",
            f"SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = '{config.database.replace(chr(39), chr(39)*2)}'"]
        result = subprocess.run(
            command, env=self._env(password), capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Gagal mengecek database.")
        return config.database in result.stdout.splitlines()

    # def ensure_database(self, config: DatabaseConfig, password: str) -> bool:
    #     """
    #     Memastikan database tersedia.

    #     Returns:
    #         True  jika database sudah ada atau berhasil dibuat.
    #         False jika database belum ada dan tidak dibuat.
    #     """
    #     if self.database_exists(config, password):
    #         return False

    #     self.create_database(config, password)
    #     return True

    def create_database(self, config: DatabaseConfig, password: str) -> None:
        safe = self._quote_identifier(config.database)
        command = self._base(config) + ["--execute", f"CREATE DATABASE `{safe}`"]
        self._run(command, password)

    def drop_database(self, config: DatabaseConfig, password: str) -> None:
        safe = self._quote_identifier(config.database)
        command = self._base(config) + ["--execute", f"DROP DATABASE `{safe}`"]
        self._run(command, password)

    # def import_sql(
    #     self,
    #     config: DatabaseConfig,
    #     password: str,
    #     sql_file: Path,
    #     callback: ProgressCallback | None = None,
    # ) -> None:
    #     if not sql_file.is_file():
    #         raise FileNotFoundError(sql_file)

    #     command = self._base(config) + ["--database", config.database]
    #     total = sql_file.stat().st_size
    #     done = 0
    #     process = subprocess.Popen(
    #         command,
    #         env=self._env(password),
    #         stdin=sql_file.open("rb"),
    #         stdout=subprocess.PIPE,
    #         stderr=subprocess.PIPE,
    #     )
    #     with sql_file.open("rb") as handle:
    #         while True:
    #             chunk = handle.read(1024 * 1024)
    #             if not chunk:
    #                 break
    #             done += len(chunk)
    #             if callback:
    #                 callback("Importing backup", done, total)

    #     stdout, stderr = process.communicate()
    #     if process.returncode != 0:
    #         raise RuntimeError(stderr.decode(errors="replace").strip() or "Import backup gagal.")
    #     print(">>> mysql import selesai")

   

    def import_sql(
        self,
        config: DatabaseConfig,
        password: str,
        sql_file: Path,
        callback: ProgressCallback | None = None,
    ) -> None:
        if not sql_file.is_file():
            raise FileNotFoundError(sql_file)

        command = self._base(config) + ["--database", config.database]

        print(">>> Popen mysql")

        with sql_file.open("rb") as stdin_handle:
            with tempfile.TemporaryFile() as error_handle:
                process = subprocess.Popen(
                    command,
                    env=self._env(password),
                    stdin=stdin_handle,
                    stdout=subprocess.DEVNULL,
                    stderr=error_handle,
                )

                print(f">>> mysql PID = {process.pid}")

                if callback:
                    callback(
                        "Importing backup",
                        0,
                        0,
                    )

                print(">>> menunggu mysql selesai")

                return_code = process.wait()

                print(f">>> mysql return code = {return_code}")

                error_handle.seek(0)
                stderr = error_handle.read()

        if return_code != 0:
            error = stderr.decode(errors="replace").strip()

            raise RuntimeError(
                error or "Import backup gagal."
            )

        if callback:
            callback(
                "Import backup selesai",
                1,
                1,
            )

        print(">>> mysql import selesai")

    def verify_database(self, config: DatabaseConfig, password: str) -> None:
        self.test_connection(config, password)
        command = self._base(config) + [
            "--database", config.database,
            "--batch", "--skip-column-names",
            "--execute",
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()",
        ]
        result = subprocess.run(command, env=self._env(password), capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Verifikasi database gagal.")
        count = int(result.stdout.strip() or "0")
        if count == 0:
            raise RuntimeError("Import selesai tetapi database tidak memiliki tabel.")

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return value.replace("`", "``")

    @staticmethod
    def _run(command: list[str], password: str) -> None:
        result = subprocess.run(
            command,
            env={**os.environ, "MYSQL_PWD": password},
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "MySQL command gagal."
            exc = RuntimeError(error)
            # Preserve raw CLI output; expose a code only for the documented ERROR form.
            # No command or password environment is attached to the exception.
            raw = result.stderr or result.stdout
            match = re.search(r"^ERROR ([0-9]+) \([A-Z0-9]{5}\)(?: at line [0-9]+)?:", raw, re.M)
            setattr(exc, "errno", int(match.group(1)) if match else None)
            setattr(exc, "stderr", raw)
            raise exc
