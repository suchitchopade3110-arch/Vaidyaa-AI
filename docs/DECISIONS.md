# Decisions log

One line per decision: date · decision · reason.

- 2026-11-19 · Patch + PRD were not in the repo root; copied from the session upload folder, PRD moved to `docs/PRD.md`, patch not committed. · Per the task brief.
- 2026-11-19 · `git apply --check` passed on `main` at `ec5503f`; no 3-way / re-branching needed. · Baseline unchanged since the patch was cut.
- 2026-11-19 · Work branch is `v2/verified-lab-intelligence` as the brief and PRD §11.1 require. The harness also designates `claude/zen-keller-hxjo94` as the session branch; pushes go to `v2/verified-lab-intelligence` (explicitly requested by the user), never to `main`. · User's explicit instruction overrides the default.
- 2026-11-19 · `AGENTS.md` describes a React/TypeScript app (`npm run lint/build`). The repo is a Python/FastAPI backend with an in-browser-Babel UI; the Python gate (ruff, pytest, alembic) is used instead. `ui/package.json` has no lint script. · Mismatched boilerplate; the PRD is the source of truth. Commit prefixes follow the PRD (`E<epic>.<FR>:`) and AGENTS.md types where compatible.
- 2026-11-19 · No Docker daemon in this environment: Postgres 16 and Redis 7 run as local system services (`service postgresql start`, `redis-server --daemonize yes`), DB `vaidyaai` / user `vaidya` with a throwaway local password in the git-ignored `.env`. · Appendix C allows local services.
- 2026-11-19 · The PyTorch CPU index (download.pytorch.org) is blocked by the sandbox proxy (403). Installed `torch==2.3.0`/`torchvision==0.18.0` from PyPI instead, as Appendix C permits. · Network policy.
- 2026-11-19 · `gh auth status` fails (token invalid). PR creation and pushes will use `git push` and the GitHub MCP tools if the remote is writable. · Environment.
- 2026-11-19 · `requirements.txt` pins `torch==2.3.0+cpu` via the blocked PyTorch index; left unchanged (CI uses it). Locally torch/torchvision 2.3.0 come from PyPI and requirements are installed with the `+cpu`/`--extra-index-url` lines filtered. · Sandbox proxy blocks download.pytorch.org.
- 2026-11-19 · X-ray weights (`densenet121-res224-nih`, `101-elastic`) fetched from GitHub releases with `HF_HUB_OFFLINE=0`, mirroring the CI prefetch step, so the 3 weight-dependent tests run instead of skipping. · Honest P0 gate (no skips).
- 2026-11-19 · Added `scripts/dev_up.sh` to restart Postgres/Redis/API/worker after container restarts. · The container restarted once mid-P0; services do not survive it.
