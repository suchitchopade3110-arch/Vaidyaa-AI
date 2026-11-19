"""Phase 3 patient history routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.ownership import is_admin
from app.db.session import get_db
from app.models.report import Report
from app.services.patient_history_service import (
    owned_by,
    compare_reports,
    get_longitudinal_trend,
    get_patient_history,
)


router = APIRouter(prefix="/patients", tags=["Patient History"])


def _scope(user: dict) -> uuid.UUID | None:
    """Whose reports this caller may read; None = unrestricted (admin).

    Patients have no owner column, so access follows the jobs that produced
    each report (Report.celery_task_id -> async_jobs.user_id). Before this,
    any authenticated user could read any patient's full report history.
    """
    return None if is_admin(user) else uuid.UUID(str(user["sub"]))


@router.get("/{patient_id}/history")
async def patient_history(
    patient_id: str,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Return recent reports for a patient."""
    history = await get_patient_history(patient_id, limit=limit, db=db, owner_user_id=_scope(user))
    if not history:
        raise HTTPException(status_code=404, detail="No history found for patient")
    return {"patient_id": patient_id, "history": history, "count": len(history)}


async def _load_patient_report(report_id: str, patient_id: str, db: AsyncSession, owner: uuid.UUID | None) -> dict:
    try:
        report_uuid = uuid.UUID(report_id)
        patient_uuid = uuid.UUID(patient_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid patient_id or report_id") from exc

    query = select(Report).where(Report.id == report_uuid, Report.patient_id == patient_uuid)
    if owner is not None:
        query = query.where(owned_by(owner))
    report = (await db.execute(query)).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail=f"Report {report_id} not found")

    from app.services.patient_history_service import _report_to_dict

    return _report_to_dict(report)


@router.get("/{patient_id}/compare")
async def compare_patient_reports(
    patient_id: str,
    current_id: str,
    previous_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Compare two reports for a patient."""
    owner = _scope(user)
    current = await _load_patient_report(current_id, patient_id, db, owner)
    previous = await _load_patient_report(previous_id, patient_id, db, owner)
    return compare_reports(current, previous)


@router.get("/{patient_id}/trend/{test_name}")
async def lab_value_trend(
    patient_id: str,
    test_name: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Return a longitudinal trend for a lab value."""
    return await get_longitudinal_trend(patient_id, test_name, db=db, owner_user_id=_scope(user))


@router.get("/{patient_id}/latest")
async def latest_report(
    patient_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Return the most recent report for a patient."""
    history = await get_patient_history(patient_id, limit=1, db=db, owner_user_id=_scope(user))
    if not history:
        raise HTTPException(status_code=404, detail="No reports found")
    return history[0]
