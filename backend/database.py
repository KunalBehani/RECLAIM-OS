import os
from pathlib import Path
from motor.motor_asyncio import AsyncIOMotorClient

try:
    from dotenv import load_dotenv
    ROOT_DIR = Path(__file__).parent
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
db_name = os.environ.get("DB_NAME", "reclaim_os")
try:
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
except Exception:
    client = None
    db = None


async def get_settings() -> dict:
    from constants import DEFAULT_SETTINGS

    settings = await db.settings.find_one({"key": "policy"}, {"_id": 0})
    if not settings:
        settings = dict(DEFAULT_SETTINGS)
        await db.settings.insert_one(dict(DEFAULT_SETTINGS))
    return settings
