import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from bot.utils.config import config


class DatabaseLogger:
    def __init__(self, db_path: str = config.DATABASE_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    platform TEXT,
                    format TEXT,
                    file_size INTEGER,
                    duration INTEGER,
                    download_time REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS failed_downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    url TEXT NOT NULL,
                    error_message TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await db.commit()

    async def log_download(
        self,
        user_id: int,
        url: str,
        title: str,
        platform: str,
        format_: str,
        file_size: int,
        duration: int,
        download_time: float
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''INSERT INTO downloads 
                   (user_id, url, title, platform, format, file_size, duration, download_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (user_id, url, title, platform, format_, file_size, duration, download_time)
            )
            await db.commit()

    async def log_failed_download(
        self,
        user_id: int,
        url: str,
        error_message: str
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                '''INSERT INTO failed_downloads 
                   (user_id, url, error_message)
                   VALUES (?, ?, ?)''',
                (user_id, url, error_message)
            )
            await db.commit()

    async def get_stats(self, user_id: int) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                '''SELECT 
                    COUNT(*) as total_count,
                    SUM(CASE WHEN format LIKE 'audio%' THEN 1 ELSE 0 END) as audio_count,
                    SUM(file_size) as total_size,
                    AVG(file_size / NULLIF(download_time, 0)) as avg_speed,
                    MAX(timestamp) as last_download
                FROM downloads WHERE user_id = ?''',
                (user_id,)
            )
            row = await cursor.fetchone()

            if not row or row[0] == 0:
                return {
                    'total_count': 0,
                    'audio_count': 0,
                    'total_size': 0,
                    'avg_speed': 0,
                    'last_download': None
                }

            total_size = row[2] or 0
            avg_speed = row[3] or 0

            return {
                'total_count': row[0],
                'audio_count': row[1] or 0,
                'total_size': total_size,
                'avg_speed': max(0, avg_speed),
                'last_download': row[4]
            }

    async def get_history(self, user_id: int, limit: int = 10) -> List[Dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                '''SELECT id, url, title, format, file_size, timestamp 
                FROM downloads 
                WHERE user_id = ? 
                ORDER BY timestamp DESC 
                LIMIT ?''',
                (user_id, limit)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
