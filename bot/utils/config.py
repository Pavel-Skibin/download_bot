import os
from pathlib import Path
from dotenv import load_dotenv

env_file = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_file)


class Config:
    TELEGRAM_BOT_TOKEN: str = os.getenv('TELEGRAM_BOT_TOKEN', '')
    ALLOWED_USER_ID: int = int(os.getenv('ALLOWED_USER_ID', '0'))

    RUTRACKER_LOGIN: str = os.getenv('RUTRACKER_LOGIN', '').strip()
    RUTRACKER_PASSWORD: str = os.getenv('RUTRACKER_PASSWORD', '').strip()
    RUTRACKER_PROXY: str = os.getenv('RUTRACKER_PROXY', '').strip()

    EXTERNAL_URL: str = os.getenv('EXTERNAL_URL', '').strip()
    AIOTORRENT_STREAM_URL_TEMPLATE: str = os.getenv('AIOTORRENT_STREAM_URL_TEMPLATE', '').strip()
    AIOTORRENT_HOST: str = os.getenv('AIOTORRENT_HOST', '127.0.0.1').strip()

    DOWNLOADS_PATH: str = os.getenv('DOWNLOADS_PATH', './data/downloads')
    DATABASE_PATH: str = os.getenv('DATABASE_PATH', './data/database.db')
    LOGS_PATH: str = os.getenv('LOGS_PATH', './logs')

    MAX_CONCURRENT_DOWNLOADS: int = int(os.getenv('MAX_CONCURRENT_DOWNLOADS', '2'))
    MAX_FILE_SIZE_GB: int = int(os.getenv('MAX_FILE_SIZE_GB', '2'))
    CLEANUP_AFTER_SEND: bool = os.getenv('CLEANUP_AFTER_SEND', 'true').lower() == 'true'

    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    LOG_MAX_SIZE_MB: int = int(os.getenv('LOG_MAX_SIZE_MB', '50'))

    @classmethod
    def init(cls) -> None:
        Path(cls.DOWNLOADS_PATH).mkdir(parents=True, exist_ok=True)
        Path(cls.LOGS_PATH).mkdir(parents=True, exist_ok=True)


config = Config()
config.init()
