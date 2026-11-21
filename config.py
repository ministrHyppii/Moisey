import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # папка, где лежит main.py
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── окружение ────────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "1012623951719051284"))  # неограниченные команды
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID", "0") or 0) or None  # для мгновенной синхронизации слэш-команд
# База и все записи (карточки, выговоры, ЧС, настройки) хранятся в папке data рядом с main.py,
# независимо от того, из какой папки запущен бот.
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.getenv("DB_PATH") or os.path.join(DATA_DIR, "modbot.db")
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(BASE_DIR, DB_PATH)
PREFIXES = ("m!", "M!")

# ── правила наказаний ────────────────────────────────────────
MAX_WARNS = 3  # 3 предупреждения = 1 выговор
MAX_REPS = 3   # 3 выговора = аннуляция + сообщение

# ── цвета ────────────────────────────────────────────────────
BLURPLE = 0x5865F2
GREEN = 0x57F287
RED = 0xED4245

# ── возможности, которые можно выдавать степеням ─────────────
CAPS = {
    "warn_add": "Выдача предупреждений",
    "warn_remove": "Снятие предупреждений",
    "rep_add": "Выдача выговоров",
    "rep_remove": "Снятие выговоров",
    "bl_add": "Выдача ЧС",
    "bl_remove": "Снятие ЧС",
    "card_edit": "Редактирование карточек участников",
}
CAP_ADD = {"warn": "warn_add", "rep": "rep_add", "bl": "bl_add"}
CAP_REM = {"warn": "warn_remove", "rep": "rep_remove", "bl": "bl_remove"}

KINDS = {
    "warn": {"name": "Предупреждение", "emoji": "⚠️", "color": 0xFEE75C},
    "rep": {"name": "Выговор", "emoji": "📛", "color": 0xE67E22},
    "bl": {"name": "ЧС", "emoji": "⛔", "color": RED},
}
STATUS_TXT = {
    "active": "🔴 действует",
    "removed": "✅ снято",
    "converted": "🔁 стало выговором",
    "annulled": "🗑️ аннулировано",
}
MODE_TXT = {"moderator": "🛡️ Модератор", "watcher": "👁️ Смотрящий"}
