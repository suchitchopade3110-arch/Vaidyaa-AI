import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import pool

engine = create_async_engine(
    "postgresql+asyncpg://1:1@localhost/1",
    poolclass=pool.NullPool,
)
print("Pool class is:", engine.pool)
