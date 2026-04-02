from aiogram.fsm.state import State, StatesGroup


class DownloadStates(StatesGroup):
    waiting_for_url = State()
    waiting_for_quality = State()
    downloading = State()
    processing_playlist = State()
