import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
import uuid
from unittest.mock import patch, AsyncMock, MagicMock

from app.routes.websocket_routes import router
from app.models.async_job import AsyncJobRecord
from jose import jwt
from datetime import datetime, timedelta, timezone
from app.core.config import settings

def create_access_token(claims: dict, expires_in: timedelta = timedelta(minutes=20)) -> str:
    payload = {**claims, "exp": datetime.now(timezone.utc) + expires_in, "type": "access"}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


app = FastAPI()
app.include_router(router)

client = TestClient(app)

def test_websocket_auth_query_param():
    job_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    token = create_access_token({"sub": user_id})

    # Mock DB read
    mock_record = AsyncJobRecord(id=job_id, user_id=uuid.UUID(user_id))
    mock_scalar = MagicMock(return_value=mock_record)
    mock_execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=mock_scalar))

    with patch("app.routes.websocket_routes.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_session.return_value.__aenter__.return_value = mock_db

        with patch("app.routes.websocket_routes.AsyncResult") as mock_result:
            mock_result.return_value.state = "SUCCESS"
            mock_result.return_value.result = {"foo": "bar"}

            with client.websocket_connect(f"/ws/{job_id}?token={token}") as websocket:
                data = websocket.receive_json()
                assert data["status"] == "SUCCESS"
                assert data["result"] == {"foo": "bar"}

def test_websocket_auth_sec_websocket_protocol():
    job_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    token = create_access_token({"sub": user_id})

    # Mock DB read
    mock_record = AsyncJobRecord(id=job_id, user_id=uuid.UUID(user_id))
    mock_scalar = MagicMock(return_value=mock_record)
    mock_execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=mock_scalar))

    with patch("app.routes.websocket_routes.AsyncSessionLocal") as mock_session:
        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_session.return_value.__aenter__.return_value = mock_db

        with patch("app.routes.websocket_routes.AsyncResult") as mock_result:
            mock_result.return_value.state = "SUCCESS"
            mock_result.return_value.result = {"foo": "bar"}

            with client.websocket_connect(f"/ws/{job_id}", subprotocols=[token]) as websocket:
                data = websocket.receive_json()
                assert data["status"] == "SUCCESS"
                assert data["result"] == {"foo": "bar"}
                assert websocket.accepted_subprotocol == token

def test_websocket_auth_failure():
    job_id = str(uuid.uuid4())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/{job_id}?token=invalid_token") as websocket:
            websocket.receive_text()

    assert exc_info.value.code == 4401
