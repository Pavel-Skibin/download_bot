import asyncio
import json
import hashlib
import mimetypes
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timedelta
from urllib.parse import quote
from aiohttp import web
from bot.utils.config import config
from bot.utils.logger import BotLogger

logger = BotLogger.get_logger()


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
        self.setup_routes()

    def setup_routes(self) -> None:
        self.app.router.add_get('/download/{file_hash}', self.download_handler)
        self.app.router.add_get('/health', self.health_handler)

    async def health_handler(self, request: web.Request) -> web.Response:
        # Log current registry state for debugging
        if self.registry.files:
            logger.debug(f'Health check - Files in registry: {len(self.registry.files)}')
            for fh, info in list(self.registry.files.items())[:3]:  # Log first 3
                path = info['path']
                logger.debug(f'  {fh}: {info["name"]} ({info["size"]/(1024*1024):.1f}MB) at {path}')
        
        return web.json_response({'status': 'ok', 'files': len(self.registry.files)})

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
