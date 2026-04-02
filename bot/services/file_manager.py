import asyncio
from pathlib import Path
from typing import Optional
from bot.utils.config import config
from bot.utils.logger import BotLogger

logger = BotLogger.get_logger()


class FileManager:
    def __init__(self):
        self.downloads_path = Path(config.DOWNLOADS_PATH)
        self.downloads_path.mkdir(parents=True, exist_ok=True)

    async def send_file(self, filepath: str, send_callback) -> bool:
        try:
            file_path = Path(filepath)
            if not file_path.exists():
                logger.error(f'File not found: {filepath}')
                return False

            await send_callback(filepath)
            return True

        except Exception as e:
            logger.error(f'Failed to send file {filepath}: {str(e)}')
            return False

    async def cleanup_file(self, filepath: str) -> None:
        try:
            file_path = Path(filepath)
            if file_path.exists():
                file_path.unlink()
                logger.info(f'Cleaned up file: {filepath}')
        except Exception as e:
            logger.error(f'Failed to cleanup file {filepath}: {str(e)}')

    def get_unique_download_dir(self, user_id: int, download_id: str) -> str:
        unique_dir = self.downloads_path / f'{user_id}_{download_id}'
        unique_dir.mkdir(parents=True, exist_ok=True)
        return str(unique_dir)

    async def cleanup_download_dir(self, directory: str) -> None:
        try:
            dir_path = Path(directory)
            if dir_path.exists():
                import shutil
                shutil.rmtree(dir_path)
                logger.info(f'Cleaned up download directory: {directory}')
        except Exception as e:
            logger.error(f'Failed to cleanup directory {directory}: {str(e)}')
