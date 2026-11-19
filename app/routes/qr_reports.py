import io
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.ownership import get_owned_job
from app.db.session import get_db
from app.services.qr_service import (
    ALGORITHM,
    burn_report_token,
    find_report,
    generate_qr_image,
    generate_report_token,
    get_report_pdf_bytes,
    get_report_summary,
    log_qr_scan,
    validate_report_token,
)

router = APIRouter(prefix="/reports", tags=["QR Report Access"])


def _client_host(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _public_base_url(request: Request) -> str:
    configured = (
        os.getenv("PUBLIC_BASE_URL")
        or os.getenv("QR_BASE_URL")
        or os.getenv("FRONTEND_BASE_URL")
    )
    if configured:
        return configured.rstrip("/")

    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    scheme = (forwarded_proto or request.url.scheme or "http").split(",", 1)[0].strip()
    host = (forwarded_host or request.headers.get("host") or "").split(",", 1)[0].strip()
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/{report_id}/qr")
async def get_report_qr(
    report_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_current_user),
):
    """Generate a signed QR PNG for a completed, signed-off report.

    Minting a share link is an authenticated, owner-only action: the QR
    token it produces lets ANYONE holding it download the report PDF
    without logging in, so this used to be an anonymous PHI leak for any
    signed report whose id was known. `report_id` may be a Report row id or
    the job (Celery task) id; either way it must resolve to a job the
    caller owns. Unknown and not-yours both return 404.
    """
    report = await find_report(report_id, db)
    job_id = report.celery_task_id if report and report.celery_task_id else report_id
    if await get_owned_job(db, job_id, user) is None:
        raise HTTPException(status_code=404, detail="Report not found")
    # The token's patient_id comes from the report row only — a caller-supplied
    # ?patient_id= used to be embedded (and logged on every scan) verbatim.
    token = await generate_report_token(report_id, db)
    payload = jwt.decode(token, settings.QR_SECRET_KEY or settings.SECRET_KEY, algorithms=[ALGORITHM])
    base_url = _public_base_url(request)
    qr_bytes = generate_qr_image(token, base_url=base_url)

    await log_qr_scan(
        db,
        token_id=payload.get("token_id"),
        report_id=payload.get("report_id"),
        patient_id=payload.get("patient_id"),
        action="qr_generated",
        ip_address=_client_host(request),
        user_agent=request.headers.get("user-agent"),
    )

    return StreamingResponse(
        io.BytesIO(qr_bytes),
        media_type="image/png",
        headers={
            "Content-Disposition": f"inline; filename=report_{report_id[:8]}_qr.png",
            "Cache-Control": "no-store",
            "X-QR-Base-URL": base_url,
        },
    )


@router.get("/preview")
async def report_preview(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Validate a QR token without consuming it and return preview-card data."""
    payload = await validate_report_token(token, db, burn=False)
    report_id = payload["report_id"]
    summary = await get_report_summary(report_id, db)

    await log_qr_scan(
        db,
        token_id=payload.get("token_id"),
        report_id=report_id,
        patient_id=payload.get("patient_id"),
        action="preview_accessed",
        ip_address=_client_host(request),
        user_agent=request.headers.get("user-agent"),
    )

    return {
        "report_id": report_id,
        "patient_name": summary.get("patient_name", "Patient"),
        "report_date": summary.get("created_at"),
        "report_type": summary.get("report_type"),
        "key_findings": summary.get("key_findings", []),
        "urgency_flag": summary.get("urgency_flag", "routine"),
        "confidence": summary.get("confidence", 0),
        "download_token": token,
        "expires_at": payload.get("exp"),
        "disclaimer": "AI-assisted analysis. NOT a medical diagnosis.",
    }


@router.get("/download")
async def download_report_pdf(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Consume a one-time QR token and stream the report PDF.

    The token is spent only once the PDF exists, so a rendering failure
    leaves it usable for a retry.
    """
    payload = await validate_report_token(token, db, burn=False)
    report_id = payload["report_id"]
    pdf_bytes = await get_report_pdf_bytes(report_id, db)
    await burn_report_token(payload, db)

    await log_qr_scan(
        db,
        token_id=payload.get("token_id"),
        report_id=report_id,
        patient_id=payload.get("patient_id"),
        action="pdf_downloaded",
        ip_address=_client_host(request),
        user_agent=request.headers.get("user-agent"),
    )

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="vaidya_report_{report_id[:8]}.pdf"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
