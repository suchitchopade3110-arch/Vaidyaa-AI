import re

with open('tests/test_pipelines.py', 'r') as f:
    content = f.read()

search_pattern = r"# Initialize the database schema for the test engine.*?client = TestClient\(app\)"

replace_content = """
# Initialize the database schema for the test engine
@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    async def init_db():
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Run loop
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    yield
    loop.close()

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

import app.core.middleware
app.core.middleware.AsyncSessionLocal = TestingSessionLocal

client = TestClient(app)
"""

new_content = re.sub(search_pattern, replace_content, content, flags=re.DOTALL)

with open('tests/test_pipelines.py', 'w') as f:
    f.write(new_content)
