import asyncio

from celery.result import AsyncResult
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.auth import decode_access_token
from app.db.session import AsyncSessionLocal
from app.models.async_job import AsyncJobRecord
from app.workers.celery_app import celery_app

router = APIRouter()


@router.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    """Stream live Celery task status for `job_id` every 2s until it completes.

    Sends `{job_id, status, progress, step}`, plus `result` on SUCCESS or
    `error` on FAILURE, then closes the connection.

    SEC-02: authenticates via `?token=` query param before accepting.
    SEC-01: also checks the token's owner matches AsyncJobRecord.user_id
    for `job_id`, same rule `require_job_owner` applies to the HTTP routes
    — a job with no AsyncJobRecord (nothing has written one yet, or it
    predates this check) is refused too, not silently allowed.
    """
    token = websocket.query_params.get("token")
    # TODO(SEC-02): also accept the token via Sec-WebSocket-Protocol, for
    # clients that can't put a bearer token in a URL query string.
    try:
        user = decode_access_token(token)
    except ValueError:
        await websocket.close(code=4401)  # unauthorized
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AsyncJobRecord).where(AsyncJobRecord.id == job_id))
        record = result.scalar_one_or_none()

    if record is None or str(record.user_id) != user.get("sub"):
        await websocket.close(code=4404)  # not found / not yours
        return

    await websocket.accept()
    try:
        while True:
            try:
                result = AsyncResult(job_id, app=celery_app)
                state = result.state
                info = result.info if isinstance(result.info, dict) else {}
                payload = {
                    "job_id": job_id,
                    "status": state,
                    "progress": info.get("pct", 0),
                    "step": info.get("step", ""),
                }

                if state == "SUCCESS":
                    payload["result"] = result.result
                    await websocket.send_json(payload)
                    break

                if state == "FAILURE":
                    payload["error"] = str(result.result)
                    await websocket.send_json(payload)
                    break

                await websocket.send_json(payload)

            except Exception as celery_exc:
                await websocket.send_json({
                    "job_id": job_id,
                    "status": "ERROR",
                    "progress": 0,
                    "step": "",
                    "error": str(celery_exc),
                })
                break

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
