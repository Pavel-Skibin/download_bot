import re
from typing import Optional, Tuple


class URLValidator:
    SUPPORTED_PLATFORMS = {
        'youtube_music': r'music\.youtube\.com',
        'youtube': r'(?:youtube\.com|youtu\.be)',
        'rutube': r'rutube\.ru',
        'vkvideo': r'(?:vk\.com|vkvideo\.ru)',
    }

    @classmethod
    def get_platform(cls, url: str) -> Optional[str]:
        for platform, pattern in cls.SUPPORTED_PLATFORMS.items():
            if re.search(pattern, url):
                return platform
        return None

    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        pattern = r'https?://[^\s]+'
        return bool(re.match(pattern, url))

    @classmethod
    def validate_and_get_platform(cls, url: str) -> Tuple[bool, Optional[str]]:
        if not cls.is_valid_url(url):
            return False, None
        platform = cls.get_platform(url)
        return bool(platform), platform
