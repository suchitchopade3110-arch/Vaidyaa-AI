import asyncio

from celery.result import AsyncResult
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.auth import decode_access_token
from app.workers.celery_app import celery_app

router = APIRouter()


@router.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    """Stream live Celery task status for `job_id` every 2s until it completes.

    Sends `{job_id, status, progress, step}`, plus `result` on SUCCESS or
    `error` on FAILURE, then closes the connection.

    SEC-02: authenticates via `?token=` query param (or the same value
    passed as a Sec-WebSocket-Protocol subprotocol — TODO below) before
    accepting. TODO(SEC-01): this only proves *a* valid user is connected,
    not that they own `job_id` — swap in the same ownership check
    `require_job_owner` does (app/core/ownership.py) once job submission
    persists ownership, so users can't watch each other's job progress.
    """
    token = websocket.query_params.get("token")
    # TODO(SEC-02): also accept the token via Sec-WebSocket-Protocol, for
    # clients that can't put a bearer token in a URL query string.
    try:
        decode_access_token(token)
    except ValueError:
        await websocket.close(code=4401)  # unauthorized
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
