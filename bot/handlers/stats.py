from aiogram import Router, types
from aiogram.filters import Command
from bot.utils.logger import BotLogger
from bot.utils.config import config
from bot.services.logger import DatabaseLogger
from datetime import datetime

logger = BotLogger.get_logger()
router = Router()
db_logger = DatabaseLogger()


@router.message(Command('stats'))
async def cmd_stats(message: types.Message) -> None:
    user_id = message.from_user.id

    if user_id != config.ALLOWED_USER_ID:
        await message.answer('🚫 Access denied.')
        return

    stats = await db_logger.get_stats(user_id)

    total_size_gb = stats['total_size'] / (1024 ** 3)
    avg_speed_mbps = stats['avg_speed'] / (1024 ** 2) if stats['avg_speed'] > 0 else 0

    last_download = 'Never'
    if stats['last_download']:
        last_dt = datetime.fromisoformat(stats['last_download'])
        diff = datetime.now() - last_dt
        hours = diff.total_seconds() / 3600
        minutes = diff.total_seconds() / 60

        if hours >= 1:
            last_download = f'{int(hours)} hours ago'
        elif minutes >= 1:
            last_download = f'{int(minutes)} minutes ago'
        else:
            last_download = 'Just now'

    stats_text = f'''📊 Your Statistics:
Total downloaded: {stats['total_count']} videos
Audio files: {stats['audio_count']}
Total size: {total_size_gb:.1f} GB
Average speed: {avg_speed_mbps:.1f} MB/s
Last download: {last_download}'''

    await message.answer(stats_text)


@router.message(Command('history'))
async def cmd_history(message: types.Message) -> None:
    user_id = message.from_user.id

    if user_id != config.ALLOWED_USER_ID:
        await message.answer('🚫 Access denied.')
        return

    args = message.text.split()
    limit = 10
    if len(args) > 1:
        try:
            limit = int(args[1])
            limit = min(limit, 50)
        except ValueError:
            pass

    history = await db_logger.get_history(user_id, limit)

    if not history:
        await message.answer('📜 No download history.')
        return

    history_text = '📜 Download History:\n\n'
    for i, entry in enumerate(history, 1):
        file_size_mb = entry['file_size'] / (1024 * 1024)
        date = datetime.fromisoformat(entry['timestamp']).strftime('%d.%m.%Y %H:%M')

        emoji = '🎬' if 'video' in entry['format'].lower() else '🎵'
        history_text += f'{i}. {emoji} {entry["title"][:40]}\n'
        history_text += f'   Format: {entry["format"]} | Size: {file_size_mb:.1f} MB\n'
        history_text += f'   📅 {date}\n\n'

    await message.answer(history_text)
