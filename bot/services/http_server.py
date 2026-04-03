import asyncio
import json
import hashlib
import mimetypes
import html
import socket
import contextlib
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timedelta
from urllib.parse import quote
from aiohttp import web
import aiohttp

from aiotorrent import Torrent
from http.client import HTTPConnection, HTTPSConnection
import urllib.parse
import http.cookiejar

from bot.utils.config import config
from bot.utils.logger import BotLogger

logger = BotLogger.get_logger()

# Global storage for RuTracker session cookies
_rutracker_cookies = ""


async def _get_rutracker_session() -> str:
    """Get RuTracker session cookies using provided credentials"""
    global _rutracker_cookies
    
    if _rutracker_cookies:
        return _rutracker_cookies
    
    if not (config.RUTRACKER_LOGIN and config.RUTRACKER_PASSWORD):
        logger.warning("RuTracker credentials not configured, tracker requests may fail with 403")
        return ""
    
    try:
        login_data = urllib.parse.urlencode({
            'login_username': config.RUTRACKER_LOGIN,
            'login_password': config.RUTRACKER_PASSWORD,
            'login': 'submit',
        }).encode('utf-8')
        
        loop = asyncio.get_running_loop()
        
        def fetch_session():
            try:
                conn = HTTPSConnection('rutracker.org')
                conn.request('POST', '/forum/login.php', login_data, {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'
                })
                response = conn.getresponse()
                
                # Collect all cookies from Set-Cookie headers
                cookies = []
                for header, value in response.getheaders():
                    if header.lower() == 'set-cookie':
                        # Extract just the cookie part (before semicolon and expiry)
                        cookie_part = value.split(';')[0]
                        cookies.append(cookie_part)
                
                conn.close()
                cookie_str = '; '.join(cookies) if cookies else ""
                logger.info(f"RuTracker cookies raw: {cookie_str[:100]}")
                return cookie_str
            except Exception as e:
                logger.error(f"Failed to fetch RuTracker session: {e}")
                return ""
        
        _rutracker_cookies = await loop.run_in_executor(None, fetch_session)
        if _rutracker_cookies:
            logger.info(f"RuTracker session acquired with {len(_rutracker_cookies.split(';'))} cookies")
        else:
            logger.warning("No cookies received from RuTracker login")
        return _rutracker_cookies
    except Exception as e:
        logger.error(f"Failed to get RuTracker session: {e}")
        return ""


def _patch_aiotorrent_useragent():
    """Patch aiotorrent HTTPTracker to include User-Agent and headers for RuTracker compatibility"""
    try:
        from aiotorrent.core.trackers import HTTPTracker
        from aiotorrent.core.bencode_utils import bencode_util
        from aiotorrent.core.util import chunk
        from ipaddress import IPv4Address
        from struct import unpack
        
        async def patched_get_peers(self):
            self.peers = []
            
            announce_req_raw = self.gen_announce_http()
            announce_req = HTTPTracker.serialize_announce("url", announce_req_raw)
            
            def connect_to_tracker(payload: str) -> dict:
                # Use proxy if configured
                proxy = config.RUTRACKER_PROXY if config.RUTRACKER_PROXY else None
                
                if proxy:
                    # TODO: Implement proxy support via ProxyManager or similar
                    logger.debug(f"Proxy configured but not yet implemented: {proxy}")
                
                http_conn_factory = HTTPSConnection if self.scheme == "https" else HTTPConnection
                connection = http_conn_factory(host=self.hostname, port=self.port)
                final_query = f"{self.path}?{payload}"
                
                # Add comprehensive headers for private trackers like RuTracker
                headers = {
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Referer': 'https://rutracker.org/',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'DNT': '1',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                }
                
                # Add RuTracker session cookies if available
                if _rutracker_cookies:
                    headers['Cookie'] = _rutracker_cookies
                    logger.debug(f"Tracker request to {self.hostname} with cookies: {_rutracker_cookies[:50]}...")
                else:
                    logger.debug(f"Tracker request to {self.hostname} WITHOUT cookies")
                
                try:
                    connection.request("GET", final_query, headers=headers)
                    response = connection.getresponse()
                    response_data = response.read()
                    
                    if response.status == 200:
                        self.active = True
                        self.announce_response = bencode_util.bdecode(response_data)
                        peer_list = self.announce_response.get('peers', b'')
                        for ip_addr in chunk(peer_list, 6):
                            if ip_addr[0] is not None:
                                try:
                                    ip, port = unpack('>IH', bytes(ip_addr))
                                    ip = IPv4Address(ip).compressed
                                    self.peers.append((ip, port))
                                except Exception:
                                    pass
                        logger.info(f"Tracker {self} returned {len(self.peers)} peers")
                    else:
                        logger.warning(f"Error fetching peers from {self}: Error Code: {response.status} - {response.reason}")
                        if response.status == 403:
                            logger.warning(f"  403 Forbidden from tracker. Session cookies may not be valid for this tracker host.")
                            logger.warning(f"  Recommend: Check if RUTRACKER_PROXY is needed or passkey in announce URL.")
                        return []
                except Exception as e:
                    logger.error(f"Error connecting to {self}: {e}")
                    return []
            
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, connect_to_tracker, announce_req)
                return result
            except Exception as e:
                logger.error(f"Error occurred while connecting to {self}: {e}")
                return []
        
        HTTPTracker.get_peers = patched_get_peers
        logger.info("aiotorrent HTTPTracker patched with auth headers support")
    except Exception as e:
        logger.warning(f"Failed to patch aiotorrent HTTPTracker: {e}")


