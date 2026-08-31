"""PLT-01 — repository-layer org scoping.

The requirement is explicit that scoping happens "at the repository layer,
not per-route" — i.e. every query that touches org-owned data should run
through something like `scope_to_org` rather than each route remembering to
add a `WHERE org_id = ...` filter by hand. This is the seam for that; it's
not called anywhere yet because there is no repository layer to call it
from today (routes query models directly). Introducing one is a larger
refactor than this skeleton pass covers.
"""
from uuid import UUID

from sqlalchemy import Select


def scope_to_org(query: Select, org_id: UUID, org_id_column) -> Select:
    """Add a `WHERE <org_id_column> = :org_id` clause to `query`.

    TODO(PLT-01): once a repository layer exists, every read/write of
    Organisation-scoped data (async_jobs, reports, images, claims, ...)
    should go through a helper built on this, so cross-org access is
    impossible by construction rather than by remembering to filter.

    Usage (once wired):
        query = scope_to_org(select(AsyncJobRecord), org_id, AsyncJobRecord.org_id)
    """
    return query.where(org_id_column == org_id)
