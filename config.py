import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
    
    # Идентификация клона
    BOT_TYPE = os.getenv("BOT_TYPE", "u2tg")
    CLONE_ID = int(os.getenv("CLONE_ID", "1"))
    BOT_USERNAME = os.getenv("BOT_USERNAME", "")
    
    # Префикс для таблиц
    TABLE_PREFIX = f"{BOT_TYPE}_{CLONE_ID}_"
    
    # PostgreSQL
    DATABASE_URL = os.getenv("DATABASE_URL")
    
    # YouTube API
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
    YOUTUBE_MAX_RESULTS = int(os.getenv("YOUTUBE_MAX_RESULTS", "50"))
    YOUTUBE_SEARCH_DAYS = int(os.getenv("YOUTUBE_SEARCH_DAYS", "7"))
    
    # Пути
    SHARED_DIR = os.getenv("SHARED_DIR", "/app/shared")
    TEMP_DIR = os.path.join(SHARED_DIR, "temp")
    BACKUP_DIR = os.path.join(SHARED_DIR, "backups", f"{BOT_TYPE}_{CLONE_ID}")
    
    # Лимиты
    DEFAULT_MAX_PROJECTS = int(os.getenv("DEFAULT_MAX_PROJECTS", "1"))
    DEFAULT_MAX_SOURCES_PER_PROJECT = int(os.getenv("DEFAULT_MAX_SOURCES_PER_PROJECT", "3"))
    DEFAULT_CHECK_INTERVAL = int(os.getenv("DEFAULT_CHECK_INTERVAL", "60"))
    
    # Публикация
    DEFAULT_POST_INTERVAL_HOURS = int(os.getenv("DEFAULT_POST_INTERVAL_HOURS", "2"))
    MIN_POST_INTERVAL_MINUTES = int(os.getenv("MIN_POST_INTERVAL_MINUTES", "30"))
    DEFAULT_ACTIVE_HOURS_START = int(os.getenv("DEFAULT_ACTIVE_HOURS_START", "8"))
    DEFAULT_ACTIVE_HOURS_END = int(os.getenv("DEFAULT_ACTIVE_HOURS_END", "22"))
    
    SHOW_SOURCE_SIGNATURE = os.getenv("SHOW_SOURCE_SIGNATURE", "false").lower() == "true"
    
    TIMEZONE = "Europe/Moscow"
    
    BOT_CONNECT_TIMEOUT = 30
    BOT_READ_TIMEOUT = 60
    BOT_WRITE_TIMEOUT = 60

    @classmethod
    def validate(cls):
        if not cls.BOT_TOKEN:
            raise ValueError("BOT_TOKEN is required")
        if not cls.DATABASE_URL:
            raise ValueError("DATABASE_URL is required")

Config.validate()