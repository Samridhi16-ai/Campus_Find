# CampusFind backend (Render + PostgreSQL)

## Local development

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Without `DATABASE_URL`, the backend uses SQLite locally at `campusfind.db`.

## Render deployment

This folder includes `render.yaml` for a Render Blueprint deployment. It creates:
- a Python web service running `gunicorn app:app`
- a PostgreSQL database
- `DATABASE_URL`, `ALLOWED_ORIGIN`, and a generated `ADMIN_TOKEN`

The allowed frontend origin is `https://samridhi16-ai.github.io`.

## Health check

After deployment, open `https://YOUR-SERVICE.onrender.com/health`.
Expected response:

```json
{"ok": true, "service": "CampusFind API"}
```

## Main API endpoints

- `GET /api/listings`
- `POST /api/listings`
- `GET /api/listings/<id>`
- `GET /api/listings/<id>/matches`
- `POST /api/listings/<id>/claims`
- `GET /api/stats`

Admin endpoints require the `X-Admin-Token` header.
