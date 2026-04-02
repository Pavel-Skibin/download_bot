import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from bot.utils.config import config


class BotLogger:
    _logger: logging.Logger = None

    @classmethod
    def setup(cls) -> logging.Logger:
        if cls._logger:
            return cls._logger

        cls._logger = logging.getLogger('telegram_bot')
        cls._logger.setLevel(getattr(logging, config.LOG_LEVEL))

        log_file = Path(config.LOGS_PATH) / 'bot.log'
        max_bytes = config.LOG_MAX_SIZE_MB * 1024 * 1024

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=3
        )

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        file_handler.setFormatter(formatter)
        cls._logger.addHandler(file_handler)

        return cls._logger

    @classmethod
    def get_logger(cls) -> logging.Logger:
        if not cls._logger:
            cls.setup()
        return cls._logger