# Apply patch at module load time
_patch_aiotorrent_useragent()

# Will be called during init_http_server to fetch RuTracker session


class FileRegistry:
    def __init__(self):
        self.files: Dict[str, dict] = {}
        self.registry_path = Path(config.DOWNLOADS_PATH).resolve().parent / 'http_registry.json'
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.load_registry()

    def _serialize(self, info: dict) -> dict:
        return {
            'path': info['path'],
            'name': info['name'],
            'size': info['size'],
            'created': info['created'].isoformat(),
            'expires': info['expires'].isoformat(),
        }

    def _deserialize(self, info: dict) -> dict:
        return {
            'path': info['path'],
            'name': info['name'],
            'size': info['size'],
            'created': datetime.fromisoformat(info['created']),
            'expires': datetime.fromisoformat(info['expires']),
        }

    def save_registry(self) -> None:
        try:
            payload = {file_hash: self._serialize(info) for file_hash, info in self.files.items()}
            self.registry_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            logger.error(f'Failed to save registry: {str(e)}')

    def load_registry(self) -> None:
        if not self.registry_path.exists():
            return

        try:
            raw_data = json.loads(self.registry_path.read_text(encoding='utf-8'))
            now = datetime.now()
            for file_hash, info in raw_data.items():
                try:
                    restored = self._deserialize(info)
                    if restored['expires'] <= now:
                        continue
                    if not Path(restored['path']).exists():
                        continue
                    self.files[file_hash] = restored
                except Exception:
                    continue
            if self.files:
                logger.info(f'Loaded {len(self.files)} files from registry')
        except Exception as e:
            logger.error(f'Failed to load registry: {str(e)}')

    def register(self, filepath: str, expires_in_hours: int = 24) -> str:
        file_path = Path(filepath)
        if not file_path.exists():
            raise FileNotFoundError(f'File not found: {filepath}')

        file_hash = hashlib.md5(f'{filepath}{datetime.now().isoformat()}'.encode()).hexdigest()[:8]
        
        # Try to make path relative to DOWNLOADS_PATH for Docker compatibility
        abs_path = file_path.resolve()
        downloads_base = Path(config.DOWNLOADS_PATH).resolve()
        
        # Try to get relative path, fall back to absolute
        rel_path_str = None
        try:
            rel_path_str = str(abs_path.relative_to(downloads_base))
            storage_path = str(downloads_base / rel_path_str)
        except ValueError:
            # Path is not under DOWNLOADS_PATH, use absolute
            storage_path = str(abs_path)
            rel_path_str = None
        
        self.files[file_hash] = {
            'path': storage_path,
            'name': file_path.name,
            'size': file_path.stat().st_size,
            'created': datetime.now(),
            'expires': datetime.now() + timedelta(hours=expires_in_hours)
        }
        self.save_registry()
        
        file_size = self.files[file_hash]['size']
        logger.info(f'Registered file: {file_hash} -> {file_path.name} ({file_size/(1024*1024):.1f}MB)')
        logger.info(f'  Absolute path: {abs_path}')
        logger.info(f'  Storage path: {storage_path}')
        logger.info(f'  File exists: {file_path.exists()}')
        logger.info(f'  Expires in: {expires_in_hours} hours')
        return file_hash

    def get(self, file_hash: str) -> Optional[dict]:
        if file_hash not in self.files:
            return None
        
        info = self.files[file_hash]
        if datetime.now() > info['expires']:
            self.cleanup_file(file_hash)
            return None
        
        return info

    def cleanup_file(self, file_hash: str) -> None:
        if file_hash not in self.files:
            return
        
        info = self.files[file_hash]
        try:
            file_path = Path(info['path'])
            if file_path.exists():
                file_path.unlink()
                filename = info['name']
                logger.info(f'Cleaned up expired file: {filename}')
        except Exception as e:
            logger.error(f'Failed to cleanup file {file_hash}: {str(e)}')
        
        del self.files[file_hash]
        self.save_registry()

    def cleanup_expired(self) -> None:
        expired = [
            fh for fh, info in self.files.items()
            if datetime.now() > info['expires']
        ]
        for fh in expired:
            self.cleanup_file(fh)
        
        if expired:
            logger.info(f'Cleaned up {len(expired)} expired files')


