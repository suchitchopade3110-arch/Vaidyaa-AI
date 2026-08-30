# Import all models here so Alembic can discover them
from app.db.base_class import Base  # noqa
from app.models.patient import Patient  # noqa
from app.models.claim import Claim  # noqa
from app.models.report import Report  # noqa
from app.models.image_analysis import ImageAnalysis  # noqa
from app.models.qr_access import QRAuditLog, QRToken  # noqa
from app.models.user import User  # noqa
from app.models.refresh_token import RefreshToken  # noqa
from app.models.audit_log import AuditLog  # noqa
# Phase 1 business-readiness skeleton (see docs/PHASE1_SKELETON.md)
from app.models.organisation import Organisation, Department  # noqa
from app.models.async_job import AsyncJobRecord  # noqa
from app.models.consent import ConsentRecord  # noqa
from app.models.sign_off import SignOff  # noqa
