import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

client = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]


async def get_settings() -> dict:
    from constants import DEFAULT_SETTINGS

    settings = await db.settings.find_one({"key": "policy"}, {"_id": 0})
    if not settings:
        settings = dict(DEFAULT_SETTINGS)
        await db.settings.insert_one(dict(DEFAULT_SETTINGS))
    return settings
