import asyncio
import re
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from urllib.parse import parse_qs, urlparse

from py_rutracker import RuTrackerClient

from bot.utils.config import config
from bot.utils.logger import BotLogger

logger = BotLogger.get_logger()


@dataclass
class TorrentRelease:
    topic_id: int
    title: str
    size_value: float
    size_unit: str
    seeds: int
    leeches: int
    added: str
    quality: str
    voice: str
    voice_detail: str
    category: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RuTrackerService:
    QUALITY_PATTERNS = [
        (r'2160p|4k|uhd', '2160p/UHD'),
        (r'1080p|full\s*hd', '1080p'),
        (r'720p|hd', '720p'),
        (r'web[\s.-]?dl', 'WEB-DL'),
        (r'blu[\s.-]?ray|bdrip|bdremux', 'BluRay/BDRip'),
        (r'dvdrip', 'DVDRip'),
        (r'hdrip', 'HDRip'),
        (r'tscr|camrip|cam', 'CAM/TS'),
    ]

    VOICE_PATTERNS = [
        (r'дубляж|дублированный|dub', 'Дубляж'),
        (r'многоголос|mvo', 'Многоголосый'),
        (r'двухголос|dvo', 'Двухголосый'),
        (r'одноголос|avo', 'Одноголосый'),
        (r'авторск', 'Авторский'),
        (r'оригинал|original', 'Оригинал'),
        (r'субтитр|sub', 'Субтитры'),
    ]

    VOICE_DETAIL_PATTERN = re.compile(
        r'(дубляж[^\]\)|\|]*|дублирован[^\]\)|\|]*|многоголос[^\]\)|\|]*|двухголос[^\]\)|\|]*|одноголос[^\]\)|\|]*|'
        r'любительск[^\]\)|\|]*|профессиональн[^\]\)|\|]*|авторск[^\]\)|\|]*|lostfilm[^\]\)|\|]*|'
        r'newstudio[^\]\)|\|]*|jaskier[^\]\)|\|]*|кубик[^\]\)|\|]*|alexfilm[^\]\)|\|]*|'
        r'анидуб[^\]\)|\|]*|tvshows[^\]\)|\|]*|hdrezka[^\]\)|\|]*)',
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_topic_id(download_url: str) -> int:
        parsed = urlparse(download_url)
        query = parse_qs(parsed.query)
        topic_id = query.get('t', ['0'])[0]
        return int(topic_id)

    @classmethod
    def _detect_quality(cls, title: str) -> str:
        lower = title.lower()
        for pattern, label in cls.QUALITY_PATTERNS:
            if re.search(pattern, lower):
                return label
        return 'Не указано'

    @classmethod
    def _detect_voice(cls, title: str) -> str:
        lower = title.lower()
        for pattern, label in cls.VOICE_PATTERNS:
            if re.search(pattern, lower):
                return label
        return 'Не указано'

    @classmethod
    def _extract_voice_detail(cls, title: str, fallback: str) -> str:
        matches = cls.VOICE_DETAIL_PATTERN.findall(title)
        if not matches:
            return fallback

        cleaned = []
        for raw in matches:
            candidate = re.sub(r'\s+', ' ', raw).strip(' []()|,-')
            if len(candidate) < 4:
                continue
            if candidate.lower() not in {item.lower() for item in cleaned}:
                cleaned.append(candidate)

        if not cleaned:
            return fallback
        return ' | '.join(cleaned[:3])

    @staticmethod
    def _is_movie_category(category: str) -> bool:
        text = (category or '').lower()
        keywords = ['фильм', 'кино', 'movie', 'мульт', 'аниме', 'serial', 'сериал']
        return any(word in text for word in keywords)

    @staticmethod
    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    @staticmethod
    def _build_proxies(proxy: str | None) -> dict | None:
        if not proxy:
            return None
        return {
            'http': proxy,
            'https': proxy,
        }

    def _search_sync(self, query: str, max_pages: int) -> list:
        proxies = self._build_proxies(config.RUTRACKER_PROXY or None)
        with RuTrackerClient(config.RUTRACKER_LOGIN, config.RUTRACKER_PASSWORD, proxies=proxies) as client:
            results = client.search_all_pages(query)
        if max_pages >= 10:
            return results
        return results[: max_pages * 50]

    def _download_torrent_sync(self, topic_id: int) -> bytes:
        proxies = self._build_proxies(config.RUTRACKER_PROXY or None)
        with RuTrackerClient(config.RUTRACKER_LOGIN, config.RUTRACKER_PASSWORD, proxies=proxies) as client:
            return client.download(topic_id)

    async def search_movie_releases(self, query: str, max_pages: int = 3) -> List[TorrentRelease]:
        if not config.RUTRACKER_LOGIN or not config.RUTRACKER_PASSWORD:
            raise RuntimeError('RuTracker credentials are not configured in .env')

        results = await asyncio.to_thread(self._search_sync, query, max_pages)

        releases: List[TorrentRelease] = []
        for result in results:
            title = getattr(result, 'title', '') or ''
            category = getattr(result, 'category', '') or ''
            if not title:
                continue
            if not self._is_movie_category(category):
                continue

            topic_id = self._as_int(getattr(result, 'topic_id', 0))
            if topic_id <= 0:
                download_url = getattr(result, 'download_url', '') or ''
                topic_id = self._extract_topic_id(download_url) if download_url else 0
            if topic_id <= 0:
                continue

            releases.append(
                TorrentRelease(
                    topic_id=topic_id,
                    title=title,
                    size_value=float(getattr(result, 'size', 0) or 0),
                    size_unit=str(getattr(result, 'unit', 'GB') or 'GB'),
                    seeds=self._as_int(getattr(result, 'seedmed', 0)),
                    leeches=self._as_int(getattr(result, 'leechmed', 0)),
                    added=str(getattr(result, 'added', '')),
                    quality=self._detect_quality(title),
                    voice=self._detect_voice(title),
                    voice_detail=self._extract_voice_detail(title, self._detect_voice(title)),
                    category=category,
                )
            )

        releases.sort(key=lambda x: (x.seeds, x.size_value), reverse=True)
        return releases

    async def get_torrent_bytes(self, topic_id: int) -> bytes:
        if not config.RUTRACKER_LOGIN or not config.RUTRACKER_PASSWORD:
            raise RuntimeError('RuTracker credentials are not configured in .env')

        return await asyncio.to_thread(self._download_torrent_sync, topic_id)
