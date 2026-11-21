import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, status, Depends
from celery.result import AsyncResult
from datetime import timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.ownership import record_job_ownership, require_job_owner
from app.db.session import get_db
from app.models.async_job import AsyncJobRecord

UTC = timezone.utc

from app.schemas.image import AnalysisType
from app.schemas.job import JobStatus
from app.workers.pipeline_tasks import analyze_image_task
from app.workers.celery_app import celery_app

router = APIRouter()

VALID_IMAGE_TYPES = {t.value for t in AnalysisType}
IMAGE_TYPES = {"xray", "ct", "mri", "skin", "pathology"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".dcm"}
IMAGE_MAX_BYTES = {
    ".dcm": 50 * 1024 * 1024,
    ".jpg": 10 * 1024 * 1024,
    ".jpeg": 10 * 1024 * 1024,
    ".png": 10 * 1024 * 1024,
}
UPLOAD_ROOT = Path("uploads").resolve()


async def _save_image_upload(file: UploadFile, job_id: str) -> tuple[str, str]:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "FILE_FORMAT_UNSUPPORTED",
                "message": f"Extension '{ext}' not allowed. Allowed: {sorted(IMAGE_EXTENSIONS)}",
            },
        )

    content = await file.read()
    max_bytes = IMAGE_MAX_BYTES[ext]
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"File exceeds {max_bytes // 1024 // 1024}MB limit",
            },
        )

    save_dir = UPLOAD_ROOT / "images"
    save_dir.mkdir(parents=True, exist_ok=True)
    save_path = save_dir / f"{job_id}{ext}"
    async with aiofiles.open(save_path, "wb") as handle:
        await handle.write(content)
    return str(save_path), ext.lstrip(".")


@router.post("/{analysis_type}", status_code=status.HTTP_202_ACCEPTED)
async def submit_image_analysis(
    analysis_type: str,
    file: UploadFile = File(..., description="Medical image: DICOM, JPG, PNG"),
    patient_id: str = Form(None),
    clinical_context: str = Form(""),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Submit medical image for analysis.
    """
    if analysis_type not in VALID_IMAGE_TYPES and analysis_type not in IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_IMAGE_TYPE",
                "message": f"image_type must be xray|ct|mri|skin|pathology, got '{analysis_type}'",
            },
        )

    job_id = str(uuid.uuid4())
    file_path, _extension = await _save_image_upload(file, job_id)

    # TODO(DPD-01): call require_valid_consent(db, patient_id, purpose=...)
    # before dispatch and reject if it 403s. See app/core/consent.py.
    # Dispatch Celery task
    task = analyze_image_task.apply_async(
        args=[file_path, analysis_type, patient_id, clinical_context],
        task_id=job_id,
        queue="images",
    )
    await record_job_ownership(db, task.id, user, "image")

    return {
        "job_id": task.id,
        "task_id": task.id,
        "status": "queued",
        "poll_url": f"/api/v1/jobs/{task.id}",
        "estimated_seconds": 30,
    }


@router.get("/status/{task_id}", response_model=JobStatus)
async def get_image_status(task_id: str, _owner: AsyncJobRecord = Depends(require_job_owner)):
    """Poll image analysis task status."""
    request_id = str(uuid.uuid4())
    result = AsyncResult(task_id, app=celery_app)
    state = result.state
    meta = result.info or {}

    if state == "PENDING":
        return JobStatus(request_id=request_id, task_id=task_id, status="pending")
    if state == "PROGRESS":
        return JobStatus(
            request_id=request_id,
            task_id=task_id,
            status="processing",
            progress_pct=meta.get("pct", 0),
        )
    if state == "SUCCESS":
        return JobStatus(
            request_id=request_id,
            task_id=task_id,
            status="complete",
            progress_pct=100,
            result_url=f"/api/v1/analyze/image/{task_id}",
        )
    if state == "FAILURE":
        return JobStatus(request_id=request_id, task_id=task_id, status="failed", error=str(meta))
    return JobStatus(request_id=request_id, task_id=task_id, status=state.lower())


@router.get("/{task_id}")
async def get_image_combined(task_id: str, _owner: AsyncJobRecord = Depends(require_job_owner)):
    """Combined status and result endpoint for the frontend."""
    result = AsyncResult(task_id, app=celery_app)
    state = result.state

    if state == "SUCCESS":
        return result.result
    
    return {
        "status": state.lower(),
        "task_id": task_id,
        "id": task_id
    }
