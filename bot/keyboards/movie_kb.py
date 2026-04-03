from typing import List, Dict

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


class MovieKeyboards:
    @staticmethod
    def quality_selection(qualities: List[str], selected: str = 'all') -> InlineKeyboardMarkup:
        buttons = []
        for quality in qualities:
            prefix = '✅ ' if quality == selected else ''
            buttons.append([
                InlineKeyboardButton(
                    text=f'{prefix}{quality}',
                    callback_data=f'movie_q:{quality}'
                )
            ])

        buttons.append([
            InlineKeyboardButton(text='➡️ Дальше: озвучка', callback_data='movie_next_voice')
        ])
        buttons.append([
            InlineKeyboardButton(text='❌ Отмена', callback_data='movie_cancel')
        ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def voice_selection(voices: List[str], selected_quality: str, selected_voice: str = 'all') -> InlineKeyboardMarkup:
        buttons = []
        for idx, voice in enumerate(voices):
            prefix = '✅ ' if voice == selected_voice else ''
            caption = voice if len(voice) <= 48 else f'{voice[:45]}...'
            buttons.append([
                InlineKeyboardButton(
                    text=f'{prefix}{caption}',
                    callback_data=f'movie_v:{idx}'
                )
            ])

        buttons.append([
            InlineKeyboardButton(text='📦 Показать релизы', callback_data='movie_show_releases')
        ])
        buttons.append([
            InlineKeyboardButton(text='⬅️ Назад к качеству', callback_data='movie_back_quality')
        ])
        buttons.append([
            InlineKeyboardButton(text='❌ Отмена', callback_data='movie_cancel')
        ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def release_selection(releases: List[Dict], page: int = 0, per_page: int = 6, detailed: bool = False) -> InlineKeyboardMarkup:
        start = page * per_page
        end = start + per_page
        current = releases[start:end]

        buttons = []
        for item in current:
            voice = item.get('voice_detail') or item.get('voice') or 'Не указано'
            short_voice = voice if len(voice) <= 28 else f"{voice[:25]}..."
            label = f"🎬 {item['quality']} | {short_voice} | 🌱{item['seeds']}"
            buttons.append([
                InlineKeyboardButton(text=label[:64], callback_data=f"movie_pick:{item['topic_id']}")
            ])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton(text='⬅️', callback_data=f'movie_page:{page - 1}'))
        if end < len(releases):
            nav_row.append(InlineKeyboardButton(text='➡️', callback_data=f'movie_page:{page + 1}'))
        if nav_row:
            buttons.append(nav_row)

        mode_title = '🧾 Режим: максимум деталей' if not detailed else '📝 Режим: кратко'
        buttons.append([
            InlineKeyboardButton(text=mode_title, callback_data='movie_toggle_details')
        ])

        buttons.append([
            InlineKeyboardButton(text='🔎 Новый поиск', callback_data='movie_new_search')
        ])
        buttons.append([
            InlineKeyboardButton(text='❌ Отмена', callback_data='movie_cancel')
        ])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def release_actions(topic_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='⬇️ Скачать .torrent', callback_data=f'movie_dl:{topic_id}')],
                [InlineKeyboardButton(text='▶️ Стрим в браузере', callback_data=f'movie_stream:{topic_id}')],
                [InlineKeyboardButton(text='⬅️ К списку релизов', callback_data='movie_show_releases')],
            ]
        )
