import aiosqlite
import os

DB_PATH = "users.db"

async def init_db():
    """Initialize database and create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)
        await db.commit()

async def add_user(user_id: int, username: str, first_name: str, last_name: str):
    """Add or update a user in the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (user_id, username, first_name, last_name))
        await db.commit()

async def get_all_users():
    """Get all users from the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, first_name, last_name, joined_at FROM users ORDER BY joined_at DESC"
        ) as cursor:
            return await cursor.fetchall()

async def get_user_count():
    """Get total user count."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
