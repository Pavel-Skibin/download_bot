import secrets
from typing import List, Dict

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from bot.handlers import DownloadStates
from bot.keyboards.movie_kb import MovieKeyboards
from bot.services.http_server import (
    register_stream_torrent,
    get_stream_url,
    get_stream_torrent_url,
)
from bot.services.rutracker_service import RuTrackerService
from bot.utils.config import config
from bot.utils.logger import BotLogger

logger = BotLogger.get_logger()
router = Router()

rutracker_service = RuTrackerService()


def _is_allowed(user_id: int) -> bool:
    return user_id == config.ALLOWED_USER_ID


def _serialize_releases(releases) -> List[Dict]:
    return [item.to_dict() for item in releases]


def _collect_filters(releases: List[Dict]) -> tuple[List[str], List[str]]:
    quality = sorted({item['quality'] for item in releases})
    voice = sorted({item.get('voice_detail') or item['voice'] for item in releases})
    return ['all'] + quality, ['all'] + voice


def _apply_filters(releases: List[Dict], selected_quality: str, selected_voice: str) -> List[Dict]:
    filtered = releases
    if selected_quality != 'all':
        filtered = [x for x in filtered if x['quality'] == selected_quality]
    if selected_voice != 'all':
        filtered = [x for x in filtered if (x.get('voice_detail') or x['voice']) == selected_voice]
    return filtered


def _render_release_lines(filtered: List[Dict], page: int, per_page: int, detailed: bool) -> List[str]:
    start = page * per_page
    end = start + per_page
    block = filtered[start:end]

    lines = []
    for idx, item in enumerate(block, start=1 + start):
        voice_info = item.get('voice_detail') or item.get('voice') or 'Не указано'
        if detailed:
            lines.append(
                f"{idx}. [{item['quality']}] [{voice_info}] 🌱{item['seeds']} 📥{item['leeches']}\n{item['title']}"
            )
        else:
            title_short = item['title'] if len(item['title']) <= 70 else f"{item['title'][:67]}..."
            lines.append(
                f"{idx}. {item['quality']} | {voice_info} | 🌱{item['seeds']}\n{title_short}"
            )
    return lines


