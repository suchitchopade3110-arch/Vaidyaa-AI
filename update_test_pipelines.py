import re

with open('tests/test_pipelines.py', 'r') as f:
    content = f.read()

# Find the mock implementation from `class _FakeScalars:` to `AuditLogMiddleware._write_log = mock_write_log`
# and replace it.

search_pattern = r"class _FakeScalars:.*AuditLogMiddleware\._write_log = mock_write_log"

replace_content = """from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.db.base import Base
import asyncio
import pytest

# Use in-memory SQLite for testing
test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    echo=False,
    pool_pre_ping=True,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Initialize the database schema for the test engine
async def init_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Synchronously initialize DB for tests (TestClient is sync)
loop = asyncio.get_event_loop()
loop.run_until_complete(init_db())

async def override_get_db():
    async with TestingSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

app.dependency_overrides[get_db] = override_get_db

# We also need to patch AsyncSessionLocal in middleware because it uses it directly without get_db
import app.core.middleware
app.core.middleware.AsyncSessionLocal = TestingSessionLocal
"""

new_content = re.sub(search_pattern, replace_content, content, flags=re.DOTALL)

with open('tests/test_pipelines.py', 'w') as f:
    f.write(new_content)
