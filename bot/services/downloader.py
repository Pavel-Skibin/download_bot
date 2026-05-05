import asyncio
import shutil
from pathlib import Path
from typing import Optional, Dict
import yt_dlp
from bot.utils.config import config
from bot.utils.logger import BotLogger

logger = BotLogger.get_logger()


class VideoMetadata:
    def __init__(self, data: dict):
        self.title = data.get('title', 'Unknown')
        self.duration = data.get('duration', 0)
        self.uploader = data.get('uploader', 'Unknown')
        self.is_playlist = data.get('_type') == 'playlist'
        self.playlist_count = data.get('playlist_count') or len(data.get('entries', []) or [])
        self.formats = data.get('formats', [])
        self.ext = data.get('ext', 'mp4')
        self.entries = data.get('entries', []) or []

    @staticmethod
    def _to_number(value, default: float = 0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def get_available_qualities(self) -> Dict[str, Dict]:
        qualities = {}

        preferred_audio = max(
            [
                f for f in self.formats
                if f.get('vcodec') == 'none' and f.get('acodec') != 'none'
            ],
            key=lambda x: self._to_number(x.get('abr'), 0),
            default=None
        )
        preferred_audio_size = 0
        if preferred_audio:
            preferred_audio_size = self._to_number(
                preferred_audio.get('filesize')
                or preferred_audio.get('filesize_approx'),
                0
            )

        video_formats = [
            f for f in self.formats
            if f.get('vcodec') != 'none'
            and f.get('ext') in ['mp4', 'webm']
            and (f.get('height') or 0) >= 360
        ]
        audio_formats = [
            f for f in self.formats
            if f.get('vcodec') == 'none'
            and f.get('acodec') != 'none'
        ]

        height_groups = {}
        for fmt in video_formats:
            height = fmt.get('height')
            if height and height >= 360:
                if height not in height_groups:
                    height_groups[height] = fmt
                else:
                    current = height_groups[height]
                    current_size = self._to_number(
                        current.get('filesize') or current.get('filesize_approx'),
                        float('inf')
                    )
                    new_size = self._to_number(
                        fmt.get('filesize') or fmt.get('filesize_approx'),
                        float('inf')
                    )
                    if (
                        new_size < current_size
                        or (fmt.get('ext') == 'mp4' and current.get('ext') == 'webm')
                    ):
                        height_groups[height] = fmt

        quality_labels = {1080: '1080p', 720: '720p', 480: '480p', 360: '360p'}
        for height in sorted(height_groups.keys(), reverse=True):
            fmt = height_groups[height]
            label = quality_labels.get(height, f'{height}p')
            video_size = self._to_number(
                fmt.get('filesize') or fmt.get('filesize_approx'),
                0
            )
            qualities[f'video_{label}'] = {
                'format_id': fmt.get('format_id'),
                'filesize': video_size + preferred_audio_size,
                'resolution': label,
                'ext': fmt.get('ext', 'mp4')
            }

        best_audio = max(
            audio_formats,
            key=lambda x: self._to_number(x.get('abr'), 0)
        ) if audio_formats else None
        if best_audio:
            qualities['audio_mp3_320'] = {
                'format_id': best_audio.get('format_id'),
                'filesize': best_audio.get('filesize'),
                'abr': best_audio.get('abr', 320)
            }
            if len(audio_formats) > 1:
                low_audio = min(
                    audio_formats,
                    key=lambda x: self._to_number(x.get('abr'), 320)
                )
                qualities['audio_mp3_128'] = {
                    'format_id': low_audio.get('format_id'),
                    'filesize': low_audio.get('filesize'),
                    'abr': low_audio.get('abr', 128)
                }

        return qualities

    def get_playlist_entries(self) -> list[dict]:
        playlist_entries = []

        for entry in self.entries:
            if not entry:
                continue

            entry_url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url') or entry.get('id')
            if entry_url and not str(entry_url).startswith('http'):
                entry_url = f'https://music.youtube.com/watch?v={entry_url}'
            if not entry_url:
                continue

            title = entry.get('title') or entry.get('track') or entry.get('id') or 'Unknown'
            uploader = (
                entry.get('artist')
                or entry.get('uploader')
                or entry.get('channel')
                or 'Unknown'
            )
            playlist_entries.append({
                'title': title,
                'url': entry_url,
                'duration': entry.get('duration', 0),
                'uploader': uploader,
            })

        return playlist_entries


class Downloader:
    def __init__(self):
        self.max_size_bytes = config.MAX_FILE_SIZE_GB * 1024 * 1024 * 1024

    async def get_metadata(self, url: str) -> Optional[VideoMetadata]:
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'ignoreerrors': True,
                'extract_flat': 'in_playlist',
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.extract_info(url, download=False)
                )
                if not info:
                    return None
                return VideoMetadata(info)
        except Exception as e:
            logger.error(f'Failed to get metadata for {url}: {str(e)}')
            return None

    async def download(
        self,
        url: str,
        quality_key: str,
        download_path: str,
        title_hint: Optional[str] = None,
        uploader_hint: Optional[str] = None,
        progress_callback=None
    ) -> Optional[str]:
        try:
            metadata = await self.get_metadata(url)
            if not metadata:
                return None

            qualities = metadata.get_available_qualities()
            if quality_key not in qualities:
                logger.error(f'Quality {quality_key} not available')
                return None

            quality_info = qualities[quality_key]
            filesize = quality_info.get('filesize')

            if filesize and filesize > self.max_size_bytes:
                logger.warning(f'File too large: {filesize} bytes')
                return None

            download_dir = Path(download_path)
            download_dir.mkdir(parents=True, exist_ok=True)

            format_spec = quality_info['format_id']
            if 'video' in quality_key:
                format_spec = f"{format_spec}+bestaudio[ext=m4a]/bestaudio/best"

            ydl_opts = {
                'format': format_spec,
                'outtmpl': str(download_dir / '%(title)s.%(ext)s'),
                'quiet': False,
                'no_warnings': False,
                'socket_timeout': 30,
                'merge_output_format': 'mp4',
                'addmetadata': True,
                'addchapters': True,
            }

            if 'audio' in quality_key:
                ydl_opts.update({
                    'writethumbnail': True,
                    'prefer_ffmpeg': True,
                    'postprocessors': [
                        {
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        },
                        {
                            'key': 'FFmpegMetadata',
                            'add_metadata': True,
                            'add_chapters': True,
                        },
                        {
                            'key': 'FFmpegThumbnailsConvertor',  # конвертируем ДО embed
                            'format': 'jpg',
                        },
                        {
                            'key': 'EmbedThumbnail',  # embed последним
                        },
                    ],
                    'postprocessor_args': {
                        'ffmpegextractaudio': ['-id3v2_version', '3'],  # явно указываем цель
                    },
                })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: ydl.extract_info(url, download=True)
                )
                prepared_filename = ydl.prepare_filename(info)

                # yt-dlp may return the pre-processed filename (e.g. .webm)
                # while the final file after postprocessing is .mp3 or merged .mp4.
                prepared_path = Path(prepared_filename)
                if prepared_path.exists():
                    return str(prepared_path)

                # Try same stem with common final extensions first.
                candidate_exts = ['.mp3', '.mp4', '.m4a', '.webm']
                for ext in candidate_exts:
                    candidate = prepared_path.with_suffix(ext)
                    if candidate.exists():
                        return str(candidate)

                # Fallback: pick the newest media file in the download directory.
                media_files = []
                for ext in candidate_exts:
                    media_files.extend(download_dir.glob(f'*{ext}'))

                if media_files:
                    latest_file = max(media_files, key=lambda p: p.stat().st_mtime)
                    prepared_path = latest_file
                else:
                    logger.error(f'Could not resolve downloaded file path for URL: {url}')
                    return None

                # Normalize final filename to avoid random/opaque names.
                # Keep extension from the actual downloaded file.
                raw_title = (info.get('title') if isinstance(info, dict) else None) or title_hint or prepared_path.stem
                raw_uploader = (info.get('uploader') if isinstance(info, dict) else None) or uploader_hint or ''

                clean_title = ''.join(ch for ch in str(raw_title).strip() if ch not in '<>:"/\\|?*').strip('.') or 'download'
                clean_uploader = ''.join(ch for ch in str(raw_uploader).strip() if ch not in '<>:"/\\|?*').strip('.')

                if 'audio' in quality_key and clean_uploader:
                    final_name = f'{clean_uploader} - {clean_title}{prepared_path.suffix}'
                else:
                    final_name = f'{clean_title}{prepared_path.suffix}'

                final_path = prepared_path.with_name(final_name)
                if final_path != prepared_path:
                    if final_path.exists():
                        final_path.unlink()
                    prepared_path.rename(final_path)
                    prepared_path = final_path

                return str(prepared_path)

        except Exception as e:
            logger.error(f'Download failed for {url}: {str(e)}')
            return None

    async def merge_video_with_audio(
        self,
        video_path: str,
        audio_path: str,
        output_path: Optional[str] = None,
    ) -> Optional[str]:
        try:
            source_video = Path(video_path)
            source_audio = Path(audio_path)

            if not source_video.exists():
                logger.error(f'Video file not found: {video_path}')
                return None
            if not source_audio.exists():
                logger.error(f'Audio file not found: {audio_path}')
                return None

            target_path = Path(output_path) if output_path else source_video.with_name(f'{source_video.stem}_dubbed.mp4')
            if target_path.exists():
                target_path.unlink()

            ffmpeg_cmd = [
                'ffmpeg',
                '-y',
                '-i', str(source_video),
                '-i', str(source_audio),
                '-map', '0:v:0',
                '-map', '1:a:0',
                '-map_metadata', '0',
                '-map_chapters', '0',
                '-c:v', 'libx264',
                '-preset', 'veryfast',
                '-crf', '20',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-movflags', '+faststart',
                '-shortest',
                str(target_path),
            ]

            logger.info(f'Merging video and audio into {target_path.name}')
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_text = (stderr or b'').decode('utf-8', errors='ignore').strip()
                if stdout:
                    logger.debug(stdout.decode('utf-8', errors='ignore').strip())
                logger.error(f'Failed to merge video and audio: {error_text or "ffmpeg returned non-zero exit code"}')
                return None

            return str(target_path)

        except Exception as e:
            logger.error(f'Failed to merge video and audio: {str(e)}')
            return None

    async def get_file_metadata(self, filepath: str) -> dict:
        try:
            file_size = Path(filepath).stat().st_size
            return {
                'file_size': file_size,
                'filename': Path(filepath).name
            }
        except Exception as e:
            logger.error(f'Failed to get file metadata: {str(e)}')
            return {}

    async def cleanup_directory(self, directory: str) -> None:
        try:
            dir_path = Path(directory)
            if dir_path.exists():
                shutil.rmtree(dir_path)
                logger.info(f'Cleaned up directory: {directory}')
        except Exception as e:
            logger.error(f'Failed to cleanup directory {directory}: {str(e)}')
