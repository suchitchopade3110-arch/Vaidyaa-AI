"""PLT-01 — organisation hierarchy: the write side.

Organisation/Department models and the User.org_id/department_id columns
have existed since the Phase 1 skeleton, but nothing could ever populate
them — there was no route to create an org, create a department, or
assign a user to either. This is that.

All admin-only (bootstrap problem accepted: the first admin account is
created the same way any user is today, via /auth/register + a manual
role flip — there's no self-serve "become an org admin" flow, same gap
noted for DPD-01's consent-grant endpoints).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_role
from app.db.session import get_db
from app.models.organisation import Department, Organisation
from app.models.user import User

router = APIRouter(dependencies=[Depends(require_role("admin"))])


class OrgRequest(BaseModel):
    name: str


class OrgResponse(BaseModel):
    id: str
    name: str
    is_active: bool


class DepartmentRequest(BaseModel):
    name: str


class DepartmentResponse(BaseModel):
    id: str
    org_id: str
    name: str


class AssignOrgRequest(BaseModel):
    org_id: str
    department_id: str | None = None


class UserSummary(BaseModel):
    id: str
    username: str
    org_id: str | None
    department_id: str | None


@router.post("", response_model=OrgResponse, status_code=201)
async def create_org(body: OrgRequest, db: AsyncSession = Depends(get_db)):
    org = Organisation(id=uuid.uuid4(), name=body.name)
    db.add(org)
    await db.flush()
    return OrgResponse(id=str(org.id), name=org.name, is_active=org.is_active)


@router.get("", response_model=list[OrgResponse])
async def list_orgs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Organisation))
    return [
        OrgResponse(id=str(o.id), name=o.name, is_active=o.is_active)
        for o in result.scalars().all()
    ]


@router.post("/{org_id}/departments", response_model=DepartmentResponse, status_code=201)
async def create_department(org_id: str, body: DepartmentRequest, db: AsyncSession = Depends(get_db)):
    try:
        parsed_org_id = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="org_id is not a UUID")

    org = (await db.execute(select(Organisation).where(Organisation.id == parsed_org_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")

    department = Department(id=uuid.uuid4(), org_id=parsed_org_id, name=body.name)
    db.add(department)
    await db.flush()
    return DepartmentResponse(id=str(department.id), org_id=str(department.org_id), name=department.name)


@router.post("/users/{user_id}/assign", response_model=UserSummary)
async def assign_user_to_org(user_id: str, body: AssignOrgRequest, db: AsyncSession = Depends(get_db)):
    """Assign a user to an org (and optionally a department within it).

    Does not touch existing JWTs already issued to that user — the org_id
    claim is only set at login/refresh (see app/routes/auth.py), so a
    reassigned user needs to log in again before it takes effect. Not
    resolved here — flagging it rather than pretending it's instant.
    """
    try:
        parsed_user_id = uuid.UUID(user_id)
        parsed_org_id = uuid.UUID(body.org_id)
        parsed_department_id = uuid.UUID(body.department_id) if body.department_id else None
    except ValueError:
        raise HTTPException(status_code=422, detail="user_id/org_id/department_id must be UUIDs")

    user = (await db.execute(select(User).where(User.id == parsed_user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    org = (await db.execute(select(Organisation).where(Organisation.id == parsed_org_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organisation not found")

    if parsed_department_id is not None:
        department = (
            await db.execute(select(Department).where(Department.id == parsed_department_id))
        ).scalar_one_or_none()
        if department is None or department.org_id != parsed_org_id:
            raise HTTPException(status_code=422, detail="department_id does not belong to org_id")

    user.org_id = parsed_org_id
    user.department_id = parsed_department_id

    return UserSummary(
        id=str(user.id),
        username=user.username,
        org_id=str(user.org_id),
        department_id=str(user.department_id) if user.department_id else None,
    )
