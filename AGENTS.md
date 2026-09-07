# AGENTS.md

## Project

This is a React/TypeScript application.

## Rules

- Do not rewrite the application from scratch.
- Preserve the existing architecture.
- Use existing components whenever possible.
- Do not introduce dependencies without a strong reason.
- Keep changes focused.
- Do not modify environment secrets.
- Do not modify deployment configuration unless explicitly required.

## Before committing

Run:

npm install
npm run lint
npm run build

If tests exist, run them as well.

## Git

Create focused commits.

Commit format:

feat:
fix:
refactor:
perf:
test:
docs:
chore:

## Pull Requests

Every PR should explain:

1. What changed
2. Why it changed
3. Verification performed
4. Potential risks
