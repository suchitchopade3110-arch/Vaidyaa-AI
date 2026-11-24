"""Alembic env.py — VaidyaAI"""
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.base import Base  # noqa: F401 — imports every model onto Base.metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Use the app's own settings (DATABASE_URL or POSTGRES_*) rather than the
# placeholder URL in alembic.ini, so `alembic upgrade head` targets the same
# database as the API and workers. Override with `-x db_url=...` if needed.
_x_url = context.get_x_argument(as_dictionary=True).get("db_url")
if _x_url:
    # configparser interpolation: a URL-encoded password ("p%40ss") must not be read as "%(...)s".
    config.set_main_option("sqlalchemy.url", _x_url.replace("%", "%%"))
else:
    try:
        from app.core.config import settings as _settings
        config.set_main_option(
            "sqlalchemy.url",
            _settings.SYNC_DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1).replace("%", "%%"),
        )
    except Exception:  # settings incomplete (e.g. offline SQL generation) — keep ini value
        pass

def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
