from aiogram.fsm.state import State, StatesGroup


class DownloadStates(StatesGroup):
    waiting_for_url = State()
    waiting_for_quality = State()
    waiting_for_neuro_audio = State()
    downloading = State()
    processing_playlist = State()
    waiting_for_movie_query = State()
    waiting_for_movie_quality = State()
