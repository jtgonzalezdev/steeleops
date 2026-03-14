# SteeleOps

SteeleOps is a lightweight security operations platform for guard companies that need scheduling, reporting, patrol tracking, timekeeping, and payroll-ready exports without a heavy setup.

This build is prepared for a **first private beta launch on Render** while still supporting simple **local SQLite development**.

## What is included in this beta-ready build

### Security and auth
- CSRF protection across app forms using a signed token cookie + hidden form token
- PBKDF2 password hashing
- DB-backed sessions
- Login attempt throttling
- Password reset request + token-based reset flow
- Production guardrail: when `APP_ENV=production`, SteeleOps requires PostgreSQL
- Production startup removes seeded local demo users/companies

### Storage and uploads
- Local file storage for development
- Cloud storage abstraction for production-ready uploads
- Optional S3-compatible object storage support for:
  - company logos
  - incident photos
  - report attachments

### Audit logging
- Login attempts
- Password reset requests/completions
- Admin actions
- Shift edits
- Payroll CSV exports
- PDF report exports
- Incident status/priority updates

### Existing operations features
- Multi-company structure
- Company admins and guards
- Sites and client company names
- Shift scheduling, open shifts, swaps, and time corrections
- Clock in / clock out connected to shifts
- Patrol checkpoints
- Incident and daily activity reports
- PDF report export
- Payroll-ready CSV export

## Local development

### Requirements
- Python 3.11+

### Install
```bash
cd steele_security_app
pip install -r requirements.txt
```

### SQLite dev startup
```bash
python app.py init-db
python app.py serve
```

Open:
```text
http://127.0.0.1:8000
```

When `DATABASE_URL` is not set and `APP_ENV` is not `production`, SteeleOps uses local SQLite (`steeleops.db`).

## Production behavior

In production:
- `APP_ENV=production` requires PostgreSQL
- local demo accounts are removed on startup
- secure cookies can be enabled with `SESSION_COOKIE_SECURE=1`
- uploads can use S3-compatible object storage by setting `STORAGE_BACKEND=s3`

## Commands

### Initialize database
```bash
python app.py init-db
```

### Create first company admin
```bash
python app.py create-admin \
  --company "Steele Security Services" \
  --username admin \
  --password "REPLACE_WITH_STRONG_PASSWORD" \
  --full-name "SteeleOps Company Admin" \
  --email admin@example.com
```

### Start server
```bash
python app.py serve
```

## Environment variables

Copy `.env.example` and set values for your environment.

### Core
```bash
APP_ENV=production
PORT=10000
HOST=0.0.0.0
SECRET_KEY=replace-with-a-long-random-secret
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
SESSION_COOKIE_SECURE=1
SESSION_TTL_HOURS=24
MAX_UPLOAD_MB=8
APP_BASE_URL=https://your-domain.example.com
```

### Optional S3-compatible storage
```bash
STORAGE_BACKEND=s3
S3_BUCKET=your-bucket-name
S3_REGION=us-east-1
S3_ENDPOINT_URL=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
S3_PUBLIC_BASE_URL=https://cdn.example.com
```

### Password reset
```bash
RESET_TOKEN_HOURS=2
ALLOW_BROWSER_PASSWORD_RESET_LINKS=0
```

## Docker

### Build
```bash
docker build -t steeleops .
```

### Run
```bash
docker run -p 8000:8000 \
  -e APP_ENV=production \
  -e PORT=8000 \
  -e HOST=0.0.0.0 \
  -e SECRET_KEY=replace-with-a-long-random-secret \
  -e DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME \
  -e SESSION_COOKIE_SECURE=1 \
  steeleops
```

## Render deployment

A `render.yaml` Blueprint file is included for a web service + Render Postgres database.

### Recommended Render flow
1. Push the `steele_security_app` folder to a Git repo.
2. Keep `render.yaml` in the repository root used by Render.
3. Create a new Blueprint deployment in Render.
4. Set secrets such as `SECRET_KEY` and your object storage credentials.
5. After the first deploy, run:
   - `python app.py init-db`
   - `python app.py create-admin ...`
6. Log in with that admin and complete company branding/settings.

### Render service notes
- Runtime: Docker
- Health path: `/login`
- Database: Render Postgres via `DATABASE_URL`
- Object storage: use S3-compatible storage for persistent uploads

## Railway

Railway is still supported with the same environment variables:
- add PostgreSQL
- set `APP_ENV=production`
- set `DATABASE_URL`
- run `python app.py init-db`
- create the first admin with `python app.py create-admin ...`

## Production checklist

### Required environment variables
- `APP_ENV=production`
- `SECRET_KEY`
- `DATABASE_URL`
- `SESSION_COOKIE_SECURE=1`
- `APP_BASE_URL`
- object storage variables if using `STORAGE_BACKEND=s3`

### Database migration / init steps
1. Deploy service and PostgreSQL.
2. Run:
   ```bash
   python app.py init-db
   ```
3. Verify tables were created successfully.

### Admin account creation
1. Run:
   ```bash
   python app.py create-admin \
     --company "Your Company Name" \
     --username admin \
     --password "YOUR_STRONG_PASSWORD" \
     --full-name "Admin Name" \
     --email admin@yourcompany.com
   ```
2. Confirm login at `/login`.

### Backup notes
- Render Postgres backups should be enabled and monitored at the database/service level.
- For uploads, use S3-compatible storage with its own bucket lifecycle and backup policy.
- Keep a periodic export of payroll CSVs and critical reports for business continuity.

### Domain / HTTPS steps
1. Add your custom domain in Render.
2. Set `APP_BASE_URL` to the final HTTPS domain.
3. Keep `SESSION_COOKIE_SECURE=1` in production.
4. Verify login, password reset links, and uploaded file URLs all resolve over HTTPS.

## Files included for deployment
- `Dockerfile`
- `.dockerignore`
- `requirements.txt`
- `.env.example`
- `render.yaml`

## Private beta notes
- For a no-email private beta, you can temporarily set `ALLOW_BROWSER_PASSWORD_RESET_LINKS=1` so SteeleOps shows one-time reset links in the browser after a valid request.
- For normal production behavior, leave that value at `0` and wire the reset token to your email workflow later.
