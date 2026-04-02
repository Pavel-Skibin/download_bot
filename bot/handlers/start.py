from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from bot.handlers import DownloadStates
from bot.utils.validators import URLValidator
from bot.utils.logger import BotLogger
from bot.utils.config import config

logger = BotLogger.get_logger()
router = Router()


async def check_access(user_id: int) -> bool:
    return user_id == config.ALLOWED_USER_ID


@router.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if not await check_access(user_id):
        await message.answer('🚫 Access denied.\nThis bot is for personal use only.')
        return

    await state.clear()
    await message.answer('''👋 Hello! I'll help you download videos and music.

Supported platforms:
• YouTube
• YouTube Music
• Rutube
• VK Video

Just send me a link and I'll offer you download options!

/help - Full guide
/stats - Your statistics
/history - Download history''')


@router.message(Command('help'))
async def cmd_help(message: types.Message) -> None:
    user_id = message.from_user.id

    if not await check_access(user_id):
        await message.answer('🚫 Access denied.')
        return

    await message.answer('''📖 Help Guide

How to use:
1. Send me a video or music link from YouTube, YouTube Music, Rutube, or VK Video
2. I'll show you available quality options
3. Choose the format you want
4. I'll download and send you the file

Commands:
/start - Welcome message
/help - This help
/stats - Download statistics
/history [N] - Last N downloads (default 10)
/cancel - Cancel current operation

Supported formats:
• Video: MP4 (H.264)
• Audio: MP3 (320/128 kbps)

Limits:
• Maximum file size: 2 GB
• Maximum concurrent downloads: 2''')


@router.message(Command('cancel'))
async def cmd_cancel(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if not await check_access(user_id):
        await message.answer('🚫 Access denied.')
        return

    await state.clear()
    await message.answer('❌ Operation cancelled.')


@router.message(lambda msg: msg.text and msg.text.startswith('http'))
async def handle_url(message: types.Message, state: FSMContext) -> None:
    user_id = message.from_user.id

    if not await check_access(user_id):
        await message.answer('🚫 Access denied.')
        return

    url = message.text.strip()
    is_valid, platform = URLValidator.validate_and_get_platform(url)

    if not is_valid:
        await message.answer('❌ Invalid or unsupported URL.\nSupported: YouTube, YouTube Music, Rutube, VK Video')
        return

    await state.update_data(url=url, platform=platform)
    await message.answer('⏳ Getting video information...')

    try:
        from bot.services.downloader import Downloader
        downloader = Downloader()
        metadata = await downloader.get_metadata(url)

        if not metadata:
            await message.answer('❌ Failed to get video info.\nCheck the link.')
            return

        duration_min, duration_sec = divmod(metadata.duration, 60)
        duration_str = f'{int(duration_min)}:{int(duration_sec):02d}'

        info_text = f'''📺 {metadata.title}
👤 {metadata.uploader}
⏱ {duration_str}

Available formats:
Please choose quality below:'''

        qualities = metadata.get_available_qualities()
        from bot.keyboards.download_kb import DownloadKeyboards

        if metadata.is_playlist and platform == 'youtube_music':
            playlist_entries = metadata.get_playlist_entries()
            if not playlist_entries:
                await message.answer('❌ Failed to read playlist tracks.')
                return

            playlist_text = f'''🎵 YouTube Music playlist detected
📀 {metadata.title}
👤 {metadata.uploader}
🎼 Tracks: {len(playlist_entries)}

I can download all songs one by one.
Each song will be sent separately, as a file or a link if it is too large.'''

            await state.update_data(
                metadata=metadata,
                selected_url=url,
                playlist_entries=playlist_entries,
                playlist_title=metadata.title,
                playlist_uploader=metadata.uploader,
                platform=platform
            )
            await state.set_state(DownloadStates.processing_playlist)
            await message.answer(playlist_text, reply_markup=DownloadKeyboards.playlist_confirmation())
            return

        if not qualities:
            await message.answer('❌ No available formats.')
            return

        keyboard = DownloadKeyboards.quality_selection(qualities)

        await state.update_data(
            metadata=metadata,
            qualities=qualities,
            selected_url=url
        )
        await state.set_state(DownloadStates.waiting_for_quality)

        await message.answer(info_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f'Error handling URL: {str(e)}')
        await message.answer('🌐 Network error. Try again later.')
