import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def env_int(key: str, default: int) -> int:
    value = os.getenv(key)

    if value is None or value.strip() == "":
        return default

    return int(value)


# ==========================================
# PRE-MIGRATION / TEST
# ==========================================

PRE_DB_HOST = env("PRE_DB_HOST", "127.0.0.1")
PRE_DB_PORT = env_int("PRE_DB_PORT", 3306)
PRE_DB_DATABASE = env("PRE_DB_DATABASE")
PRE_DB_USERNAME = env("PRE_DB_USERNAME")
PRE_DB_PASSWORD = env("PRE_DB_PASSWORD")

PRE_MIGRATION_FILE = env("PRE_MIGRATION_FILE")
PRE_BACKUP_FILE = env("PRE_BACKUP_FILE")


# ==========================================
# PRODUCTION
# ==========================================

FINAL_DB_HOST = env("FINAL_DB_HOST", "127.0.0.1")
FINAL_DB_PORT = env_int("FINAL_DB_PORT", 3306)
FINAL_DB_DATABASE = env("FINAL_DB_DATABASE")
FINAL_DB_USERNAME = env("FINAL_DB_USERNAME")
FINAL_DB_PASSWORD = env("FINAL_DB_PASSWORD")