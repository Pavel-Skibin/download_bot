from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import Dict


class DownloadKeyboards:
    @staticmethod
    def quality_selection(qualities: Dict[str, Dict]) -> InlineKeyboardMarkup:
        video_buttons = []
        audio_buttons = []

        for quality_key, quality_info in qualities.items():
            if quality_key.startswith('video_'):
                resolution = quality_info.get('resolution', quality_key)
                filesize = quality_info.get('filesize')
                size_mb = f' (~{filesize // (1024 * 1024)} MB)' if filesize else ''
                button = InlineKeyboardButton(
                    text=f'{resolution}{size_mb}',
                    callback_data=f'download_{quality_key}'
                )
                if '1080' in resolution:
                    video_buttons.insert(0, button)
                else:
                    video_buttons.append(button)
            elif quality_key.startswith('audio_'):
                filesize = quality_info.get('filesize')
                size_mb = f' (~{filesize // 1024} KB)' if filesize else ''
                abr = quality_info.get('abr', '128')
                button = InlineKeyboardButton(
                    text=f'MP3 {abr}kbps{size_mb}',
                    callback_data=f'download_{quality_key}'
                )
                audio_buttons.append(button)

        buttons = []

        if video_buttons:
            buttons.append([InlineKeyboardButton(text='📹 Video', callback_data='noop')])
            for i in range(0, len(video_buttons), 2):
                row = video_buttons[i:i+2]
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton(text='🧠 Добавить нейроперевод', callback_data='neuro_dub')
            ])

        if audio_buttons:
            if video_buttons:
                buttons.append([InlineKeyboardButton(text='🎵 Audio', callback_data='noop')])
            for btn in audio_buttons:
                buttons.append([btn])

        buttons.append([InlineKeyboardButton(text='❌ Cancel', callback_data='cancel_download')])

        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def playlist_confirmation() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text='🎵 Download All Songs', callback_data='download_playlist'),
                    InlineKeyboardButton(text='❌ Cancel', callback_data='cancel_playlist'),
                ]
            ]
        )

    @staticmethod
    def cancel_button() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text='❌ Cancel', callback_data='cancel_operation')]
            ]
        )