async def _safe_edit_text(message: types.Message, text: str, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        if 'message is not modified' in str(e).lower():
            return
        raise


@router.message(Command('movie', 'zona'))
async def cmd_movie(message: types.Message, state: FSMContext) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer('🚫 Access denied.')
        return

    query = (message.text or '').split(maxsplit=1)
    if len(query) > 1 and query[1].strip():
        await _search_and_show(message, state, query[1].strip())
        return

    await state.set_state(DownloadStates.waiting_for_movie_query)
    await message.answer('🎬 Введите название фильма или сериала для поиска на RuTracker:')


@router.message(DownloadStates.waiting_for_movie_query)
async def movie_query_input(message: types.Message, state: FSMContext) -> None:
    if not _is_allowed(message.from_user.id):
        await message.answer('🚫 Access denied.')
        return

    query = (message.text or '').strip()
    if len(query) < 2:
        await message.answer('❌ Слишком короткий запрос. Введите хотя бы 2 символа.')
        return

    await _search_and_show(message, state, query)


async def _search_and_show(message: types.Message, state: FSMContext, query: str) -> None:
    status = await message.answer('🔎 Ищу релизы на RuTracker...')
    try:
        releases = await rutracker_service.search_movie_releases(query, max_pages=3)
    except Exception as e:
        logger.error(f'RuTracker search failed: {str(e)}')
        await status.edit_text(f'❌ Ошибка поиска: {str(e)[:120]}')
        return

    if not releases:
        await status.edit_text('❌ Ничего не найдено. Попробуйте другой запрос.')
        return

    release_dicts = _serialize_releases(releases)
    qualities, voices = _collect_filters(release_dicts)

    await state.update_data(
        movie_query=query,
        movie_releases=release_dicts,
        movie_qualities=qualities,
        movie_voices=voices,
        movie_selected_quality='all',
        movie_selected_voice='all',
        movie_page=0,
        movie_detailed=False,
    )
    await state.set_state(DownloadStates.waiting_for_movie_quality)

    await status.edit_text(
        f'✅ Найдено релизов: {len(release_dicts)}\n\nВыберите качество:',
        reply_markup=MovieKeyboards.quality_selection(qualities, 'all')
    )


@router.callback_query(F.data == 'movie_cancel')
async def movie_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await _safe_edit_text(callback.message, '❌ Отменено.')


@router.callback_query(F.data.startswith('movie_q:'))
async def movie_select_quality(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    selected = callback.data.split(':', 1)[1]
    data = await state.get_data()
    qualities = data.get('movie_qualities', ['all'])
    if selected not in qualities:
        return

    if selected == data.get('movie_selected_quality', 'all'):
        return

    await state.update_data(movie_selected_quality=selected, movie_page=0)
    await _safe_edit_text(
        callback.message,
        'Выберите качество:',
        reply_markup=MovieKeyboards.quality_selection(qualities, selected)
    )


@router.callback_query(F.data == 'movie_next_voice')
@router.callback_query(F.data == 'movie_back_quality')
async def movie_switch_step(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    if callback.data == 'movie_back_quality':
        await _safe_edit_text(
            callback.message,
            'Выберите качество:',
            reply_markup=MovieKeyboards.quality_selection(data.get('movie_qualities', ['all']), data.get('movie_selected_quality', 'all'))
        )
        return

    await _safe_edit_text(
        callback.message,
        'Выберите озвучку (детальная):\nМожно выбрать конкретный релиз-группу/вариант перевода.',
        reply_markup=MovieKeyboards.voice_selection(
            data.get('movie_voices', ['all']),
            data.get('movie_selected_quality', 'all'),
            data.get('movie_selected_voice', 'all')
        )
    )


@router.callback_query(F.data.startswith('movie_v:'))
async def movie_select_voice(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    raw_idx = callback.data.split(':', 1)[1]
    data = await state.get_data()
    voices = data.get('movie_voices', ['all'])
    if not raw_idx.isdigit():
        return
    idx = int(raw_idx)
    if idx < 0 or idx >= len(voices):
        return
    selected = voices[idx]

    if selected == data.get('movie_selected_voice', 'all'):
        return

    await state.update_data(movie_selected_voice=selected, movie_page=0)
    await _safe_edit_text(
        callback.message,
        'Выберите озвучку (детальная):\nМожно выбрать конкретный релиз-группу/вариант перевода.',
        reply_markup=MovieKeyboards.voice_selection(
            voices,
            data.get('movie_selected_quality', 'all'),
            selected
        )
    )


@router.callback_query(F.data == 'movie_show_releases')
@router.callback_query(F.data.startswith('movie_page:'))
async def movie_show_releases(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    releases = data.get('movie_releases', [])
    if not releases:
        await _safe_edit_text(callback.message, '❌ Релизы не найдены, выполните поиск снова.')
        return

    page = data.get('movie_page', 0)
    if callback.data.startswith('movie_page:'):
        page = int(callback.data.split(':', 1)[1])
        await state.update_data(movie_page=page)

    selected_quality = data.get('movie_selected_quality', 'all')
    selected_voice = data.get('movie_selected_voice', 'all')
    detailed = bool(data.get('movie_detailed', False))
    filtered = _apply_filters(releases, selected_quality, selected_voice)

    if not filtered:
        await _safe_edit_text(callback.message, '❌ Нет релизов для выбранных фильтров. Измените качество/озвучку.')
        return

    per_page = 6
    max_page = max((len(filtered) - 1) // per_page, 0)
    if page > max_page:
        page = max_page
        await state.update_data(movie_page=page)

    text_lines = [
        f"🔎 Запрос: {data.get('movie_query', '')}",
        f"🎚 Качество: {selected_quality}",
        f"🎙 Озвучка: {selected_voice}",
        f"📦 Подходящих релизов: {len(filtered)}",
        f"🧾 Режим: {'максимум деталей' if detailed else 'кратко'}",
        '',
        'Выберите релиз:'
    ]

    lines_preview = _render_release_lines(filtered, page, per_page, detailed)

    text_lines.append('')
    text_lines.extend(lines_preview)

    await _safe_edit_text(
        callback.message,
        '\n'.join(text_lines),
        reply_markup=MovieKeyboards.release_selection(filtered, page=page, detailed=detailed)
    )


@router.callback_query(F.data == 'movie_toggle_details')
async def movie_toggle_details(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    detailed = bool(data.get('movie_detailed', False))
    await state.update_data(movie_detailed=not detailed)
    await movie_show_releases(callback, state)


@router.callback_query(F.data.startswith('movie_pick:'))
async def movie_pick_release(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    topic_id = int(callback.data.split(':', 1)[1])
    data = await state.get_data()
    releases = data.get('movie_releases', [])
    selected = next((x for x in releases if x['topic_id'] == topic_id), None)
    if not selected:
        await _safe_edit_text(callback.message, '❌ Релиз не найден, повторите поиск.')
        return

    await state.update_data(movie_selected_topic_id=topic_id)
    text = (
        f"🎬 {selected['title']}\n"
        f"🎚 Качество: {selected['quality']}\n"
        f"🎙 Озвучка: {selected.get('voice_detail') or selected['voice']}\n"
        f"💽 Размер: {selected['size_value']} {selected['size_unit']}\n"
        f"🌱 Сиды: {selected['seeds']} | 📥 Личи: {selected['leeches']}\n\n"
        f"Выберите действие:"
    )
    await _safe_edit_text(callback.message, text, reply_markup=MovieKeyboards.release_actions(topic_id))


@router.callback_query(F.data.startswith('movie_dl:'))
async def movie_download_torrent(callback: types.CallbackQuery) -> None:
    await callback.answer('Скачиваю .torrent...')
    topic_id = int(callback.data.split(':', 1)[1])

    try:
        torrent_bytes = await rutracker_service.get_torrent_bytes(topic_id)
    except Exception as e:
        logger.error(f'Failed to load torrent file: {str(e)}')
        await callback.message.answer(f'❌ Не удалось скачать .torrent: {str(e)[:120]}')
        return

    file_name = f'rutracker_{topic_id}.torrent'
    document = BufferedInputFile(torrent_bytes, filename=file_name)
    await callback.message.answer_document(
        document=document,
        caption='✅ .torrent файл готов. Откройте в торрент-клиенте.'
    )


@router.callback_query(F.data.startswith('movie_stream:'))
async def movie_stream_release(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer('Готовлю стрим...')
    topic_id = int(callback.data.split(':', 1)[1])
    data = await state.get_data()
    releases = data.get('movie_releases', [])
    selected = next((x for x in releases if x['topic_id'] == topic_id), None)
    title = selected['title'] if selected else f'Release {topic_id}'

    try:
        torrent_bytes = await rutracker_service.get_torrent_bytes(topic_id)
        stream_token = register_stream_torrent(torrent_bytes, title=title, expires_in_hours=24)
        stream_url = get_stream_url(stream_token, config.EXTERNAL_URL or None)
        torrent_url = get_stream_torrent_url(stream_token, config.EXTERNAL_URL or None)
    except Exception as e:
        logger.error(f'Failed to prepare stream: {str(e)}')
        await callback.message.answer(f'❌ Не удалось подготовить стрим: {str(e)[:120]}')
        return

    buttons = [
        [InlineKeyboardButton(text='▶️ Открыть стрим', url=stream_url)],
    ]
    buttons.append([InlineKeyboardButton(text='📄 Открыть torrent-ссылку', url=torrent_url)])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = (
        '✅ Подготовлено.\n'
        'Стрим открывается через прокси сервера бота для стабильного воспроизведения в браузере.'
    )

    await callback.message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == 'movie_new_search')
async def movie_new_search(callback: types.CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(DownloadStates.waiting_for_movie_query)
    await _safe_edit_text(callback.message, '🎬 Введите новое название фильма или сериала:')
