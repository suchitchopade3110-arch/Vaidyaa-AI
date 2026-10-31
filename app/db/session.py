from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.config import settings

import sys
from sqlalchemy.pool import NullPool

# Fix for "Event loop is closed" errors during pytest teardown
pool_kwargs = {}
if 'pytest' in sys.modules or settings.APP_ENV == 'test':
    pool_kwargs['poolclass'] = NullPool
else:
    pool_kwargs['pool_size'] = 10
    pool_kwargs['max_overflow'] = 20

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_ENV == "development",
    pool_pre_ping=True,
    **pool_kwargs
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
