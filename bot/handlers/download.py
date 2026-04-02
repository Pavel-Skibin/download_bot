import asyncio
import html
import time
import uuid
from types import SimpleNamespace
from typing import Dict, Optional
from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile
from bot.handlers import DownloadStates
from bot.utils.logger import BotLogger
from bot.utils.config import config
from bot.services.downloader import Downloader
from bot.services.file_manager import FileManager
from bot.services.logger import DatabaseLogger
from bot.services.http_server import register_file_for_download, get_download_url

logger = BotLogger.get_logger()
router = Router()

downloader = Downloader()
file_manager = FileManager()
db_logger = DatabaseLogger()

download_queue = {}
active_downloads = 0


@router.callback_query((F.data.startswith('download_')) & (F.data != 'download_playlist'))
async def handle_download_quality(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()

    user_id = callback.from_user.id
    quality_key = callback.data.replace('download_', '')

    data = await state.get_data()
    url = data.get('selected_url')
    metadata = data.get('metadata')

    if not url or not metadata:
        await callback.message.edit_text('❌ Session expired. Send the link again.')
        return

    qualities = data.get('qualities', {})
    if quality_key not in qualities:
        await callback.answer('❌ Invalid quality selected.')
        return

    chat_id = callback.message.chat.id
    download_id = str(uuid.uuid4())

    download_queue[download_id] = {
        'user_id': user_id,
        'url': url,
        'quality_key': quality_key,
        'message_id': callback.message.message_id,
        'chat_id': chat_id,
        'download_id': download_id,
        'metadata': metadata,
        'platform': data.get('platform', 'unknown')
    }

    position = len([d for d in download_queue.values() if d['chat_id'] == chat_id])
    if position > 1:
        await callback.message.edit_text(
            f'⏳ Added to queue: position #{position}\n'
            f'Estimated wait: ~{(position - 1) * 5} minutes'
        )

    import asyncio
    asyncio.create_task(process_downloads(callback.bot))


@router.callback_query(F.data == 'cancel_download')
async def cancel_download(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.edit_text('❌ Cancelled.')


@router.callback_query(F.data == 'noop')
async def noop_callback(callback: types.CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == 'download_playlist')
async def handle_download_playlist(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    logger.info('Playlist download callback received')

    data = await state.get_data()
    playlist_entries = data.get('playlist_entries', [])
    playlist_title = data.get('playlist_title', 'YouTube Music playlist')
    platform = data.get('platform', 'youtube_music')

    logger.info(f'Playlist entries in state: {len(playlist_entries)}')

    if not playlist_entries:
        await callback.message.edit_text('❌ Playlist is empty or expired. Send the link again.')
        await state.clear()
        return

    await callback.message.edit_text(
        f'🎵 Downloading playlist...\n\n📀 {playlist_title}\n🎼 Tracks: {len(playlist_entries)}\n\nEach song will be sent separately.'
    )
    await state.clear()
    asyncio.create_task(process_playlist_downloads(callback.bot, callback.message.chat.id, callback.message.message_id, playlist_title, playlist_entries, platform))


@router.callback_query(F.data == 'cancel_playlist')
async def cancel_playlist(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.edit_text('❌ Playlist download cancelled.')


async def process_downloads(bot) -> None:
    global active_downloads

    while download_queue and active_downloads < config.MAX_CONCURRENT_DOWNLOADS:
        if not download_queue:
            break

        first_key = next(iter(download_queue))
        task_data = download_queue.pop(first_key)
        active_downloads += 1

        try:
            await execute_download(bot, task_data)
        except Exception as e:
            logger.error(f'Download task error: {str(e)}')
        finally:
            active_downloads -= 1

        if download_queue:
            await asyncio.sleep(1)


async def process_playlist_downloads(bot, chat_id: int, message_id: int, playlist_title: str, playlist_entries: list[dict], platform: str) -> None:
    user_id = config.ALLOWED_USER_ID
    total_tracks = len(playlist_entries)

    for index, entry in enumerate(playlist_entries, start=1):
        track_title = entry.get('title', f'Track {index}')
        track_url = entry.get('url')

        if not track_url:
            await bot.send_message(chat_id, f'⚠️ Skipping track {index}/{total_tracks}: no URL found')
            continue

        try:
            await bot.edit_message_text(
                f'🎵 Playlist progress: {index}/{total_tracks}\n\n📀 {playlist_title}\n🎶 {track_title}',
                chat_id=chat_id,
                message_id=message_id
            )
        except Exception:
            pass

        track_download_id = f'playlist_{uuid.uuid4().hex}_{index}'
        track_download_dir = file_manager.get_unique_download_dir(user_id, track_download_id)
        track_metadata = SimpleNamespace(title=track_title, duration=entry.get('duration', 0))

        task_data = {
            'user_id': user_id,
            'url': track_url,
            'quality_key': 'audio_mp3_320',
            'message_id': message_id,
            'chat_id': chat_id,
            'download_id': track_download_id,
            'download_dir': track_download_dir,
            'metadata': track_metadata,
            'platform': platform,
        }

        try:
            await execute_download(bot, task_data)
        except Exception as e:
            logger.error(f'Playlist track download error ({index}/{total_tracks}): {str(e)}')
            await bot.send_message(chat_id, f'⚠️ Failed to download: {track_title}')


async def execute_download(bot, task_data: Dict) -> None:
    user_id = task_data['user_id']
    url = task_data['url']
    quality_key = task_data['quality_key']
    chat_id = task_data['chat_id']
    download_id = task_data['download_id']
    metadata = task_data['metadata']
    platform = task_data.get('platform', 'unknown')

    download_dir = task_data.get('download_dir') or file_manager.get_unique_download_dir(user_id, download_id)

    try:
        try:
            await bot.edit_message_text(
                '⬇️ Downloading...',
                chat_id=chat_id,
                message_id=task_data['message_id']
            )
        except:
            pass

        start_time = time.time()

        filepath = await downloader.download(url, quality_key, download_dir)
        if not filepath:
            await bot.send_message(chat_id, '❌ Download failed.\nCheck the link or try a different quality.')
            await db_logger.log_failed_download(user_id, url, 'Download failed')
            return

        file_info = await downloader.get_file_metadata(filepath)
        download_time = time.time() - start_time

        try:
            await bot.edit_message_text(
                '⬆️ Sending file...',
                chat_id=chat_id,
                message_id=task_data['message_id']
            )
        except:
            pass

        used_http_link = await send_file_to_telegram(bot, chat_id, filepath)

        await bot.send_message(
            chat_id,
            f'''✅ Done!
📹 {metadata.title}
📦 Size: {file_info.get("file_size", 0) / (1024*1024):.1f} MB
⏱ Downloaded in: {download_time:.0f} seconds'''
        )

        quality_label = quality_key.replace('_', ' ').title()
        await db_logger.log_download(
            user_id,
            url,
            metadata.title,
            platform,
            quality_label,
            file_info.get('file_size', 0),
            metadata.duration,
            download_time
        )

        if config.CLEANUP_AFTER_SEND and not used_http_link:
            await file_manager.cleanup_download_dir(download_dir)

    except Exception as e:
        logger.error(f'Execute download error: {str(e)}')
        await bot.send_message(chat_id, f'❌ Error: {str(e)[:100]}')
        await db_logger.log_failed_download(user_id, url, str(e)[:200])
        logger.warning(f'Keeping files in {download_dir} due to failed send')


async def send_file_to_telegram(bot, chat_id: int, filepath: str) -> bool:
    try:
        from pathlib import Path
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        file_path = Path(filepath)
        
        # Verify file exists before processing
        if not file_path.exists():
            raise FileNotFoundError(f'File does not exist: {filepath}')
        
        file_size = file_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        file_size_gb = file_size / (1024 * 1024 * 1024)

        logger.info(f'send_file_to_telegram called for: {file_path.name} ({file_size_mb:.1f} MB)')

        if file_size > 2 * 1024 * 1024 * 1024:
            raise Exception(f'File too large: {file_size_gb:.2f} GB (max 2 GB)')

        max_direct_upload = 50 * 1024 * 1024

        if file_size > max_direct_upload:
            logger.info(f'File {file_path.name} ({file_size_mb:.1f} MB) exceeds 50 MB, using download link')
            
            file_hash = register_file_for_download(filepath, expires_in_hours=24)
            download_url = get_download_url(file_hash, config.EXTERNAL_URL or None)
            safe_filename = html.escape(file_path.name)
            
            logger.info(f'Registered file with hash: {file_hash} -> {download_url}')
            
            # Create inline keyboard with download button
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text='⬇️ Скачать файл', url=download_url)]
                ]
            )
            
            await bot.send_message(
                chat_id,
                f'''📥 <b>Файл слишком большой для прямой отправки</b> ({file_size_mb:.1f} МБ)

📄 <code>{safe_filename}</code>

💾 Размер: {file_size_mb:.1f} МБ ({file_size_gb:.2f} ГБ)

Нажми кнопку ниже, чтобы скачать файл.''',
                parse_mode='HTML',
                reply_markup=keyboard
            )
            logger.info(f'Download link sent: {file_hash}')
            return True

        input_file = FSInputFile(filepath)
        filename = file_path.name

        if filepath.endswith('.mp3'):
            await bot.send_audio(
                chat_id,
                input_file,
                title=filename,
                request_timeout=600
            )
        else:
            await bot.send_document(
                chat_id,
                input_file,
                caption=filename,
                request_timeout=600
            )
        return False

    except Exception as e:
        logger.error(f'Failed to send file: {str(e)}')
        raise
