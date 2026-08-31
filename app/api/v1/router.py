from fastapi import APIRouter
from app.api.v1.routes import admin, claims, consent, images, orgs, reports, jobs, health, signoff

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(claims.router, prefix="/verify", tags=["Claim Verification"])
api_router.include_router(images.router, prefix="/analyze/image", tags=["Image Analysis"])
api_router.include_router(reports.router, prefix="/analyze/report", tags=["Report Analysis"])
api_router.include_router(jobs.router, prefix="/jobs", tags=["Job Status"])
api_router.include_router(signoff.router, prefix="/jobs", tags=["Clinician Sign-off"])
api_router.include_router(consent.router, prefix="/consent", tags=["Consent"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(orgs.router, prefix="/admin/orgs", tags=["Organisations"])
