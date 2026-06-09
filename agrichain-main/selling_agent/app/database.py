from motor.motor_asyncio import AsyncIOMotorClient

import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGODB_URI)
db = client["vendorgroove"]

# his existing local/own db stays as-is for his group-buying features
AGRICHAIN_URI = os.getenv("MDB_MCP_CONNECTION_STRING")  # the shared non-SRV string
agrichain_db = AsyncIOMotorClient(AGRICHAIN_URI)["agrichain"]
