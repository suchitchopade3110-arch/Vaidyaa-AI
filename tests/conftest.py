"""Session-wide test setup.

Import every SQLAlchemy model before any test runs. Several models use
string-referenced relationships (e.g. Patient -> "Claim") that SQLAlchemy
only resolves the first time any mapper in the shared registry is
configured — which happens lazily, on first real use, in whichever test
module happens to touch an ORM model first. In the real app this is a
non-issue because app/main.py imports app.db.base (which imports every
model) at boot, before any request is served. Without that same import
here, whichever test file runs first "wins" and silently leaves the
other models unconfigured for the rest of the process — a previously
passing test file can start failing purely because of import order.
"""
import app.db.base  # noqa: F401