class HTTPServer:
    def __init__(self, port: int = 8080):
        self.port = port
        self.app = web.Application()
        self.registry = FileRegistry()
        self.stream_torrents: Dict[str, dict] = {}
        self.setup_routes()

    def setup_routes(self) -> None:
        self.app.router.add_get('/download/{file_hash}', self.download_handler)
        self.app.router.add_get('/health', self.health_handler)
        self.app.router.add_get('/stream/{stream_token}', self.stream_page_handler)
        self.app.router.add_get('/stream-torrent/{stream_token}', self.stream_torrent_handler)
        self.app.router.add_get('/stream-media/{stream_token}', self.stream_media_handler)

    async def health_handler(self, request: web.Request) -> web.Response:
        # Log current registry state for debugging
        if self.registry.files:
            logger.debug(f'Health check - Files in registry: {len(self.registry.files)}')
            for fh, info in list(self.registry.files.items())[:3]:  # Log first 3
                path = info['path']
                logger.debug(f'  {fh}: {info["name"]} ({info["size"]/(1024*1024):.1f}MB) at {path}')
        return web.json_response({'status': 'ok', 'files': len(self.registry.files), 'streams': len(self.stream_torrents)})

    def register_stream_torrent(self, torrent_bytes: bytes, title: str, expires_in_hours: int = 24) -> str:
        stream_token = hashlib.md5(f'{title}{datetime.now().isoformat()}'.encode()).hexdigest()[:12]
        torrent_store = Path(config.DOWNLOADS_PATH).resolve().parent / 'stream_torrents'
        torrent_store.mkdir(parents=True, exist_ok=True)
        torrent_path = torrent_store / f'{stream_token}.torrent'
        torrent_path.write_bytes(torrent_bytes)

        self.stream_torrents[stream_token] = {
            'title': title,
            'torrent_bytes': torrent_bytes,
            'torrent_path': str(torrent_path),
            'created': datetime.now(),
            'expires': datetime.now() + timedelta(hours=expires_in_hours),
            'aiotorrent_instance': None,
            'aiotorrent_port': None,
            'aiotorrent_task': None,
        }
        return stream_token

    def get_stream_url(self, stream_token: str, external_url: Optional[str] = None) -> str:
        base_url = external_url or config.EXTERNAL_URL or ''
        if base_url:
            return f'{base_url.rstrip("/")}/stream/{stream_token}'
        return f'http://127.0.0.1:{self.port}/stream/{stream_token}'

    def get_stream_torrent_url(self, stream_token: str, external_url: Optional[str] = None) -> str:
        base_url = external_url or config.EXTERNAL_URL or ''
        if base_url:
            return f'{base_url.rstrip("/")}/stream-torrent/{stream_token}'
        return f'http://127.0.0.1:{self.port}/stream-torrent/{stream_token}'

    def get_aiotorrent_stream_url(self, stream_token: str, external_url: Optional[str] = None) -> Optional[str]:
        template = config.AIOTORRENT_STREAM_URL_TEMPLATE
        if not template:
            return None

        info = self._get_stream_info(stream_token)
        if not info:
            return None

        torrent_url = self.get_stream_torrent_url(stream_token, external_url)
        title = str(info.get('title', ''))

        return (
            template
            .replace('{torrent_url}', quote(torrent_url, safe=''))
            .replace('{title}', quote(title, safe=''))
            .replace('{stream_token}', stream_token)
        )

    def _get_stream_info(self, stream_token: str) -> Optional[dict]:
        info = self.stream_torrents.get(stream_token)
        if not info:
            return None
        if datetime.now() > info['expires']:
            task = info.get('aiotorrent_task')
            if task and not task.done():
                task.cancel()
            info['aiotorrent_instance'] = None
            torrent_path = info.get('torrent_path')
            if torrent_path:
                try:
                    p = Path(torrent_path)
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            del self.stream_torrents[stream_token]
            return None
        return info

    @staticmethod
    def _get_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('127.0.0.1', 0))
            return int(s.getsockname()[1])

    async def _start_aiotorrent_backend(self, stream_token: str) -> None:
        info = self._get_stream_info(stream_token)
        if not info:
            raise RuntimeError('Stream not found or expired')

        existing_port = info.get('aiotorrent_port')
        existing_task = info.get('aiotorrent_task')
        if existing_port and existing_task and not existing_task.done():
            return

        torrent_path = info.get('torrent_path')
        if not torrent_path or not Path(torrent_path).exists():
            raise RuntimeError('Torrent file is missing for stream backend')

        port = self._get_free_port()

        async def run_backend() -> None:
            torrent = Torrent(torrent_path)
            info['aiotorrent_instance'] = torrent
            logger.info(f'aiotorrent init: loading torrent {torrent_path}')
            await torrent.init(dht_enabled=True)

            logger.info(f'aiotorrent init: loaded torrent_info with {len(torrent.torrent_info.get("trackers", []))} trackers')
            if torrent.torrent_info.get('announce'):
                logger.info(f'  Announce URL: {torrent.torrent_info.get("announce")[:80]}...')
            for idx, tracker in enumerate(torrent.torrent_info.get('trackers', [])):
                logger.debug(f'  Tracker {idx}: {tracker}')
            logger.info(f'aiotorrent init: {len(torrent.peers)} total peer objects created')
            active = [p for p in torrent.peers if p.has_handshaked]
            logger.info(f'aiotorrent init: {len(active)} peers active after handshake')
            
            if not active:
                logger.warning(f'aiotorrent init: No handshaked peers, will attempt fallback with DHT/local seeds')

            files = list(torrent.files)
            if not files:
                raise RuntimeError('No files found in torrent')

            playable_ext = ('.mkv', '.avi', '.mov', '.mp4', '.webm', '.m4v', '.ogv', '.ts', '.m2ts')
            playable = [f for f in files if str(getattr(f, 'name', '')).lower().endswith(playable_ext)]
            if playable:
                selected = max(playable, key=lambda f: int(getattr(f, 'size', 0) or 0))
            else:
                selected = max(files, key=lambda f: int(getattr(f, 'size', 0) or 0))

            logger.info(f'Starting aiotorrent backend for {stream_token} on 127.0.0.1:{port}, file: {getattr(selected, "name", "unknown")}')
            await torrent.stream(selected, host=config.AIOTORRENT_HOST or '127.0.0.1', port=port)

        task = asyncio.create_task(run_backend())
        info['aiotorrent_port'] = port
        info['aiotorrent_task'] = task

        # Give backend a small warm-up window to bind the port without opening stream endpoint
        for _ in range(20):
            if task.done():
                exc = task.exception()
                if exc:
                    raise RuntimeError(f'aiotorrent backend failed: {str(exc)}')
                break
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection('127.0.0.1', port),
                    timeout=1,
                )
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                with contextlib.suppress(Exception):
                    reader.feed_eof()
                return
            except Exception:
                await asyncio.sleep(0.3)

    async def _wait_for_peers(self, stream_token: str, timeout: int = 15) -> bool:
        """Wait for peers with fallback - return True even if no peers found after timeout"""
        info = self._get_stream_info(stream_token)
        if not info:
            return False

        started = datetime.now()
        last_log_second = -1
        while (datetime.now() - started).total_seconds() < timeout:
            torrent = info.get('aiotorrent_instance')
            peers = list(getattr(torrent, 'peers', []) or []) if torrent else []
            handshaked = [peer for peer in peers if getattr(peer, 'has_handshaked', False)]
            if handshaked:
                logger.info(
                    f'aiotorrent peers ready for {stream_token}: '
                    f'{len(handshaked)}/{len(peers)} handshaked'
                )
                return True

            elapsed = int((datetime.now() - started).total_seconds())
            if elapsed % 3 == 0 and elapsed != last_log_second:
                last_log_second = elapsed
                logger.info(
                    f'Waiting peers for {stream_token}: '
                    f'{len(handshaked)}/{len(peers)} handshaked, elapsed={elapsed}s (timeout={timeout}s)'
                )

            await asyncio.sleep(1)

        # Fallback: Even if no peers, allow FFmpeg to attempt streaming via DHT or other means
        logger.warning(f'No handshaked peers found in {timeout}s, proceeding with fallback mode')
        logger.warning(f'  Stream will be attempted without known peers - DHT/local discovery may provide data')
        return True

    async def stream_torrent_handler(self, request: web.Request) -> web.Response:
        stream_token = request.match_info['stream_token']
        info = self._get_stream_info(stream_token)
        if not info:
            return web.Response(status=404, text='Stream not found or expired')

        return web.Response(
            body=info['torrent_bytes'],
            headers={
                'Content-Type': 'application/x-bittorrent',
                'Content-Disposition': f"attachment; filename=\"{stream_token}.torrent\"",
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
            }
        )

    async def stream_page_handler(self, request: web.Request) -> web.Response:
        stream_token = request.match_info['stream_token']
        logger.info(f'Stream page requested: {stream_token}')
        info = self._get_stream_info(stream_token)
        if not info:
            return web.Response(status=404, text='Stream not found or expired')

        external_url = config.EXTERNAL_URL or None
        if config.AIOTORRENT_STREAM_URL_TEMPLATE:
            logger.warning('AIOTORRENT_STREAM_URL_TEMPLATE is set, but redirect is disabled to keep ffmpeg proxy path active')

        try:
            await self._start_aiotorrent_backend(stream_token)
        except Exception as e:
            logger.error(f'Failed to start aiotorrent backend for {stream_token}: {str(e)}')
            return web.Response(status=500, text=f'Failed to start aiotorrent backend: {str(e)}')

        safe_title = html.escape(info['title'])
        media_url = f'/stream-media/{stream_token}'
        torrent_url = self.get_stream_torrent_url(stream_token, external_url)
        page_html = f"""
<!doctype html>
<html lang=\"ru\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />
    <title>{safe_title}</title>
    <style>
        body {{ font-family: sans-serif; margin: 16px; background: #111; color: #f5f5f5; }}
        a {{ color: #9bd; }}
        code {{ color: #d9f; }}
        video {{ width: 100%; max-height: 75vh; background: #000; margin-top: 12px; }}
    </style>
</head>
<body>
    <h2>{safe_title}</h2>
    <p>Поток запущен через библиотеку <code>aiotorrent</code>.</p>
    <video controls autoplay src=\"{media_url}\"></video>
    <p>Ссылка на .torrent:</p>
    <p><a href=\"{torrent_url}\">{torrent_url}</a></p>
</body>
</html>
        """
        return web.Response(
            text=page_html,
            content_type='text/html',
            headers={
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
                'Expires': '0',
            },
        )

    async def stream_media_handler(self, request: web.Request) -> web.StreamResponse:
        stream_token = request.match_info['stream_token']
        logger.info(f'Stream media requested: {stream_token}')
        info = self._get_stream_info(stream_token)
        if not info:
            return web.Response(status=404, text='Stream not found or expired')

        try:
            await self._start_aiotorrent_backend(stream_token)
        except Exception as e:
            logger.error(f'Failed to bootstrap aiotorrent media for {stream_token}: {str(e)}')
            return web.Response(status=500, text=f'Failed to start media backend: {str(e)}')

        await self._wait_for_peers(stream_token, timeout=30)

        port = info.get('aiotorrent_port')
        if not port:
            return web.Response(status=500, text='aiotorrent port is not available')

        upstream_url = f'http://127.0.0.1:{port}/'
        logger.info(f"Starting ffmpeg for stream {stream_token}, upstream: {upstream_url}")
        ffmpeg_cmd = [
            'ffmpeg',
            '-hide_banner',
            '-loglevel',
            'warning',
            '-i',
            upstream_url,
            '-map',
            '0:v:0',
            '-map',
            '0:a:0?',
            '-c:v',
            'libx264',
            '-preset',
            'ultrafast',
            '-crf',
            '28',
            '-c:a',
            'aac',
            '-b:a',
            '192k',
            '-movflags',
            'frag_keyframe+empty_moov+default_base_moof',
            '-f',
            'mp4',
            'pipe:1',
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Consume stderr continuously to prevent pipe blocking
            async def consume_stderr():
                try:
                    while True:
                        line = await process.stderr.readline()
                        if not line:
                            break
                        logger.warning(f"ffmpeg ({stream_token}): {line.decode('utf-8', errors='ignore').strip()}")
                except Exception:
                    pass

            stderr_task = asyncio.create_task(consume_stderr())

            # Read first chunk to ensure process actually started and outputting data
            try:
                first_chunk = await asyncio.wait_for(process.stdout.read(1024 * 64), timeout=120)
            except asyncio.TimeoutError:
                stderr_task.cancel()
                with contextlib.suppress(Exception):
                    if process.returncode is None:
                        process.kill()
                logger.error(f'ffmpeg did not produce first bytes in time for {stream_token}')
                return web.Response(status=504, text='Stream did not start in 120 seconds (torrent is too slow)')
            
            if not first_chunk:
                stderr_task.cancel()
                with contextlib.suppress(Exception):
                    if process.returncode is None:
                        process.kill()
                return web.Response(status=500, text='FFmpeg failed to start or produced no output (see bot logs)')

            headers = {
                'Content-Type': 'video/mp4',
                'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                'Pragma': 'no-cache',
            }
            response = web.StreamResponse(status=200, headers=headers)
            await response.prepare(request)
            
            await response.write(first_chunk)

            while True:
                chunk = await process.stdout.read(1024 * 64)
                if not chunk:
                    break
                await response.write(chunk)

            await process.wait()
            
            try:
                await response.write_eof()
            except Exception:
                pass
                
            return response
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f'Stream proxy error for {stream_token}: {str(e)}')
            return web.Response(status=500, text=f'Stream proxy error: {str(e)}')
        finally:
            with contextlib.suppress(Exception):
                if 'process' in locals() and process.returncode is None:
                    process.kill()

    async def download_handler(self, request: web.Request) -> web.StreamResponse:
        file_hash = request.match_info['file_hash']
        
        logger.info(f'Download requested: {file_hash}')
        
        self.registry.cleanup_expired()
        
        file_info = self.registry.get(file_hash)
        if not file_info:
            logger.warning(f'File not in registry: {file_hash}, trying to find on disk...')
            return web.Response(status=404, text='File not found or expired')
        
        file_path = Path(file_info['path'])
        
        # Search for file - try exact path first, then search by name/size
        found_path = None
        
        if file_path.exists():
            found_path = file_path
        else:
            logger.warning(f'File path not found: {file_path}, searching by name/size...')
            # Search in downloads directory
            downloads_base = Path(config.DOWNLOADS_PATH).resolve()
            if downloads_base.exists():
                filename = file_info['name']
                file_size = file_info['size']
                
                for item in downloads_base.rglob(filename):
                    try:
                        if item.stat().st_size == file_size:
                            found_path = item
                            logger.info(f'Found file by search: {found_path}')
                            break
                    except (OSError, FileNotFoundError):
                        pass
        
        if not found_path:
            logger.error(f'File not found for hash {file_hash}: {file_info["name"]}')
            return web.Response(status=404, text='File not found')
        
        try:
            file_size_bytes = found_path.stat().st_size
            logger.info(f'Serving file: {file_hash} -> {file_info["name"]} ({file_size_bytes / (1024*1024):.2f} MB)')

            content_type, _ = mimetypes.guess_type(str(found_path))
            if not content_type:
                content_type = 'application/octet-stream'

            response = web.FileResponse(found_path)
            filename = file_info['name']
            safe_ascii = ''.join(ch if ord(ch) < 128 else '_' for ch in filename)
            quoted_name = quote(filename)
            response.headers['Content-Disposition'] = (
                f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{quoted_name}"
            )
            response.headers['Content-Type'] = content_type
            response.headers['Content-Length'] = str(file_size_bytes)
            
            return response
        except Exception as e:
            logger.error(f'Download error for {file_hash}: {str(e)}')
            return web.Response(status=500, text='Download error')

    def get_download_url(self, file_hash: str, external_url: Optional[str] = None) -> str:
        base_url = external_url or config.EXTERNAL_URL or ''
        if base_url:
            return f'{base_url.rstrip("/")}/download/{file_hash}'
        return f'http://127.0.0.1:{self.port}/download/{file_hash}'

    async def run(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', self.port)
        await site.start()
        logger.info(f'HTTP server started on port {self.port}')


http_server: Optional[HTTPServer] = None


async def init_http_server() -> HTTPServer:
    global http_server
    logger.info("Initializing RuTracker session...")
    await _get_rutracker_session()
    http_server = HTTPServer(port=8080)
    await http_server.run()
    return http_server


def get_http_server() -> Optional[HTTPServer]:
    return http_server


def register_file_for_download(filepath: str, expires_in_hours: int = 24) -> str:
    if not http_server:
        raise RuntimeError('HTTP server not initialized')
    return http_server.registry.register(filepath, expires_in_hours)


def get_download_url(file_hash: str, external_url: Optional[str] = None) -> str:
    if not http_server:
        raise RuntimeError('HTTP server not initialized')
    return http_server.get_download_url(file_hash, external_url)


def register_stream_torrent(torrent_bytes: bytes, title: str, expires_in_hours: int = 24) -> str:
    if not http_server:
        raise RuntimeError('HTTP server not initialized')
    return http_server.register_stream_torrent(torrent_bytes, title, expires_in_hours)


def get_stream_url(stream_token: str, external_url: Optional[str] = None) -> str:
    if not http_server:
        raise RuntimeError('HTTP server not initialized')
    return http_server.get_stream_url(stream_token, external_url)


def get_stream_torrent_url(stream_token: str, external_url: Optional[str] = None) -> str:
    if not http_server:
        raise RuntimeError('HTTP server not initialized')
    return http_server.get_stream_torrent_url(stream_token, external_url)


def get_aiotorrent_stream_url(stream_token: str, external_url: Optional[str] = None) -> Optional[str]:
    if not http_server:
        raise RuntimeError('HTTP server not initialized')
    return http_server.get_aiotorrent_stream_url(stream_token, external_url)
