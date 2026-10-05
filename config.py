"""Configuration for Ahmed & Janna's shared Telegram bot."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer when provided") from exc


def _int(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def normalize_username(value: str) -> str:
    return value.strip().lstrip("@").lower()


ROLE_NAMES = {"ahmed": "أحمد", "janna": "جنى"}


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str
    group_chat_id: int
    ahmed_username: str
    janna_username: str
    timezone_name: str
    database_path: str
    port: int
    mulk_audio_file_id: str | None
    sleep_audio_file_id: str | None
    legacy_ahmed_user_id: int | None
    legacy_janna_user_id: int | None

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    def role_for_username(self, username: str | None) -> str | None:
        if not username:
            return None
        normalized = normalize_username(username)
        if normalized == normalize_username(self.ahmed_username):
            return "ahmed"
        if normalized == normalize_username(self.janna_username):
            return "janna"
        return None


CONFIG = Config(
    bot_token=_required("BOT_TOKEN"),
    group_chat_id=_int("GROUP_CHAT_ID"),
    ahmed_username=_required("AHMED_USERNAME"),
    janna_username=_required("JANNA_USERNAME"),
    timezone_name=os.getenv("TIMEZONE", "Asia/Riyadh").strip(),
    database_path=os.getenv("DATABASE_PATH", "bot.db").strip(),
    port=_int("PORT", 10000),
    mulk_audio_file_id=os.getenv("MULK_AUDIO_FILE_ID", "").strip() or None,
    sleep_audio_file_id=os.getenv("SLEEP_AUDIO_FILE_ID", "").strip() or None,
    legacy_ahmed_user_id=_optional_int("AHMED_USER_ID"),
    legacy_janna_user_id=_optional_int("JANNA_USER_ID"),
)

# ============================================================
# EDITABLE MEDIA CONFIG
# ============================================================
# Put the images next to bot.py, or use a relative subfolder path.
# Examples:
#   "azkar_morning.jpg"
#   "media/azkar_morning.png"
#   r"C:\Users\ahmed\Desktop\Azkar Together\azkar_morning.jpg"
# Leave a value empty ("") to send the azkar as a normal text message.
AZKAR_IMAGES = {
    "morning": "azkar_morning.jpg",
    "evening": "azkar_evening.jpg",
    "sleep": "azkar_sleep.jpg",
}

# ============================================================
# ONLINE QURAN SOURCE
# ============================================================
# We fetch only the 10 assigned pages each day. No local 604-image folder is needed.
QURAN_API_BASE_URL = os.getenv(
    "QURAN_API_BASE_URL", "https://api.alquran.cloud/v1"
).strip().rstrip("/")
QURAN_EDITION = os.getenv("QURAN_EDITION", "quran-uthmani").strip()
QURAN_TOTAL_PAGES = _int("QURAN_TOTAL_PAGES", 604)
QURAN_DAILY_PAGES = _int("QURAN_DAILY_PAGES", 10)
QURAN_FIRST_PAGE = _int("QURAN_FIRST_PAGE", 1)

# ============================================================
# DAILY SCHEDULE — Asia/Riyadh by default
# ============================================================
SCHEDULE = {
    "morning": (6, 0),
    "evening": (16, 0),
    "sleep": (22, 0),
    # Ahmed: one medication reminder at 14:00.
    "ahmed_medication": (14, 0),
    # Janna: before-lunch dose at 12:00 + a fixed re-reminder at 13:00 if unfinished.
    "janna_pre_lunch": (12, 0),
    "janna_pre_lunch_retry": (13, 0),
    # Janna: a separate after-lunch medication at 16:00.
    "janna_after_lunch": (16, 0),
    "quran": (18, 0),
    "weekly_digest": (14, 0),
    "streak_check": (0, 5),
}

FOCUS_DEFAULT_MINUTES = 120
SNOOZE_MINUTES = 15
TASBEEH_DEFAULT_TARGET = 500


def resolve_asset_path(value: str) -> Path | None:
    """Resolve an absolute path or a path relative to the project folder."""
    value = (value or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return candidate


def azkar_image_path(item_key: str) -> Path | None:
    return resolve_asset_path(AZKAR_IMAGES.get(item_key, ""))
