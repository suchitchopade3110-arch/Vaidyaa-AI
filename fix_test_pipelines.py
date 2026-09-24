import re

with open('tests/test_pipelines.py', 'r') as f:
    content = f.read()

# Let's replace the whole setup back to _FakeSession, but correctly implemented

search_pattern = r"from sqlalchemy.ext.asyncio import create_async_engine.*?client = TestClient\(app\)"

replace_content = """
# Global store for fake DB records
_fake_db_store = {}

class _FakeScalars:
    def __init__(self, record):
        self._record = record

    def first(self):
        return self._record

class _FakeResult:
    def __init__(self, record):
        self._record = record

    def scalar_one_or_none(self):
        return self._record

    def scalars(self):
        return _FakeScalars(self._record)

class _FakeSession:
    def __init__(self):
        self.added = []
        self.flushed = False

    async def execute(self, _query):
        query_str = str(_query).lower()

        # Look for existing records of the correct type in the store
        for record in _fake_db_store.values():
            model_name = record.__class__.__tablename__
            if model_name in query_str:
                return _FakeResult(record)

        # Fallbacks for specific tables if not in store
        import uuid
        if "async_jobs" in query_str:
            from app.models.async_job import AsyncJobRecord
            record = AsyncJobRecord(
                id="dummy_task",
                user_id=uuid.UUID(TEST_USER_ID),
                org_id=uuid.UUID(TEST_USER_ID),
                pipeline="test"
            )
            return _FakeResult(record)

        return _FakeResult(None)

    def add(self, record):
        self.added.append(record)
        if hasattr(record, 'id'):
            _fake_db_store[str(record.id)] = record

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.flushed = True

    async def refresh(self, obj):
        pass

async def override_get_db():
    yield _FakeSession()

app.dependency_overrides[get_db] = override_get_db

from app.core.middleware import AuditLogMiddleware
# Monkeypatch AuditLogMiddleware to skip writing logs
async def mock_write_log(*args, **kwargs):
    pass
AuditLogMiddleware._write_log = mock_write_log

client = TestClient(app)
"""

new_content = re.sub(search_pattern, replace_content, content, flags=re.DOTALL)

with open('tests/test_pipelines.py', 'w') as f:
    f.write(new_content)
