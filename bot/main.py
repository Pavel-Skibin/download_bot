import asyncio
import logging
from aiogram import Dispatcher, Bot, Router
from aiogram.fsm.storage.memory import MemoryStorage
from bot.utils.config import config
from bot.utils.logger import BotLogger
from bot.handlers import start, download, stats, movie
from bot.services.logger import DatabaseLogger
from bot.services.http_server import init_http_server

BotLogger.setup()
logger = BotLogger.get_logger()


async def main() -> None:
    logger.info('Starting Telegram Video Bot...')
    logger.info(f'Allowed user ID: {config.ALLOWED_USER_ID}')

    db_logger = DatabaseLogger()
    await db_logger.init_db()
    logger.info('Database initialized')

    http_server = await init_http_server()
    logger.info('HTTP server initialized on port 8080')

    storage = MemoryStorage()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    router = Router()
    router.include_router(start.router)
    router.include_router(download.router)
    router.include_router(movie.router)
    router.include_router(stats.router)

    dp.include_router(router)

    logger.info('Bot is running...')

    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f'Bot error: {str(e)}')
    finally:
        await bot.session.close()
        logger.info('Bot stopped')


if __name__ == '__main__':
    asyncio.run(main())
