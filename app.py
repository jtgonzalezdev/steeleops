import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from base64 import b64encode
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, quote_plus, unquote_plus, urlencode
from wsgiref.simple_server import make_server

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None

from jinja2 import Environment, FileSystemLoader, select_autoescape
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'steeleops.db')
RENDER_DISK_PATH = (os.getenv('RENDER_DISK_PATH') or '').strip()
UPLOAD_DIR_ENV = (os.getenv('UPLOAD_DIR') or '').strip()
if UPLOAD_DIR_ENV:
    UPLOAD_ROOT = os.path.abspath(UPLOAD_DIR_ENV)
elif RENDER_DISK_PATH:
    UPLOAD_ROOT = os.path.abspath(os.path.join(RENDER_DISK_PATH, 'uploads'))
else:
    UPLOAD_ROOT = os.path.abspath(os.path.join(BASE_DIR, 'uploads'))
UPLOAD_DIR = UPLOAD_ROOT
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
STATIC_DIR = os.path.join(BASE_DIR, 'static')
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

APP_ENV = os.getenv('APP_ENV', 'development').lower()
DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
PORT = int(os.getenv('PORT', '10000'))
HOST = os.getenv('HOST', '0.0.0.0')
SECRET_KEY = os.getenv('SECRET_KEY', 'change-me-in-production')
SESSION_COOKIE_NAME = os.getenv('SESSION_COOKIE_NAME', 'steeleops_session')
SESSION_COOKIE_SECURE = os.getenv('SESSION_COOKIE_SECURE', '1' if APP_ENV == 'production' else '0') == '1'
SESSION_TTL_HOURS = int(os.getenv('SESSION_TTL_HOURS', '24'))
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '8'))
MISSED_CLOCK_INTERNAL_TOKEN = os.getenv('MISSED_CLOCK_INTERNAL_TOKEN', '').strip()
USE_POSTGRES = DATABASE_URL.startswith('postgres://') or DATABASE_URL.startswith('postgresql://')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = 'postgresql://' + DATABASE_URL[len('postgres://'):]

BOOTSTRAP_ADMIN_USERNAME = os.getenv('BOOTSTRAP_ADMIN_USERNAME', '').strip()
BOOTSTRAP_ADMIN_PASSWORD = os.getenv('BOOTSTRAP_ADMIN_PASSWORD', '').strip()
BOOTSTRAP_ADMIN_EMAIL = os.getenv('BOOTSTRAP_ADMIN_EMAIL', '').strip()

TEMP_ADMIN_RESET_PASSWORD = os.getenv('TEMP_ADMIN_RESET_PASSWORD', 'Admin123!')
TEMP_ADMIN_RESET_MARKER = os.path.join(BASE_DIR, '.temp_admin_password_reset_done')

# Branding defaults keep SteeleOps as the product/platform while keeping
# Steele Security Services as the operating company/user brand. Avoid
# provider-as-sponsor language because it reverses that hierarchy.
PRODUCT_SHORT_NAME = os.getenv('PRODUCT_SHORT_NAME', 'SteeleOps').strip() or 'SteeleOps'
PRODUCT_FULL_NAME = os.getenv('PRODUCT_FULL_NAME', 'SteeleOps Control Center').strip() or 'SteeleOps Control Center'
PROVIDER_BRAND_NAME = os.getenv('PROVIDER_BRAND_NAME', 'Steele Security Services').strip() or 'Steele Security Services'
BRAND_SUBTITLE = os.getenv('BRAND_SUBTITLE', f'Built for {PROVIDER_BRAND_NAME}').strip() or f'Built for {PROVIDER_BRAND_NAME}'

PROVIDER_SHIELD_LOGO_FILENAME = 'steele-security-shield.svg'
PROVIDER_SHIELD_LOGO_URL = f'/static/{PROVIDER_SHIELD_LOGO_FILENAME}'
PROVIDER_SHIELD_LOGO_SVG = r'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 160" role="img" aria-labelledby="title desc">
  <title id="title">Steele Security Services shield logo</title>
  <desc id="desc">Black, red, and silver shield mark with an S monogram.</desc>
  <defs>
    <linearGradient id="shield" x1="18" y1="8" x2="110" y2="148" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#ef4444"/>
      <stop offset="0.45" stop-color="#b91c1c"/>
      <stop offset="1" stop-color="#111111"/>
    </linearGradient>
    <linearGradient id="edge" x1="25" y1="15" x2="103" y2="139" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.9"/>
      <stop offset="1" stop-color="#c0c0c0" stop-opacity="0.58"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-15%" width="140%" height="135%">
      <feDropShadow dx="0" dy="10" stdDeviation="8" flood-color="#000000" flood-opacity="0.36"/>
    </filter>
  </defs>
  <path d="M64 6 112 22l-7 73c-3 31-22 45-41 59-19-14-38-28-41-59l-7-73L64 6Z" fill="url(#shield)" filter="url(#shadow)"/>
  <path d="M64 16 101 29l-6 63c-2 24-16 36-31 47-15-11-29-23-31-47l-6-63 37-13Z" fill="none" stroke="url(#edge)" stroke-width="6" stroke-linejoin="round"/>
  <path d="M76 54c-4-5-10-8-18-8-11 0-19 6-19 15 0 22 49 10 49 39 0 14-12 24-29 24-13 0-24-5-31-14l11-10c5 7 12 10 21 10 8 0 14-4 14-10 0-15-49-8-49-38 0-18 15-29 34-29 12 0 22 4 29 12L76 54Z" fill="#ffffff"/>
  <path d="M64 26v107" stroke="#c0c0c0" stroke-width="4" stroke-linecap="round" opacity="0.28"/>
</svg>
'''

env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(['html']))


def bootstrap_initial_admin(conn, now):
    if not BOOTSTRAP_ADMIN_USERNAME or not BOOTSTRAP_ADMIN_PASSWORD:
        return False

    company_row = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()
    if not company_row:
        conn.execute(
            'INSERT INTO companies (name, tagline, created_at) VALUES (?, ?, ?)',
            (PROVIDER_BRAND_NAME, 'Security Operations Simplified', now),
        )
        company_row = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()

    existing = conn.execute('SELECT id FROM users WHERE username=?', (BOOTSTRAP_ADMIN_USERNAME,)).fetchone()
    password_hash = hash_password(BOOTSTRAP_ADMIN_PASSWORD)
    if existing:
        conn.execute(
            """
            UPDATE users
            SET company_id=?, password=?, role='company_admin', active=1
            WHERE id=?
            """,
            (company_row['id'], password_hash, existing['id']),
        )
    else:
        conn.execute(
            """
            INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at)
            VALUES (?, ?, ?, ?, 'company_admin', ?, ?, ?, ?, 1, ?)
            """,
            (company_row['id'], BOOTSTRAP_ADMIN_USERNAME, password_hash, 'Bootstrap Admin', '', BOOTSTRAP_ADMIN_EMAIL, '', 0, now),
        )
    print(f'Bootstrap admin credentials repaired for {BOOTSTRAP_ADMIN_USERNAME}')
    return True



def reset_admin_password_once(conn):
    if os.path.exists(TEMP_ADMIN_RESET_MARKER):
        return False

    targets = conn.execute(
        "SELECT id, username, role FROM users WHERE username=? OR role=?",
        ('jtadmin', 'admin')
    ).fetchall()

    if not targets:
        print('Temporary admin password reset skipped; no matching users found')
        return False

    new_hash = hash_password(TEMP_ADMIN_RESET_PASSWORD)
    for target in targets:
        conn.execute('UPDATE users SET password=? WHERE id=?', (new_hash, target['id']))

    with open(TEMP_ADMIN_RESET_MARKER, 'w', encoding='utf-8') as marker_file:
        marker_file.write(utc_now_str())

    print(f"Temporary admin password reset applied for {len(targets)} user(s)")
    return True


def repair_admin_account(conn):
    print('Repair admin route called')
    if not BOOTSTRAP_ADMIN_USERNAME or not BOOTSTRAP_ADMIN_PASSWORD or not BOOTSTRAP_ADMIN_EMAIL:
        raise ValueError('BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_PASSWORD, and BOOTSTRAP_ADMIN_EMAIL must be set.')

    company_row = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()
    if not company_row:
        now = utc_now_str()
        conn.execute(
            'INSERT INTO companies (name, tagline, created_at) VALUES (?, ?, ?)',
            (PROVIDER_BRAND_NAME, 'Security Operations Simplified', now),
        )
        company_row = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()

    existing = conn.execute(
        """
        SELECT * FROM users
        WHERE role IN ('superadmin', 'company_admin', 'admin')
           OR username=?
           OR email=?
        ORDER BY id
        LIMIT 1
        """,
        ('jtadmin', BOOTSTRAP_ADMIN_EMAIL),
    ).fetchone()

    now = utc_now_str()
    password_hash = hash_password(BOOTSTRAP_ADMIN_PASSWORD)
    if existing:
        conn.execute(
            """
            UPDATE users
            SET company_id=?, username=?, email=?, password=?, role='company_admin', active=1
            WHERE id=?
            """,
            (company_row['id'], BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_EMAIL, password_hash, existing['id']),
        )
        print('Admin repaired')
        return 'Admin repaired'

    conn.execute(
        """
        INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at)
        VALUES (?, ?, ?, ?, 'company_admin', ?, ?, ?, ?, 1, ?)
        """,
        (company_row['id'], BOOTSTRAP_ADMIN_USERNAME, password_hash, 'Bootstrap Admin', '', BOOTSTRAP_ADMIN_EMAIL, '', 0, now),
    )
    print('Admin created')
    return 'Admin created'


def quickbooks_base_url():
    qb_env = os.getenv('QUICKBOOKS_ENV', 'sandbox').strip().lower()
    return 'https://sandbox-quickbooks.api.intuit.com' if qb_env == 'sandbox' else 'https://quickbooks.api.intuit.com'


def quickbooks_token_url():
    qb_env = os.getenv('QUICKBOOKS_ENV', 'sandbox').strip().lower()
    return 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer' if qb_env == 'sandbox' else 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'


def exchange_quickbooks_code_for_tokens(code, redirect_uri):
    client_id = os.getenv('QUICKBOOKS_CLIENT_ID', '').strip()
    client_secret = os.getenv('QUICKBOOKS_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret or not redirect_uri:
        raise ValueError('QuickBooks OAuth configuration is incomplete.')
    payload = urllib.parse.urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
    }).encode('utf-8')
    auth = b64encode(f'{client_id}:{client_secret}'.encode('utf-8')).decode('utf-8')
    req = urllib.request.Request(quickbooks_token_url(), data=payload, method='POST')
    req.add_header('Authorization', f'Basic {auth}')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode('utf-8'))


def refresh_quickbooks_token(refresh_token):
    client_id = os.getenv('QUICKBOOKS_CLIENT_ID', '').strip()
    client_secret = os.getenv('QUICKBOOKS_CLIENT_SECRET', '').strip()
    payload = urllib.parse.urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
    }).encode('utf-8')
    auth = b64encode(f'{client_id}:{client_secret}'.encode('utf-8')).decode('utf-8')
    req = urllib.request.Request(quickbooks_token_url(), data=payload, method='POST')
    req.add_header('Authorization', f'Basic {auth}')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode('utf-8'))


def get_valid_quickbooks_token(company_id):
    conn = db()
    company = conn.execute('SELECT id, qb_access_token, qb_refresh_token, qb_realm_id, qb_expires_at FROM companies WHERE id=?', (company_id,)).fetchone()
    if not company or not company.get('qb_access_token') or not company.get('qb_refresh_token'):
        conn.close()
        raise ValueError('QuickBooks is not connected for this company.')
    expires_at = company.get('qb_expires_at')
    if expires_at:
        try:
            expiry = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except Exception:
            expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    else:
        expiry = datetime.now(timezone.utc) - timedelta(seconds=1)
    if expiry <= (datetime.now(timezone.utc) + timedelta(minutes=2)):
        token_data = refresh_quickbooks_token(company['qb_refresh_token'])
        new_access = token_data.get('access_token')
        new_refresh = token_data.get('refresh_token') or company['qb_refresh_token']
        expires_in = int(token_data.get('expires_in') or 3600)
        new_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            'UPDATE companies SET qb_access_token=?, qb_refresh_token=?, qb_expires_at=?, qb_connected_at=? WHERE id=?',
            (new_access, new_refresh, new_expires_at, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), company_id),
        )
        conn.commit()
        company['qb_access_token'] = new_access
        company['qb_refresh_token'] = new_refresh
    conn.close()
    return company['qb_access_token']


def quickbooks_fetch_company_info(company_id):
    conn = db()
    company = conn.execute('SELECT qb_realm_id FROM companies WHERE id=?', (company_id,)).fetchone()
    realm_id = (company.get('qb_realm_id') if company else '') or ''
    conn.close()
    if not realm_id:
        raise ValueError('QuickBooks realmId is missing.')
    access_token = get_valid_quickbooks_token(company_id)
    url = f"{quickbooks_base_url()}/v3/company/{realm_id}/companyinfo/{realm_id}"
    req = urllib.request.Request(url, method='GET')
    req.add_header('Authorization', f'Bearer {access_token}')
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data.get('CompanyInfo', {})


def ensure_assets():
    templates = {
        'layout.html': LAYOUT_HTML,
        'app_shell.html': APP_SHELL_HTML,
        'login.html': LOGIN_HTML,
        'dashboard.html': DASHBOARD_HTML, 'admin_company_logo.html': ADMIN_COMPANY_LOGO_HTML,
        'patrols.html': PATROLS_HTML,
        'schedule.html': SCHEDULE_HTML,
        'guards.html': GUARDS_HTML,
        'patrol_run.html': PATROL_RUN_HTML, 'patrol_tour.html': PATROL_TOUR_HTML, 'reports.html': REPORTS_HTML,
        'payroll.html': PAYROLL_HTML,
        'profile.html': PROFILE_HTML,
        'admin_paystub_upload.html': ADMIN_PAYSTUB_UPLOAD_HTML,
        'guard_paystubs.html': GUARD_PAYSTUBS_HTML,
        'guard_daily_activity_reports.html': GUARD_DAILY_ACTIVITY_REPORTS_HTML,
        'guard_incident_reports.html': GUARD_INCIDENT_REPORTS_HTML,
        'guard_my_reports.html': GUARD_MY_REPORTS_HTML,
        'guard_my_report_detail.html': GUARD_MY_REPORT_DETAIL_HTML,
    }
    for name, content in templates.items():
        path = os.path.join(TEMPLATE_DIR, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
    with open(os.path.join(STATIC_DIR, 'styles.css'), 'w', encoding='utf-8') as f:
        f.write(STYLES_CSS)
    with open(os.path.join(STATIC_DIR, PROVIDER_SHIELD_LOGO_FILENAME), 'w', encoding='utf-8') as f:
        f.write(PROVIDER_SHIELD_LOGO_SVG)



def normalize_sql(sql):
    return sql.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'SERIAL PRIMARY KEY').replace('AUTOINCREMENT', '')


class CursorWrapper:
    def __init__(self, cursor, backend):
        self.cursor = cursor
        self.backend = backend

    def _sql(self, sql):
        return sql.replace('?', '%s') if self.backend == 'postgres' else sql

    def execute(self, sql, params=None):
        self.cursor.execute(self._sql(sql), params or ())
        return self

    def executemany(self, sql, seq):
        self.cursor.executemany(self._sql(sql), seq)
        return self

    def executescript(self, script):
        if self.backend == 'postgres':
            for stmt in [s.strip() for s in script.split(';') if s.strip()]:
                self.cursor.execute(normalize_sql(stmt))
        else:
            self.cursor.executescript(script)
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def __iter__(self):
        return iter(self.cursor)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class ConnectionWrapper:
    def __init__(self, conn, backend):
        self.conn = conn
        self.backend = backend

    def cursor(self):
        return CursorWrapper(self.conn.cursor(), self.backend)

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params or ())
        return cur

    def executemany(self, sql, seq):
        cur = self.cursor()
        cur.executemany(sql, seq)
        return cur

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        return self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)

def db():
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError('psycopg2-binary is required when DATABASE_URL points to PostgreSQL.')
        raw = psycopg2.connect(DATABASE_URL, sslmode='require', cursor_factory=psycopg2.extras.RealDictCursor)
        raw.autocommit = False
        return ConnectionWrapper(raw, 'postgres')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return ConnectionWrapper(conn, 'sqlite')


def fetch_scalar(conn, query, params=(), column='cnt', default=0):
    row = conn.execute(query, params).fetchone()
    if not row:
        return default
    try:
        return row[column]
    except Exception:
        try:
            return row[0]
        except Exception:
            return default


def table_exists(conn, name):
    if conn.backend == 'postgres':
        row = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=?", (name,)).fetchone()
    else:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)


def column_names(conn, table):
    if conn.backend == 'postgres':
        rows = conn.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema='public' AND table_name=?", (table,)).fetchall()
    else:
        rows = conn.execute(f'PRAGMA table_info({table})').fetchall()
    return {row['name'] for row in rows}


def ensure_column(conn, table, column_def):
    col = column_def.split()[0]
    if table_exists(conn, table) and col not in column_names(conn, table):
        cleaned = column_def.replace('INTEGER PRIMARY KEY AUTOINCREMENT', 'INTEGER').replace('AUTOINCREMENT', '').replace("CHECK(role IN ('superadmin', 'company_admin', 'supervisor', 'guard'))", '')
        default_source_col = None
        # Cross-column defaults such as "DEFAULT user_id" are not portable and
        # PostgreSQL rejects them during ALTER TABLE ... ADD COLUMN. Strip that
        # default and backfill after the column is created instead.
        default_match = re.search(r'\s+DEFAULT\s+([A-Za-z_][A-Za-z0-9_]*)\b', cleaned)
        if default_match and default_match.group(1).lower() not in {'true', 'false', 'null', 'current_date', 'current_time', 'current_timestamp'}:
            default_source_col = default_match.group(1)
            cleaned = cleaned[:default_match.start()] + cleaned[default_match.end():]
        if conn.backend == 'postgres':
            cleaned = cleaned.replace('REAL', 'DOUBLE PRECISION')
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {cleaned}')
        if default_source_col and default_source_col in column_names(conn, table):
            conn.execute(f'UPDATE {table} SET {col}={default_source_col} WHERE {col} IS NULL')


def build_guard_name(full_name='', first_name='', last_name=''):
    full_name = (full_name or '').strip()
    if full_name:
        return full_name
    first_name = (first_name or '').strip()
    last_name = (last_name or '').strip()
    built_name = ' '.join(part for part in (first_name, last_name) if part).strip()
    return built_name or 'Guard'


def guard_name_parts(full_name='', first_name='', last_name=''):
    name = build_guard_name(full_name=full_name, first_name=first_name, last_name=last_name)
    parts = name.split(' ', 1)
    return name, parts[0], parts[1] if len(parts) > 1 else ''


def insert_guard(conn, company_id, full_name='', first_name='', last_name='', phone='', email='', license_number='', status='active', rating=5, training_status='', created_at=''):
    guard_name, guard_first_name, guard_last_name = guard_name_parts(full_name=full_name, first_name=first_name, last_name=last_name)
    params = [company_id]
    columns = ['company_id']
    values = ['?']
    if 'name' in column_names(conn, 'guards'):
        columns.append('name')
        values.append('?')
        params.append(guard_name)
    columns.extend(['first_name', 'last_name', 'phone', 'email', 'license_number', 'status', 'rating', 'training_status', 'created_at'])
    values.extend(['?', '?', '?', '?', '?', '?', '?', '?', '?'])
    params.extend([guard_first_name, guard_last_name, (phone or '').strip(), (email or '').strip(), (license_number or '').strip(), status, rating, (training_status or '').strip(), created_at])
    conn.execute(f"INSERT INTO guards ({', '.join(columns)}) VALUES ({', '.join(values)})", tuple(params))
    return guard_name, guard_first_name, guard_last_name

def sync_shift_assignment_schema(conn):
    if not table_exists(conn, 'shifts'):
        return
    cols = column_names(conn, 'shifts')
    has_user_id = 'user_id' in cols
    has_guard_id = 'guard_id' in cols
    if not has_user_id and not has_guard_id:
        return
    if has_user_id and not has_guard_id:
        ensure_column(conn, 'shifts', 'guard_id INTEGER')
        conn.execute('UPDATE shifts SET guard_id=user_id WHERE guard_id IS NULL')
        cols = column_names(conn, 'shifts')
        has_guard_id = 'guard_id' in cols
    if has_guard_id and not has_user_id:
        ensure_column(conn, 'shifts', 'user_id INTEGER')
        conn.execute('UPDATE shifts SET user_id=guard_id WHERE user_id IS NULL')
        cols = column_names(conn, 'shifts')
        has_user_id = 'user_id' in cols
    if has_user_id and has_guard_id:
        conn.execute('UPDATE shifts SET guard_id=user_id WHERE user_id IS NOT NULL AND guard_id IS NULL')
        conn.execute('UPDATE shifts SET user_id=guard_id WHERE guard_id IS NOT NULL AND user_id IS NULL')


def shift_assignment_columns(conn):
    if not table_exists(conn, 'shifts'):
        return []
    cols = column_names(conn, 'shifts')
    ordered = []
    if 'user_id' in cols:
        ordered.append('user_id')
    if 'guard_id' in cols and 'guard_id' not in ordered:
        ordered.append('guard_id')
    return ordered


def shift_assignment_value(row):
    if not row:
        return None
    for key in ('user_id', 'guard_id'):
        try:
            value = row[key]
        except Exception:
            value = None
        if value is not None:
            return value
    return None


def shift_insert_sql_and_params(conn, base_columns, base_values, assigned_user_id=None):
    columns = list(base_columns)
    values = list(base_values)
    for col in shift_assignment_columns(conn):
        columns.append(col)
        values.append(assigned_user_id)
    placeholders = ', '.join(['?'] * len(columns))
    sql = f"INSERT INTO shifts ({', '.join(columns)}) VALUES ({placeholders})"
    return sql, tuple(values)


def shift_assignment_update_clause(conn):
    cols = shift_assignment_columns(conn)
    if not cols:
        return '', []
    return ', '.join([f"{col}=?" for col in cols]), cols


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000)
    return f'{salt}${digest.hex()}'


def verify_password(password, stored):
    if not stored:
        return False
    if '$' not in stored:
        return hmac.compare_digest(stored, password)
    salt, digest = stored.split('$', 1)
    candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 120000).hex()
    return hmac.compare_digest(candidate, digest)


def normalize_pin(pin):
    return ''.join(ch for ch in (pin or '').strip() if ch.isdigit())


def is_valid_pin(pin):
    return len(pin) == 4 and pin.isdigit()


def guard_pin_value(data):
    requested_pin = normalize_pin(data.get('pin'))
    if not requested_pin and data.get('generate_pin'):
        requested_pin = ''.join(secrets.choice('0123456789') for _ in range(4))
    return requested_pin


def guard_login_payload(data, guard, company_id):
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip()
    temporary_password = data.get('temporary_password') or ''
    pin = guard_pin_value(data)
    has_login_fields = any([username, email, temporary_password, pin])
    full_name = ('%s %s' % ((guard.get('first_name') or '').strip(), (guard.get('last_name') or '').strip())).strip() or 'Guard'
    return {
        'username': username,
        'email': email,
        'temporary_password': temporary_password,
        'pin': pin,
        'has_login_fields': has_login_fields,
        'full_name': full_name,
        'company_id': company_id,
    }


def validate_guard_login_payload(conn, payload, current_user_id=None, login_exists=False):
    if payload['has_login_fields'] or login_exists:
        if not payload['username']:
            raise ValueError('Username is required for a guard login')
        existing_username = conn.execute('SELECT id FROM users WHERE username=?', (payload['username'],)).fetchone()
        if existing_username and existing_username['id'] != current_user_id:
            raise ValueError('Username must be unique')
        if payload['email']:
            existing_email = conn.execute('SELECT id FROM users WHERE email=?', (payload['email'],)).fetchone()
            if existing_email and existing_email['id'] != current_user_id:
                raise ValueError('Email must be unique')
        if not login_exists and not payload['temporary_password']:
            raise ValueError('Temporary password is required to create a guard login')
        if payload['pin'] and not is_valid_pin(payload['pin']):
            raise ValueError('PIN must be exactly 4 digits')


def upsert_guard_login(conn, guard, payload):
    matching_users = conn.execute(
        "SELECT * FROM users WHERE company_id=? AND role='guard' AND guard_id=? ORDER BY id",
        (payload['company_id'], guard['id'])
    ).fetchall()
    if len(matching_users) > 1:
        raise ValueError('Duplicate user accounts already exist for this guard')
    existing_user = matching_users[0] if matching_users else None
    validate_guard_login_payload(conn, payload, current_user_id=existing_user['id'] if existing_user else None, login_exists=bool(existing_user))
    if not payload['has_login_fields'] and not existing_user:
        return None, False

    user_email = payload['email']
    user_phone = guard['phone'] or ''
    user_license = guard['license_number'] or ''
    active = 0 if guard['status'] == 'inactive' else 1

    if existing_user:
        conn.execute(
            'UPDATE users SET company_id=?, full_name=?, username=?, email=?, phone=?, license_number=?, active=? WHERE id=?',
            (payload['company_id'], payload['full_name'], payload['username'], user_email, user_phone, user_license, active, existing_user['id'])
        )
        if payload['temporary_password']:
            conn.execute('UPDATE users SET password=? WHERE id=?', (hash_password(payload['temporary_password']), existing_user['id']))
        if payload['pin']:
            conn.execute('UPDATE users SET pin_hash=? WHERE id=?', (hash_password(payload['pin']), existing_user['id']))
        return existing_user['id'], False

    conn.execute(
        """
        INSERT INTO users (company_id, guard_id, username, password, pin_hash, full_name, role, phone, email, license_number, hourly_rate, active, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'guard', ?, ?, ?, ?, ?, ?)
        """,
        (
            payload['company_id'], guard['id'], payload['username'], hash_password(payload['temporary_password']), hash_password(payload['pin']) if payload['pin'] else None, payload['full_name'],
            user_phone, user_email, user_license, 18, active, utc_now_str()
        )
    )
    new_row = conn.execute('SELECT id FROM users WHERE username=?', (payload['username'],)).fetchone()
    new_id = new_row['id'] if new_row else None
    if new_id:
        for weekday in range(7):
            conn.execute("INSERT INTO availability (company_id, user_id, weekday, available_start, available_end, is_available) VALUES (?, ?, ?, '08:00', '20:00', 1)", (payload['company_id'], new_id, weekday))
    return new_id, True

def now_utc():
    return datetime.now(timezone.utc)


def utc_now_str():
    return now_utc().strftime('%Y-%m-%d %H:%M:%S')


def expires_at(hours=SESSION_TTL_HOURS):
    return (now_utc() + timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')


def cookie_expires_gmt(expires):
    if expires.tzinfo is None:
        raise ValueError('cookie expiration datetime must be timezone-aware UTC')
    return expires.astimezone(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')

def cookie_header(session_id, expires=None):
    parts = [f'{SESSION_COOKIE_NAME}={session_id}', 'Path=/', 'HttpOnly', 'SameSite=Lax']
    if SESSION_COOKIE_SECURE:
        parts.append('Secure')
    if expires:
        parts.append('Expires=' + cookie_expires_gmt(expires))
    return '; '.join(parts)


def delete_cookie_header():
    return cookie_header('deleted', datetime(1970, 1, 1, tzinfo=timezone.utc))


def parse_request_cookies(environ):
    cookies = {}
    raw = environ.get('HTTP_COOKIE', '')
    for part in raw.split(';'):
        if '=' in part:
            k, v = part.strip().split('=', 1)
            cookies[k] = v
    return cookies


def qb_state_cookie_header(state, expires=None):
    parts = [f'qb_oauth_state={state}', 'Path=/', 'HttpOnly', 'SameSite=Lax']
    if SESSION_COOKIE_SECURE:
        parts.append('Secure')
    if expires:
        parts.append('Expires=' + cookie_expires_gmt(expires))
    return '; '.join(parts)


def qb_delete_state_cookie_header():
    return qb_state_cookie_header('deleted', datetime(1970, 1, 1, tzinfo=timezone.utc))


def response_headers(extra=None, content_type='text/html; charset=utf-8'):
    headers = [('Content-Type', content_type), ('X-Frame-Options', 'DENY'), ('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'same-origin')]
    if APP_ENV == 'production':
        headers.append(('Content-Security-Policy', "default-src 'self' 'unsafe-inline' data:; img-src 'self' data:; style-src 'self' 'unsafe-inline'"))
    if extra:
        headers.extend(extra)
    return headers


def guard_primary_assigned_site(conn, user, preferred_site_id=None):
    if not user or row_value(user, 'role') != 'guard':
        return None
    guard_row_id = row_value(user, 'guard_id')
    company_id = row_value(user, 'company_id')
    if not guard_row_id or not company_id:
        return None
    preferred_site_id = str(preferred_site_id or '').strip()
    if preferred_site_id.isdigit():
        preferred = conn.execute(
            '''
            SELECT s.id, s.name, s.address, COALESCE(NULLIF(c.name, ''), NULLIF(s.client_company_name, ''), '') AS client_name
            FROM guard_site_assignments gsa
            JOIN sites s ON s.id=gsa.site_id AND s.company_id=gsa.company_id
            LEFT JOIN clients c ON c.id=s.client_id AND c.company_id=s.company_id
            WHERE gsa.company_id=? AND gsa.guard_id=? AND gsa.site_id=? AND COALESCE(s.active,1)=1
            ORDER BY gsa.assigned_at DESC, gsa.id DESC
            LIMIT 1
            ''',
            (company_id, guard_row_id, int(preferred_site_id)),
        ).fetchone()
        if preferred:
            return preferred
    return conn.execute(
        '''
        SELECT s.id, s.name, s.address, COALESCE(NULLIF(c.name, ''), NULLIF(s.client_company_name, ''), '') AS client_name
        FROM guard_site_assignments gsa
        JOIN sites s ON s.id=gsa.site_id AND s.company_id=gsa.company_id
        LEFT JOIN clients c ON c.id=s.client_id AND c.company_id=s.company_id
        WHERE gsa.company_id=? AND gsa.guard_id=? AND COALESCE(s.active,1)=1
        ORDER BY gsa.assigned_at DESC, gsa.id DESC
        LIMIT 1
        ''',
        (company_id, guard_row_id),
    ).fetchone()


def guard_primary_assigned_site_id(conn, user, preferred_site_id=None):
    site = guard_primary_assigned_site(conn, user, preferred_site_id=preferred_site_id)
    return site['id'] if site else None


def set_session_site(session_id, site_id):
    if not session_id:
        return
    conn = db()
    try:
        if table_exists(conn, 'sessions') and 'site_id' in column_names(conn, 'sessions'):
            conn.execute('UPDATE sessions SET site_id=? WHERE id=?', (site_id, session_id))
            conn.commit()
    finally:
        conn.close()


def create_session(user_id, company_id=None, site_id=None, role=None):
    sid = secrets.token_urlsafe(32)
    conn = db()
    if site_id is None or company_id is None or role is None:
        user = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if user:
            company_id = company_id if company_id is not None else row_value(user, 'company_id')
            role = role if role is not None else row_value(user, 'role')
            if site_id is None and row_value(user, 'role') == 'guard':
                site_id = guard_primary_assigned_site_id(conn, user)
    cols = ['id', 'user_id', 'created_at', 'expires_at']
    vals = [sid, user_id, now_utc().strftime('%Y-%m-%d %H:%M:%S'), expires_at()]
    session_cols = column_names(conn, 'sessions') if table_exists(conn, 'sessions') else set()
    for col, value in [('company_id', company_id), ('site_id', site_id), ('role', role)]:
        if col in session_cols:
            cols.append(col)
            vals.append(value)
    placeholders = ', '.join(['?'] * len(cols))
    conn.execute(f"INSERT INTO sessions ({', '.join(cols)}) VALUES ({placeholders})", tuple(vals))
    conn.commit(); conn.close()
    return sid


def destroy_session(session_id):
    if not session_id:
        return
    conn = db()
    conn.execute('DELETE FROM sessions WHERE id=?', (session_id,))
    conn.commit(); conn.close()


def clear_expired_sessions():
    conn = db()
    conn.execute('DELETE FROM sessions WHERE expires_at < ?', (now_utc().strftime('%Y-%m-%d %H:%M:%S'),))
    conn.commit(); conn.close()


def login_allowed(username):
    window = now_utc() - timedelta(minutes=15)
    conn = db()
    row = conn.execute('SELECT COUNT(*) AS cnt FROM auth_attempts WHERE username=? AND success=0 AND attempted_at >= ?', (username, window.strftime('%Y-%m-%d %H:%M:%S'))).fetchone()
    conn.close()
    return (row['cnt'] if row else 0) < 5


def record_login_attempt(username, success):
    conn = db()
    conn.execute('INSERT INTO auth_attempts (username, success, attempted_at) VALUES (?, ?, ?)', (username, 1 if success else 0, now_utc().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit(); conn.close()


def init_db():
    ensure_assets()
    conn = db()
    cur = conn.cursor()
    if conn.backend == 'postgres':
        cur.executescript("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        tagline TEXT DEFAULT 'Security Operations Simplified',
        logo_path TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS guards (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        license_number TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        rating DOUBLE PRECISION DEFAULT 5,
        training_status TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id)
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER,
        guard_id INTEGER,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        pin_hash TEXT,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        license_number TEXT,
        hourly_rate DOUBLE PRECISION DEFAULT 18,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(guard_id) REFERENCES guards(id)
    );

    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        contact_name TEXT,
        contact_email TEXT,
        contact_phone TEXT,
        notes TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id)
    );

    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        client_id INTEGER,
        name TEXT NOT NULL,
        client_company_name TEXT,
        address TEXT,
        notes TEXT,
        active INTEGER DEFAULT 1,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(client_id) REFERENCES clients(id)
    );

    CREATE TABLE IF NOT EXISTS guard_site_assignments (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        guard_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(guard_id) REFERENCES guards(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );
    CREATE TABLE IF NOT EXISTS supervisor_site_assignments (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        supervisor_user_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL,
        UNIQUE(supervisor_user_id, site_id),
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(supervisor_user_id) REFERENCES users(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        user_id INTEGER,
        site_id INTEGER NOT NULL,
        shift_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        clock_in_time TEXT,
        clock_out_time TEXT,
        scheduled_hours DOUBLE PRECISION DEFAULT 0,
        worked_hours DOUBLE PRECISION DEFAULT 0,
        overtime_alert INTEGER DEFAULT 0,
        notes TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        report_type TEXT NOT NULL,
        report_date TEXT NOT NULL,
        report_time TEXT NOT NULL,
        site_id INTEGER NOT NULL,
        officer_name TEXT NOT NULL,
        summary TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        priority TEXT DEFAULT 'medium',
        attachment_name TEXT,
        attachment_path TEXT,
        photo_name TEXT,
        photo_path TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS daily_activity_reports (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        officer_id INTEGER NOT NULL,
        activity_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        photo_path TEXT,
        status TEXT NOT NULL DEFAULT 'Open',
        supervisor_notes TEXT,
        admin_notes TEXT,
        resolved_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(site_id) REFERENCES sites(id),
        FOREIGN KEY(officer_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS incident_reports (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        officer_id INTEGER NOT NULL,
        incident_type TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'Medium',
        narrative TEXT NOT NULL,
        persons_involved TEXT,
        witnesses TEXT,
        police_notified INTEGER NOT NULL DEFAULT 0,
        client_notified INTEGER NOT NULL DEFAULT 0,
        attachment_path TEXT,
        status TEXT NOT NULL DEFAULT 'Open',
        supervisor_notes TEXT,
        admin_notes TEXT,
        resolved_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(site_id) REFERENCES sites(id),
        FOREIGN KEY(officer_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS daily_activity_reports (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        officer_id INTEGER NOT NULL,
        activity_type TEXT NOT NULL,
        summary TEXT NOT NULL,
        photo_path TEXT,
        status TEXT NOT NULL DEFAULT 'Open',
        supervisor_notes TEXT,
        admin_notes TEXT,
        resolved_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(site_id) REFERENCES sites(id),
        FOREIGN KEY(officer_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS patrol_checkpoints (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        checkpoint_name TEXT NOT NULL,
        check_time TEXT NOT NULL,
        notes TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS availability (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        weekday INTEGER NOT NULL,
        available_start TEXT,
        available_end TEXT,
        is_available INTEGER DEFAULT 1,
        UNIQUE(user_id, weekday),
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS shift_swap_requests (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        requested_by INTEGER NOT NULL,
        requested_to INTEGER,
        status TEXT DEFAULT 'pending',
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(shift_id) REFERENCES shifts(id),
        FOREIGN KEY(requested_by) REFERENCES users(id),
        FOREIGN KEY(requested_to) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS time_corrections (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        requested_by INTEGER NOT NULL,
        requested_clock_in TEXT,
        requested_clock_out TEXT,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(shift_id) REFERENCES shifts(id),
        FOREIGN KEY(requested_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS time_off_requests (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        guard_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('paid', 'unpaid')),
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'denied')),
        created_at TEXT NOT NULL,
        updated_at TEXT,
        reviewed_at TEXT,
        reviewed_by INTEGER,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(guard_id) REFERENCES users(id),
        FOREIGN KEY(reviewed_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS time_off_review_logs (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        request_id INTEGER NOT NULL,
        guard_id INTEGER NOT NULL,
        reviewed_by INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'denied')),
        review_note TEXT,
        reviewed_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(request_id) REFERENCES time_off_requests(id),
        FOREIGN KEY(guard_id) REFERENCES users(id),
        FOREIGN KEY(reviewed_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS open_shift_alerts (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        company_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolved_by INTEGER,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(shift_id) REFERENCES shifts(id),
        FOREIGN KEY(resolved_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS auth_attempts (
        id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        username TEXT NOT NULL,
        success INTEGER NOT NULL,
        attempted_at TEXT NOT NULL
    );
    """)
    else:
        cur.executescript("""
    CREATE TABLE IF NOT EXISTS companies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        tagline TEXT DEFAULT 'Security Operations Simplified',
        logo_path TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS guards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        first_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        phone TEXT,
        email TEXT,
        license_number TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        rating REAL DEFAULT 5,
        training_status TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id)
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER,
        guard_id INTEGER,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        pin_hash TEXT,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('superadmin', 'company_admin', 'supervisor', 'guard')),
        phone TEXT,
        email TEXT,
        license_number TEXT,
        hourly_rate REAL DEFAULT 18,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(guard_id) REFERENCES guards(id)
    );

    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        contact_name TEXT,
        contact_email TEXT,
        contact_phone TEXT,
        notes TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id)
    );

    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        client_id INTEGER,
        name TEXT NOT NULL,
        client_company_name TEXT,
        address TEXT,
        notes TEXT,
        active INTEGER DEFAULT 1,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(client_id) REFERENCES clients(id)
    );

    CREATE TABLE IF NOT EXISTS guard_site_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        guard_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(guard_id) REFERENCES guards(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );
    CREATE TABLE IF NOT EXISTS supervisor_site_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        supervisor_user_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL,
        UNIQUE(supervisor_user_id, site_id),
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(supervisor_user_id) REFERENCES users(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS shifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        user_id INTEGER,
        site_id INTEGER NOT NULL,
        shift_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        clock_in_time TEXT,
        clock_out_time TEXT,
        scheduled_hours REAL DEFAULT 0,
        worked_hours REAL DEFAULT 0,
        overtime_alert INTEGER DEFAULT 0,
        notes TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        report_type TEXT NOT NULL,
        report_date TEXT NOT NULL,
        report_time TEXT NOT NULL,
        site_id INTEGER NOT NULL,
        officer_name TEXT NOT NULL,
        summary TEXT NOT NULL,
        status TEXT DEFAULT 'open',
        priority TEXT DEFAULT 'medium',
        attachment_name TEXT,
        attachment_path TEXT,
        photo_name TEXT,
        photo_path TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS patrol_checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        site_id INTEGER NOT NULL,
        checkpoint_name TEXT NOT NULL,
        check_time TEXT NOT NULL,
        notes TEXT,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(user_id) REFERENCES users(id),
        FOREIGN KEY(site_id) REFERENCES sites(id)
    );

    CREATE TABLE IF NOT EXISTS availability (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        weekday INTEGER NOT NULL,
        available_start TEXT,
        available_end TEXT,
        is_available INTEGER DEFAULT 1,
        UNIQUE(user_id, weekday),
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS shift_swap_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        requested_by INTEGER NOT NULL,
        requested_to INTEGER,
        status TEXT DEFAULT 'pending',
        notes TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(shift_id) REFERENCES shifts(id),
        FOREIGN KEY(requested_by) REFERENCES users(id),
        FOREIGN KEY(requested_to) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS time_corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        requested_by INTEGER NOT NULL,
        requested_clock_in TEXT,
        requested_clock_out TEXT,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(shift_id) REFERENCES shifts(id),
        FOREIGN KEY(requested_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS time_off_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id INTEGER NOT NULL,
        guard_id INTEGER NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        type TEXT NOT NULL CHECK(type IN ('paid', 'unpaid')),
        reason TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'denied')),
        created_at TEXT NOT NULL,
        updated_at TEXT,
        reviewed_at TEXT,
        reviewed_by INTEGER,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(guard_id) REFERENCES users(id),
        FOREIGN KEY(reviewed_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS time_off_review_logs (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL,
        request_id INTEGER NOT NULL,
        guard_id INTEGER NOT NULL,
        reviewed_by INTEGER NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'denied')),
        review_note TEXT,
        reviewed_at TEXT NOT NULL,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(request_id) REFERENCES time_off_requests(id),
        FOREIGN KEY(guard_id) REFERENCES users(id),
        FOREIGN KEY(reviewed_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS open_shift_alerts (
        id INTEGER PRIMARY KEY,
        company_id INTEGER NOT NULL,
        shift_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolved_by INTEGER,
        FOREIGN KEY(company_id) REFERENCES companies(id),
        FOREIGN KEY(shift_id) REFERENCES shifts(id),
        FOREIGN KEY(resolved_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS auth_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        success INTEGER NOT NULL,
        attempted_at TEXT NOT NULL
    );
    """)
    # gentle migration support for older single-company databases
    if table_exists(conn, 'users'):
        for col in [
            'company_id INTEGER', 'guard_id INTEGER', 'phone TEXT', 'email TEXT', 'license_number TEXT', 'employee_id TEXT', 'badge_id TEXT', 'hourly_rate REAL DEFAULT 18',
            'active INTEGER DEFAULT 1', 'created_at TEXT', 'pin_hash TEXT'
        ]:
            ensure_column(conn, 'users', col)
    if table_exists(conn, 'clients'):
        for col in ['company_id INTEGER', 'name TEXT', 'contact_name TEXT', 'contact_email TEXT', 'contact_phone TEXT', 'notes TEXT', 'active INTEGER DEFAULT 1', 'created_at TEXT']:
            ensure_column(conn, 'clients', col)
    if table_exists(conn, 'sites'):
        for col in ['company_id INTEGER', 'client_id INTEGER', 'client_company_name TEXT', 'active INTEGER DEFAULT 1']:
            ensure_column(conn, 'sites', col)
    if table_exists(conn, 'guards'):
        for col in ['company_id INTEGER', 'name TEXT', 'first_name TEXT', 'last_name TEXT', 'phone TEXT', 'email TEXT', 'license_number TEXT', 'employee_id TEXT', 'badge_id TEXT', "status TEXT DEFAULT 'active'", 'rating REAL DEFAULT 5', 'training_status TEXT', 'created_at TEXT']:
            ensure_column(conn, 'guards', col)
        if 'name' in column_names(conn, 'guards'):
            conn.execute("UPDATE guards SET name=COALESCE(NULLIF(TRIM(name), ''), NULLIF(TRIM(first_name || ' ' || last_name), ''), NULLIF(TRIM(first_name), ''), 'Guard') WHERE name IS NULL OR TRIM(name)=''")
    if table_exists(conn, 'guard_site_assignments'):
        for col in ['company_id INTEGER', 'guard_id INTEGER', 'site_id INTEGER', 'assigned_at TEXT']:
            ensure_column(conn, 'guard_site_assignments', col)
    if table_exists(conn, 'supervisor_site_assignments'):
        for col in ['company_id INTEGER', 'supervisor_user_id INTEGER', 'site_id INTEGER', 'assigned_at TEXT']:
            ensure_column(conn, 'supervisor_site_assignments', col)
    if table_exists(conn, 'shifts'):
        for col in ['company_id INTEGER', 'scheduled_hours REAL DEFAULT 0', 'worked_hours REAL DEFAULT 0', 'overtime_alert INTEGER DEFAULT 0', 'notes TEXT']:
            ensure_column(conn, 'shifts', col)
        sync_shift_assignment_schema(conn)
    if table_exists(conn, 'reports'):
        for col in ['company_id INTEGER', "status TEXT DEFAULT 'open'", "priority TEXT DEFAULT 'medium'", 'photo_name TEXT', 'photo_path TEXT']:
            ensure_column(conn, 'reports', col)
    if table_exists(conn, 'time_off_requests'):
        for col in ['reviewed_at TEXT', 'reviewed_by INTEGER']:
            ensure_column(conn, 'time_off_requests', col)
    if table_exists(conn, 'incident_reports'):
        for col in [
            'company_id INTEGER', 'site_id INTEGER', 'officer_id INTEGER', 'incident_type TEXT',
            "priority TEXT NOT NULL DEFAULT 'Medium'", 'narrative TEXT', 'persons_involved TEXT',
            'witnesses TEXT', 'police_notified INTEGER NOT NULL DEFAULT 0', 'client_notified INTEGER NOT NULL DEFAULT 0',
            'attachment_path TEXT', "status TEXT NOT NULL DEFAULT 'Open'", 'created_at TEXT',
            'supervisor_notes TEXT', 'admin_notes TEXT', 'resolved_at TEXT'
        ]:
            ensure_column(conn, 'incident_reports', col)
        conn.execute("UPDATE incident_reports SET status='Open' WHERE LOWER(COALESCE(status, '')) IN ('submitted', 'open')")
    if table_exists(conn, 'daily_activity_reports'):
        for col in ["status TEXT NOT NULL DEFAULT 'Open'", 'supervisor_notes TEXT', 'admin_notes TEXT', 'resolved_at TEXT']:
            ensure_column(conn, 'daily_activity_reports', col)
        conn.execute("UPDATE daily_activity_reports SET status='Open' WHERE LOWER(COALESCE(status, '')) IN ('submitted', 'open')")
    if conn.backend == 'postgres':
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_attachments (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                uploaded_by INTEGER,
                file_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                file_size INTEGER,
                created_at TEXT NOT NULL
            )
        ''')
    else:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                uploaded_by INTEGER,
                file_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                mime_type TEXT,
                file_size INTEGER,
                created_at TEXT NOT NULL
            )
        ''')
    if conn.backend == 'postgres':
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_status_history (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                report_kind TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT,
                changed_by INTEGER,
                changed_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_notes (
                id SERIAL PRIMARY KEY,
                company_id INTEGER NOT NULL,
                report_kind TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                note_text TEXT NOT NULL,
                note_type TEXT NOT NULL DEFAULT 'admin',
                created_by INTEGER,
                created_at TEXT NOT NULL
            )
        ''')
    else:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                report_kind TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT,
                changed_by INTEGER,
                changed_at TEXT NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                report_kind TEXT NOT NULL,
                report_id INTEGER NOT NULL,
                note_text TEXT NOT NULL,
                note_type TEXT NOT NULL DEFAULT 'admin',
                created_by INTEGER,
                created_at TEXT NOT NULL
            )
        ''')
    if table_exists(conn, 'report_notes'):
        ensure_column(conn, 'report_notes', "note_type TEXT NOT NULL DEFAULT 'admin'")

    patrol_pk = 'SERIAL PRIMARY KEY' if conn.backend == 'postgres' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    conn.execute(f'''CREATE TABLE IF NOT EXISTS patrol_tours (id {patrol_pk}, company_id INTEGER NOT NULL, site_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, active INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT)''')
    conn.execute(f'''CREATE TABLE IF NOT EXISTS patrol_tour_checkpoints (id {patrol_pk}, company_id INTEGER NOT NULL, tour_id INTEGER NOT NULL, site_id INTEGER NOT NULL, checkpoint_name TEXT NOT NULL, sort_order INTEGER DEFAULT 0, qr_code TEXT NOT NULL UNIQUE, nfc_tag_id TEXT NOT NULL UNIQUE, active INTEGER DEFAULT 1, created_at TEXT NOT NULL)''')
    conn.execute(f'''CREATE TABLE IF NOT EXISTS patrol_tour_runs (id {patrol_pk}, company_id INTEGER NOT NULL, site_id INTEGER NOT NULL, tour_id INTEGER NOT NULL, guard_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'in_progress', started_at TEXT NOT NULL, completed_at TEXT, missed_checkpoint_count INTEGER DEFAULT 0, notes TEXT, excused_reason TEXT, excused_note TEXT, excused_by INTEGER, excused_at TEXT)''')
    conn.execute(f'''CREATE TABLE IF NOT EXISTS patrol_checkpoint_scans (id {patrol_pk}, company_id INTEGER NOT NULL, site_id INTEGER NOT NULL, tour_id INTEGER NOT NULL, tour_run_id INTEGER NOT NULL, checkpoint_id INTEGER NOT NULL, guard_id INTEGER NOT NULL, scan_method TEXT NOT NULL, scanned_at TEXT NOT NULL, gps_latitude TEXT, gps_longitude TEXT, missed_checkpoint INTEGER DEFAULT 0)''')
    conn.execute(f'''CREATE TABLE IF NOT EXISTS patrol_tour_run_events (id {patrol_pk}, company_id INTEGER NOT NULL, tour_run_id INTEGER NOT NULL, event_type TEXT NOT NULL, event_label TEXT NOT NULL, event_note TEXT, reason TEXT, actor_user_id INTEGER, created_at TEXT NOT NULL)''')

    now = utc_now_str()
    bootstrap_created = bootstrap_initial_admin(conn, now)
    if not bootstrap_created and fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM companies') == 0:
        conn.execute('INSERT INTO companies (name, tagline, created_at) VALUES (?, ?, ?)', (PROVIDER_BRAND_NAME, 'Security Operations Simplified', now))
        conn.execute('INSERT INTO companies (name, tagline, created_at) VALUES (?, ?, ?)', ('BlueLine Protective', 'Security Operations Simplified', now))

    old_demo_company_row = conn.execute("SELECT id FROM companies WHERE name='SteeleOps Demo'").fetchone()
    provider_company_row = conn.execute("SELECT id FROM companies WHERE name=?", (PROVIDER_BRAND_NAME,)).fetchone()
    if old_demo_company_row and provider_company_row:
        conn.execute('UPDATE users SET company_id=? WHERE company_id=?', (provider_company_row['id'], old_demo_company_row['id']))
        conn.execute('DELETE FROM companies WHERE id=?', (old_demo_company_row['id'],))
    elif old_demo_company_row:
        conn.execute('UPDATE companies SET name=? WHERE id=?', (PROVIDER_BRAND_NAME, old_demo_company_row['id']))
    demo_company_row = conn.execute("SELECT id FROM companies WHERE name=?", (PROVIDER_BRAND_NAME,)).fetchone()
    other_company_row = conn.execute("SELECT id FROM companies WHERE name='BlueLine Protective'").fetchone()
    demo_company = demo_company_row['id'] if demo_company_row else None
    other_company = other_company_row['id'] if other_company_row else None

    if not bootstrap_created and fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM users') == 0:
        users = [
            (None, 'superadmin', hash_password('admin123'), 'Platform Admin', 'superadmin', '', 'platform@steeleops.local', '', 0, 1, now),
            (demo_company, 'admin', hash_password('admin123'), 'Steele Security Admin', 'company_admin', '210-555-0101', 'admin@demo.local', 'ADM-100', 28, 1, now),
            (demo_company, 'guard1', hash_password('guard123'), 'Marcus Hill', 'guard', '210-555-0199', 'marcus@demo.local', 'TX-2201', 18, 1, now),
            (demo_company, 'guard2', hash_password('guard123'), 'Ava Carter', 'guard', '210-555-0188', 'ava@demo.local', 'TX-2202', 18.5, 1, now),
            (other_company, 'demoadmin', hash_password('admin123'), 'BlueLine Admin', 'company_admin', '830-555-0112', 'admin@blueline.local', 'ADM-200', 27, 1, now),
        ]
        conn.executemany('''
            INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', users)
    elif demo_company is not None:
        # assign missing company ids to demo company and hash plain passwords if needed
        rows = conn.execute('SELECT id, password, company_id, role FROM users').fetchall()
        for row in rows:
            pwd = row['password']
            if '$' not in pwd:
                conn.execute('UPDATE users SET password=? WHERE id=?', (hash_password(pwd), row['id']))
            if row['company_id'] is None and row['role'] != 'superadmin':
                conn.execute('UPDATE users SET company_id=? WHERE id=?', (demo_company, row['id']))
            if row['role'] == 'admin':
                conn.execute("UPDATE users SET role='company_admin' WHERE id=?", (row['id'],))

    if demo_company is not None:
        supervisor_user = conn.execute(
            "SELECT id FROM users WHERE username=? OR email=? ORDER BY id LIMIT 1",
            ('supervisor1', 'supervisor@demo.local'),
        ).fetchone()
        if supervisor_user:
            conn.execute(
                """
                UPDATE users
                SET company_id=COALESCE(company_id, ?),
                    username=COALESCE(NULLIF(username, ''), ?),
                    full_name=COALESCE(NULLIF(full_name, ''), ?),
                    role='supervisor',
                    phone=COALESCE(NULLIF(phone, ''), ?),
                    email=COALESCE(NULLIF(email, ''), ?),
                    license_number=COALESCE(NULLIF(license_number, ''), ?),
                    hourly_rate=COALESCE(hourly_rate, ?),
                    active=1
                WHERE id=?
                """,
                (
                    demo_company,
                    'supervisor1',
                    'Sam Supervisor',
                    '210-555-0177',
                    'supervisor@demo.local',
                    'SUP-100',
                    24,
                    supervisor_user['id'],
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at)
                VALUES (?, ?, ?, ?, 'supervisor', ?, ?, ?, ?, 1, ?)
                """,
                (
                    demo_company,
                    'supervisor1',
                    hash_password('password123'),
                    'Sam Supervisor',
                    '210-555-0177',
                    'supervisor@demo.local',
                    'SUP-100',
                    24,
                    now,
                ),
            )

    supervisor_account = conn.execute(
        "SELECT id, company_id FROM users WHERE username=? OR email=? ORDER BY id LIMIT 1",
        ('supervisor1', 'supervisor@demo.local'),
    ).fetchone()
    if supervisor_account:
        supervisor_company_id = supervisor_account['company_id'] or demo_company
        if supervisor_company_id is None:
            first_company = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()
            supervisor_company_id = first_company['id'] if first_company else None
        if supervisor_company_id is not None:
            conn.execute(
                """
                UPDATE users
                SET company_id=COALESCE(company_id, ?),
                    username=COALESCE(NULLIF(username, ''), ?),
                    full_name=COALESCE(NULLIF(full_name, ''), ?),
                    role='supervisor',
                    email=COALESCE(NULLIF(email, ''), ?),
                    active=1
                WHERE id=?
                """,
                (
                    supervisor_company_id,
                    'supervisor1',
                    'Sam Supervisor',
                    'supervisor@demo.local',
                    supervisor_account['id'],
                ),
            )
            active_site = conn.execute(
                'SELECT id FROM sites WHERE company_id=? AND active=1 ORDER BY id LIMIT 1',
                (supervisor_company_id,),
            ).fetchone()
            if active_site:
                has_assignment = conn.execute(
                    'SELECT id FROM supervisor_site_assignments WHERE company_id=? AND supervisor_user_id=? AND site_id=?',
                    (supervisor_company_id, supervisor_account['id'], active_site['id']),
                ).fetchone()
                if not has_assignment:
                    conn.execute(
                        'INSERT INTO supervisor_site_assignments (company_id, supervisor_user_id, site_id, assigned_at) VALUES (?, ?, ?, ?)',
                        (supervisor_company_id, supervisor_account['id'], active_site['id'], now),
                    )

    if not bootstrap_created and fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM clients') == 0:
        clients = [
            (demo_company, 'Steele Commercial', 'Facility Manager', 'manager@steele-commercial.local', '210-555-1111', 'Primary point of contact', 1, now),
            (demo_company, 'Riverfront Logistics', 'Ops Manager', 'ops@riverfront-logistics.local', '210-555-2222', '', 1, now),
            (other_company, 'BlueLine Industrial', 'Yard Supervisor', 'yard@blueline-industrial.local', '830-555-3333', '', 1, now),
        ]
        conn.executemany('INSERT INTO clients (company_id, name, contact_name, contact_email, contact_phone, notes, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', clients)
    if not bootstrap_created and fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM guards') == 0:
        guard_users = conn.execute("SELECT company_id, full_name, phone, email, license_number, created_at FROM users WHERE role='guard' AND company_id IS NOT NULL").fetchall()
        for gu in guard_users:
            insert_guard(
                conn,
                gu['company_id'],
                full_name=gu['full_name'],
                phone=gu['phone'] or '',
                email=gu['email'] or '',
                license_number=gu['license_number'] or '',
                status='active',
                rating=5,
                training_status='pending',
                created_at=gu['created_at'] or now,
            )

    guard_users = conn.execute("SELECT id, company_id, full_name FROM users WHERE role='guard' AND company_id IS NOT NULL AND (guard_id IS NULL OR guard_id=0)").fetchall()
    for guard_user in guard_users:
        full_name = (guard_user['full_name'] or '').strip()
        if not full_name:
            continue
        guard = conn.execute("""
            SELECT id FROM guards
            WHERE company_id=? AND TRIM(COALESCE(name, first_name || ' ' || last_name))=?
            ORDER BY id
            LIMIT 1
        """, (guard_user['company_id'], full_name)).fetchone()
        if guard:
            conn.execute('UPDATE users SET guard_id=? WHERE id=?', (guard['id'], guard_user['id']))

    if not bootstrap_created and fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM sites') == 0:
        steele_client = conn.execute("SELECT id, name FROM clients WHERE company_id=? AND name='Steele Commercial'", (demo_company,)).fetchone()
        river_client = conn.execute("SELECT id, name FROM clients WHERE company_id=? AND name='Riverfront Logistics'", (demo_company,)).fetchone()
        blueline_client = conn.execute("SELECT id, name FROM clients WHERE company_id=? AND name='BlueLine Industrial'", (other_company,)).fetchone()
        sites = [
            (demo_company, steele_client['id'] if steele_client else None, steele_client['name'] if steele_client else 'Steele Commercial', 'Steele Plaza', '1200 Main St, San Antonio, TX', 'Front desk coverage', 1),
            (demo_company, river_client['id'] if river_client else None, river_client['name'] if river_client else 'Riverfront Logistics', 'Riverfront Logistics', '900 Warehouse Rd, New Braunfels, TX', 'Truck gate patrol', 1),
            (other_company, blueline_client['id'] if blueline_client else None, blueline_client['name'] if blueline_client else 'BlueLine Industrial', 'BlueLine Yard', '44 West Loop, Austin, TX', 'Evening patrols', 1),
        ]
        conn.executemany('INSERT INTO sites (company_id, client_id, client_company_name, name, address, notes, active) VALUES (?, ?, ?, ?, ?, ?, ?)', sites)
    elif demo_company is not None:
        conn.execute('UPDATE sites SET company_id=? WHERE company_id IS NULL', (demo_company,))
        conn.execute("UPDATE sites SET client_company_name = COALESCE(client_company_name, '')")
    if demo_company is not None:
        demo_guard_logins = [
            ('guard1', 'Marcus Hill', 'EMP-2201', 'BADGE-2201', '1111', 'Steele Plaza'),
            ('guard2', 'Ava Carter', 'EMP-2202', 'BADGE-2202', '2222', 'Riverfront Logistics'),
        ]
        for username, full_name, employee_id, badge_id, demo_pin, site_name in demo_guard_logins:
            guard_user = conn.execute(
                "SELECT id, guard_id FROM users WHERE company_id=? AND username=? AND role='guard' ORDER BY id LIMIT 1",
                (demo_company, username),
            ).fetchone()
            if not guard_user:
                continue
            conn.execute(
                "UPDATE users SET full_name=COALESCE(NULLIF(full_name, ''), ?), employee_id=COALESCE(NULLIF(employee_id, ''), ?), badge_id=COALESCE(NULLIF(badge_id, ''), ?) WHERE id=?",
                (full_name, employee_id, badge_id, guard_user['id']),
            )
            existing_pin = conn.execute('SELECT pin_hash FROM users WHERE id=?', (guard_user['id'],)).fetchone()
            if existing_pin and not existing_pin['pin_hash']:
                conn.execute('UPDATE users SET pin_hash=? WHERE id=?', (hash_password(demo_pin), guard_user['id']))
            guard_row_id = guard_user['guard_id']
            if guard_row_id:
                conn.execute(
                    "UPDATE guards SET employee_id=COALESCE(NULLIF(employee_id, ''), ?), badge_id=COALESCE(NULLIF(badge_id, ''), ?) WHERE company_id=? AND id=?",
                    (employee_id, badge_id, demo_company, guard_row_id),
                )
                assigned_site = conn.execute(
                    "SELECT id FROM sites WHERE company_id=? AND name=? AND COALESCE(active,1)=1 ORDER BY id LIMIT 1",
                    (demo_company, site_name),
                ).fetchone()
                if assigned_site:
                    existing_assignment = conn.execute(
                        'SELECT id FROM guard_site_assignments WHERE company_id=? AND guard_id=? AND site_id=?',
                        (demo_company, guard_row_id, assigned_site['id']),
                    ).fetchone()
                    if not existing_assignment:
                        conn.execute(
                            'INSERT INTO guard_site_assignments (company_id, guard_id, site_id, assigned_at) VALUES (?, ?, ?, ?)',
                            (demo_company, guard_row_id, assigned_site['id'], now),
                        )

    if demo_company is not None:
        supervisor_row = conn.execute("SELECT id FROM users WHERE company_id=? AND username='supervisor1' ORDER BY id LIMIT 1", (demo_company,)).fetchone()
        riverfront_site = conn.execute("SELECT id FROM sites WHERE company_id=? AND name='Riverfront Logistics' ORDER BY id LIMIT 1", (demo_company,)).fetchone()
        if supervisor_row and riverfront_site:
            existing_assignment = conn.execute(
                'SELECT id FROM supervisor_site_assignments WHERE company_id=? AND supervisor_user_id=? AND site_id=?',
                (demo_company, supervisor_row['id'], riverfront_site['id']),
            ).fetchone()
            if not existing_assignment:
                conn.execute(
                    'INSERT INTO supervisor_site_assignments (company_id, supervisor_user_id, site_id, assigned_at) VALUES (?, ?, ?, ?)',
                    (demo_company, supervisor_row['id'], riverfront_site['id'], now),
                )
    if not bootstrap_created and fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM shifts') == 0:
        g1 = conn.execute("SELECT id FROM users WHERE username='guard1'").fetchone()['id']
        g2 = conn.execute("SELECT id FROM users WHERE username='guard2'").fetchone()['id']
        s1 = conn.execute("SELECT id FROM sites WHERE name='Steele Plaza'").fetchone()['id']
        s2 = conn.execute("SELECT id FROM sites WHERE name='Riverfront Logistics'").fetchone()['id']
        today = date.today()
        demo_shifts = [
            (demo_company, g1, s1, today.isoformat(), '08:00', '16:00', 'assigned', 8, 0, 0, 'Day post'),
            (demo_company, g2, s2, today.isoformat(), '16:00', '23:00', 'assigned', 7, 0, 0, 'Evening truck gate'),
            (demo_company, None, s1, (today + timedelta(days=1)).isoformat(), '22:00', '06:00', 'open', 8, 0, 0, 'Open overnight shift'),
        ]
        shift_columns = ['company_id', 'site_id', 'shift_date', 'start_time', 'end_time', 'status', 'scheduled_hours', 'worked_hours', 'overtime_alert', 'notes']
        for assigned_user_id, site_id, shift_date, start_time, end_time, status, scheduled_hours, worked_hours, overtime_alert, notes in [(row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10]) for row in demo_shifts]:
            sql, params = shift_insert_sql_and_params(conn, shift_columns, [demo_company, site_id, shift_date, start_time, end_time, status, scheduled_hours, worked_hours, overtime_alert, notes], assigned_user_id)
            conn.execute(sql, params)
    elif demo_company is not None:
        conn.execute('UPDATE shifts SET company_id=? WHERE company_id IS NULL', (demo_company,))
        # backfill scheduled hours
        for row in conn.execute('SELECT id, start_time, end_time, scheduled_hours FROM shifts').fetchall():
            if not row['scheduled_hours']:
                hrs = calculate_shift_hours_from_strings(row['start_time'], row['end_time'])
                conn.execute('UPDATE shifts SET scheduled_hours=? WHERE id=?', (hrs, row['id']))
        sync_shift_assignment_schema(conn)

    if table_exists(conn, 'sessions'):
        for col in ['company_id INTEGER', 'site_id INTEGER', 'role TEXT']:
            ensure_column(conn, 'sessions', col)

    # seed availability for guards
    guards = conn.execute("SELECT id, company_id FROM users WHERE role='guard'").fetchall()
    for g in guards:
        for weekday in range(7):
            exists = conn.execute('SELECT 1 FROM availability WHERE user_id=? AND weekday=?', (g['id'], weekday)).fetchone()
            if not exists:
                conn.execute('''
                    INSERT INTO availability (company_id, user_id, weekday, available_start, available_end, is_available)
                    VALUES (?, ?, ?, '08:00', '20:00', 1)
                ''', (g['company_id'], g['id'], weekday))

    conn.commit()
    conn.close()


def render(template_name, **context):
    context.setdefault('product_short_name', PRODUCT_SHORT_NAME)
    context.setdefault('product_full_name', PRODUCT_FULL_NAME)
    context.setdefault('provider_brand_name', PROVIDER_BRAND_NAME)
    context.setdefault('brand_subtitle', BRAND_SUBTITLE)
    context.setdefault('provider_logo_url', PROVIDER_SHIELD_LOGO_URL)
    template = env.get_template(template_name)
    return template.render(**context).encode('utf-8')


def html_response(start_response, body, status='200 OK', extra_headers=None):
    start_response(status, response_headers(extra_headers, 'text/html; charset=utf-8'))
    return [body]


def json_response(start_response, payload, status='200 OK'):
    start_response(status, response_headers(content_type='application/json; charset=utf-8'))
    return [json.dumps(payload, default=str, indent=2).encode('utf-8')]


def redirect(start_response, location, extra_headers=None):
    headers = [('Location', location)]
    if extra_headers:
        headers.extend(extra_headers)
    start_response('302 Found', response_headers(headers, 'text/plain; charset=utf-8'))
    return [b'']


def redirect_with_feedback(start_response, location, message=None, error=None, extra_headers=None):
    params = []
    if message:
        params.append(('message', message))
    if error:
        params.append(('error', error))
    if params:
        separator = '&' if '?' in location else '?'
        location = f"{location}{separator}{urlencode(params)}"
    return redirect(start_response, location, extra_headers=extra_headers)


def dashboard_shift_form_location(form_data):
    params = []
    for key in ('site_id', 'user_id', 'shift_date', 'start_time', 'end_time', 'notes'):
        value = (form_data.get(key) or '').strip() if isinstance(form_data.get(key), str) else (form_data.get(key) or '')
        if value != '':
            params.append((key, value))
    if not params:
        return '/dashboard'
    return f"/dashboard?{urlencode(params)}"


def bad_request(start_response, message='Bad Request'):
    start_response('400 Bad Request', response_headers(content_type='text/plain; charset=utf-8'))
    return [message.encode('utf-8')]


def not_found(start_response):
    start_response('404 Not Found', response_headers(content_type='text/plain; charset=utf-8'))
    return [b'Not Found']


def get_session(environ):
    clear_expired_sessions()
    cookie = environ.get('HTTP_COOKIE', '')
    cookies = {}
    for item in cookie.split(';'):
        if '=' in item:
            k, v = item.strip().split('=', 1)
            cookies[k] = v
    session_id = cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        return None, None
    conn = db()
    row = conn.execute('SELECT * FROM sessions WHERE id=? AND expires_at >= ?', (session_id, now_utc().strftime('%Y-%m-%d %H:%M:%S'))).fetchone()
    conn.close()
    if not row:
        return None, session_id
    session = {'user_id': row['user_id']}
    for key in ('company_id', 'site_id', 'role'):
        try:
            session[key] = row[key]
        except Exception:
            pass
    return session, session_id


def get_current_user(environ):
    session, _ = get_session(environ)
    if not session:
        return None
    conn = db()
    user = conn.execute('''
        SELECT users.*, companies.name as company_name, companies.tagline as company_tagline, companies.logo_path as company_logo
        FROM users LEFT JOIN companies ON users.company_id = companies.id WHERE users.id=?
    ''', (session['user_id'],)).fetchone()
    conn.close()
    if not user:
        return None
    user_data = dict(user)
    for key in ('company_id', 'site_id', 'role'):
        if key in session:
            user_data[f'session_{key}'] = session.get(key)
    user_data['company_logo_url'] = public_asset_url(user_data.get('company_logo'))
    return user_data


def require_login(environ, start_response):
    user = get_current_user(environ)
    if not user:
        return None, redirect(start_response, '/login')
    return user, None


def require_company_access(user, company_id):
    return user['role'] == 'superadmin' or user['company_id'] == company_id


def require_admin(environ, start_response):
    user, response = require_login(environ, start_response)
    if response:
        return None, response
    if user['role'] not in {'superadmin', 'company_admin', 'admin', 'supervisor'}:
        return None, redirect(start_response, '/dashboard')
    if user['role'] == 'supervisor':
        path = (environ.get('PATH_INFO') or '').strip()
        blocked_prefixes = ('/admin/payroll', '/admin/paystubs', '/company', '/superadmin')
        blocked_exact = {'/payroll'}
        if path in blocked_exact or any(path.startswith(prefix) for prefix in blocked_prefixes):
            return None, redirect_with_feedback(start_response, '/dashboard', error='Supervisor role cannot access that admin area.')
    return user, None

def guard_site_ids(conn, user):
    guard_row_id = row_value(user, 'guard_id')
    if not user or row_value(user, 'role') != 'guard' or not guard_row_id:
        return None
    rows = conn.execute(
        '''
        SELECT DISTINCT gsa.site_id
        FROM guard_site_assignments gsa
        JOIN sites s ON s.id=gsa.site_id AND s.company_id=gsa.company_id
        WHERE gsa.company_id=? AND gsa.guard_id=? AND COALESCE(s.active,1)=1
        ORDER BY gsa.site_id
        ''',
        (user['company_id'], guard_row_id),
    ).fetchall()
    return {row['site_id'] for row in rows}


def supervisor_site_ids(conn, user):
    if not user:
        return None
    if row_value(user, 'role') == 'guard':
        return guard_site_ids(conn, user) or set()
    if row_value(user, 'role') != 'supervisor':
        return None
    rows = conn.execute(
        'SELECT site_id FROM supervisor_site_assignments WHERE company_id=? AND supervisor_user_id=?',
        (user['company_id'], user['id']),
    ).fetchall()
    return {row['site_id'] for row in rows}


def supervisor_can_access_site(conn, user, site_id):
    if row_value(user, 'role') not in {'supervisor', 'guard'}:
        return True
    allowed_site_ids = supervisor_site_ids(conn, user)
    return bool(allowed_site_ids and int(site_id) in allowed_site_ids)


def sidebar_nav_items(user, active_path):
    items = [
        {'label': 'Dashboard', 'href': '/dashboard', 'active': active_path == '/dashboard'},
    ]
    if user['role'] in {'company_admin', 'superadmin', 'admin', 'supervisor', 'guard', 'client'}:
        items.append({'label': 'Patrols', 'href': '/patrols', 'active': active_path in {'/patrols', '/patrol/run', '/patrol/tour'}})
    if user['role'] != 'client':
        items.extend([
            {'label': 'Weekly Schedule', 'href': '/weekly-schedule', 'active': active_path == '/weekly-schedule'},
            {'label': 'Monthly Schedule', 'href': '/monthly-schedule', 'active': active_path == '/monthly-schedule'},
            {'label': 'My Profile', 'href': '/profile', 'active': active_path == '/profile'},
        ])
    if user['role'] in {'company_admin', 'superadmin', 'supervisor'}:
        items.extend([
            {'label': 'Guards', 'href': '/guards', 'active': active_path == '/guards'},
            {'label': 'Reports', 'href': '/reports', 'active': active_path == '/reports'},
        ])
        if user['role'] in {'company_admin', 'superadmin'}:
            items.extend([
                {'label': 'Payroll', 'href': '/payroll', 'active': active_path == '/payroll'},
                {'label': 'Paystubs', 'href': '/admin/paystubs/upload', 'active': active_path == '/admin/paystubs/upload'},
            ])
    if user['role'] == 'guard':
        items.extend([
            {'label': 'Daily Activity Reports', 'href': '/guard/daily-activity-reports', 'active': active_path == '/guard/daily-activity-reports'},
            {'label': 'Incident Reports', 'href': '/guard/incident-reports', 'active': active_path == '/guard/incident-reports'},
            {'label': 'My Reports', 'href': '/guard/my-reports', 'active': active_path == '/guard/my-reports'},
        ])
        items.append({'label': 'My Paystubs', 'href': '/my/paystubs', 'active': active_path == '/my/paystubs'})
    items.append({'label': 'Logout', 'href': '/logout', 'active': False})
    return items


def app_page(environ, start_response, user, template_name, active_path='/dashboard', view='week', title=PRODUCT_FULL_NAME, **extra_context):
    query = parse_query(environ)
    selected_site_id = (query.get('site_id') or '').strip()
    if row_value(user, 'role') == 'guard' and selected_site_id:
        conn = db()
        can_access_site = selected_site_id.isdigit() and supervisor_can_access_site(conn, user, int(selected_site_id))
        conn.close()
        if not can_access_site:
            return redirect_with_feedback(start_response, '/dashboard', error='You can only access sites assigned to your guard profile.')
        session, session_id = get_session(environ)
        selected_site_int = int(selected_site_id)
        if session_id and row_value(user, 'session_site_id') != selected_site_int:
            set_session_site(session_id, selected_site_int)
            user['session_site_id'] = selected_site_int
    shift_form_values = {k: query.get(k, '') for k in ('site_id', 'user_id', 'shift_date', 'start_time', 'end_time', 'notes', 'exclude_shift_id')}
    context = get_dashboard_context(user, view, shift_form_values=shift_form_values)
    context.update(extra_context)
    context.setdefault('active_path', active_path)
    context.setdefault('nav_items', sidebar_nav_items(user, active_path))
    context.setdefault('page_title', title or PRODUCT_FULL_NAME)
    context.setdefault('flash_message', query.get('message', ''))
    context.setdefault('flash_error', query.get('error', ''))
    return html_response(
        start_response,
        render_page(environ, template_name, title=title, user=user, **context),
        extra_headers=csrf_headers(environ),
    )


def dashboard_page(environ, start_response, user, active_path='/dashboard', view='week', title=PRODUCT_FULL_NAME, **extra_context):
    return app_page(environ, start_response, user, 'dashboard.html', active_path=active_path, view=view, title=title, **extra_context)


def profile_page(environ, start_response, user, message=None, error=None):
    conn = db()
    upcoming_shifts = conn.execute("""
        SELECT shifts.*, sites.name as site_name
        FROM shifts JOIN sites ON shifts.site_id=sites.id
        WHERE shifts.company_id=? AND COALESCE(shifts.user_id, shifts.guard_id)=? AND shifts.shift_date>=?
        ORDER BY shifts.shift_date, shifts.start_time LIMIT 8
    """, (user['company_id'], user['id'], date.today().isoformat())).fetchall()
    availability = conn.execute('SELECT * FROM availability WHERE user_id=? ORDER BY weekday', (user['id'],)).fetchall()
    conn.close()
    return html_response(
        start_response,
        render_page(
            environ,
            'profile.html',
            title='Profile',
            user=user,
            upcoming_shifts=upcoming_shifts,
            availability=availability,
            message=message,
            error=error,
            nav_items=sidebar_nav_items(user, '/profile'),
            active_path='/profile',
            page_title='Guard Profile',
        ),
        extra_headers=csrf_headers(environ),
    )


def parse_multipart(environ, content_type):
    try:
        size = int(environ.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        size = 0
    raw = environ['wsgi.input'].read(size)
    boundary = None
    for part in content_type.split(';'):
        part = part.strip()
        if part.startswith('boundary='):
            boundary = part.split('=', 1)[1].encode('utf-8')
            break
    if not boundary:
        return {}, {}
    delimiter = b'--' + boundary
    fields, files = {}, {}
    for chunk in raw.split(delimiter):
        if not chunk or chunk in (b'--\r\n', b'--', b'\r\n'):
            continue
        chunk = chunk.strip(b'\r\n')
        if chunk.endswith(b'--'):
            chunk = chunk[:-2]
        if b'\r\n\r\n' not in chunk:
            continue
        header_blob, content = chunk.split(b'\r\n\r\n', 1)
        headers = header_blob.decode('utf-8', errors='ignore').split('\r\n')
        disposition = next((h for h in headers if h.lower().startswith('content-disposition:')), '')
        name = None
        filename = None
        for item in disposition.split(';'):
            item = item.strip()
            if item.startswith('name='):
                name = item.split('=', 1)[1].strip('"')
            elif item.startswith('filename='):
                filename = item.split('=', 1)[1].strip('"')
        if not name:
            continue
        content = content.rstrip(b'\r\n')
        if filename:
            files.setdefault(name, []).append({'filename': filename, 'content': content})
        else:
            fields[name] = content.decode('utf-8', errors='ignore')
    return fields, files


def parse_post(environ):
    content_type = environ.get('CONTENT_TYPE', '')
    if content_type.startswith('multipart/form-data'):
        return parse_multipart(environ, content_type)
    try:
        size = int(environ.get('CONTENT_LENGTH', '0') or 0)
    except ValueError:
        size = 0
    raw = environ['wsgi.input'].read(size).decode('utf-8')
    return {k: v[0] for k, v in parse_qs(raw).items()}, {}


def parse_query(environ):
    return {k: v[0] for k, v in parse_qs(environ.get('QUERY_STRING', '')).items()}


def files_for_field(files, field_name):
    value = files.get(field_name)
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def collect_attachments(files, field_name):
    collected = []
    for item in files_for_field(files, field_name):
        collected.append(item)
    for key, value in files.items():
        if key.startswith(field_name + '_'):
            vals = value if isinstance(value, list) else [value]
            collected.extend(vals)
    return [f for f in collected if f and f.get('filename')]


def is_allowed_attachment(file_info):
    ext = os.path.splitext(os.path.basename((file_info or {}).get('filename', '')))[1].lower()
    allowed = {'.jpg','.jpeg','.png','.gif','.webp','.pdf','.doc','.docx','.txt'}
    blocked = {'.exe','.js','.sh','.bat','.cmd','.php','.py','.jar','.msi','.com'}
    return ext in allowed and ext not in blocked


def create_report_attachment(conn, company_id, report_type, report_id, uploaded_by, file_info, folder):
    if not is_allowed_attachment(file_info):
        return False
    file_name, stored_path = save_upload(file_info, folder)
    if not stored_path:
        return False
    mime_type = upload_content_type(stored_path)
    file_size = len(file_info.get('content', b''))
    insert_and_get_id(
        conn,
        'INSERT INTO report_attachments (company_id, report_type, report_id, uploaded_by, file_name, stored_path, mime_type, file_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (company_id, report_type, report_id, uploaded_by, file_name or os.path.basename(stored_path), stored_path, mime_type, file_size, utc_now_str())
    )
    return True


def fetch_report_attachments(conn, report_type, report_id):
    rows = conn.execute('SELECT ra.*, u.full_name as uploaded_by_name FROM report_attachments ra LEFT JOIN users u ON u.id=ra.uploaded_by WHERE ra.report_type=? AND ra.report_id=? ORDER BY ra.created_at ASC, ra.id ASC', (report_type, report_id)).fetchall()
    return [dict(r) for r in rows]


def serve_static(environ, start_response, path):
    normalized = path.lstrip('/').replace('\\', '/')
    if normalized.startswith('uploads/paystubs/') or normalized.startswith('uploads/dar_photos/') or normalized.startswith('uploads/incident_attachments/'):
        return forbidden(start_response, 'Secure files must be accessed through protected routes.')
    file_path = _safe_join(UPLOAD_DIR, normalized[len('uploads/'):]) if normalized.startswith('uploads/') else os.path.join(BASE_DIR, normalized)
    if not os.path.isfile(file_path):
        return not_found(start_response)
    ext = os.path.splitext(file_path)[1].lower()
    content_type = {
        '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.png': 'image/png',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml', '.pdf': 'application/pdf',
        '.csv': 'text/csv; charset=utf-8'
    }.get(ext, 'application/octet-stream')
    start_response('200 OK', response_headers(content_type=content_type))
    with open(file_path, 'rb') as f:
        return [f.read()]


def save_upload(file_info, folder='general'):
    if isinstance(file_info, list):
        file_info = next((item for item in file_info if item and item.get('filename')), None)
    if not file_info or not file_info.get('filename'):
        return None, None
    if len(file_info.get('content', b'')) > MAX_UPLOAD_MB * 1024 * 1024:
        return None, None
    original = os.path.basename(file_info['filename'])
    ext = os.path.splitext(original)[1].lower()
    safe_dir = os.path.join(UPLOAD_DIR, folder)
    os.makedirs(safe_dir, exist_ok=True)
    safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}{ext}"
    dest = os.path.join(safe_dir, safe_name)
    with open(dest, 'wb') as f:
        f.write(file_info['content'])
    rel = os.path.relpath(dest, BASE_DIR)
    return original, rel


def calculate_shift_hours_from_strings(start_time, end_time):
    start_dt = datetime.strptime(start_time, '%H:%M')
    end_dt = datetime.strptime(end_time, '%H:%M')
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return round((end_dt - start_dt).total_seconds() / 3600.0, 2)


def shift_boundary_datetimes(shift_date, start_time, end_time):
    start_dt = datetime.strptime(f'{shift_date} {start_time}', '%Y-%m-%d %H:%M')
    end_dt = datetime.strptime(f'{shift_date} {end_time}', '%Y-%m-%d %H:%M')
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def missed_clock_alert_thresholds(shift_date, start_time, end_time):
    start_dt, end_dt = shift_boundary_datetimes(shift_date, start_time, end_time)
    grace = timedelta(minutes=10)
    return start_dt + grace, end_dt + grace


def calculate_worked_hours(clock_in, clock_out):
    if not (clock_in and clock_out):
        return 0
    start_dt = datetime.strptime(clock_in, '%Y-%m-%d %H:%M:%S')
    end_dt = datetime.strptime(clock_out, '%Y-%m-%d %H:%M:%S')
    return round(max(0, (end_dt - start_dt).total_seconds() / 3600.0), 2)


def approved_time_off_request_for_date(conn, company_id, guard_id, shift_date):
    if not (company_id and guard_id and shift_date):
        return None
    return conn.execute('''
        SELECT id, start_date, end_date, type
        FROM time_off_requests
        WHERE company_id=? AND guard_id=? AND status='approved' AND start_date<=? AND end_date>=?
        ORDER BY start_date DESC, id DESC
        LIMIT 1
    ''', (company_id, guard_id, shift_date, shift_date)).fetchone()


def approved_time_off_conflict_error(shift_date, request_row, guard_name=None):
    guard_label = guard_name or 'Selected guard'
    leave_type = (request_row['type'] if request_row and request_row['type'] else 'approved').title()
    return (
        f"{guard_label} cannot be assigned on {shift_date}: "
        "Unavailable - Approved Time Off "
        f"({leave_type} time off is approved for {request_row['start_date']} to {request_row['end_date']})."
    )


def overlapping_shift_for_guard(conn, company_id, guard_id, shift_date, start_time, end_time, exclude_shift_id=None):
    if not (company_id and guard_id and shift_date and start_time and end_time):
        return None
    if exclude_shift_id in ('', None):
        exclude_shift_id = None
    else:
        try:
            exclude_shift_id = int(exclude_shift_id)
        except (TypeError, ValueError):
            exclude_shift_id = None
    try:
        new_start, new_end = shift_boundary_datetimes(shift_date, start_time, end_time)
    except ValueError:
        return None
    window_start = (new_start.date() - timedelta(days=1)).isoformat()
    window_end = (new_end.date() + timedelta(days=1)).isoformat()
    rows = conn.execute('''
        SELECT id, shift_date, start_time, end_time
        FROM shifts
        WHERE company_id=?
          AND COALESCE(user_id, guard_id)=?
          AND (? IS NULL OR id<>?)
          AND shift_date BETWEEN ? AND ?
        ORDER BY shift_date, start_time
    ''', (company_id, guard_id, exclude_shift_id, exclude_shift_id, window_start, window_end)).fetchall()
    for row in rows:
        existing_start, existing_end = shift_boundary_datetimes(row['shift_date'], row['start_time'], row['end_time'])
        if existing_start < new_end and existing_end > new_start:
            return row
    return None


def overlapping_shift_conflict_error(conflict_shift, guard_name=None):
    guard_label = guard_name or 'Selected guard'
    return (
        f"{guard_label} already has another shift during that time "
        f"({conflict_shift['shift_date']} {conflict_shift['start_time']}-{conflict_shift['end_time']})."
    )


def guard_availability_metadata(conn, company_id, guards, shift_date, start_time, end_time, exclude_shift_id=None):
    metadata = {}
    if not guards:
        return metadata
    for guard in guards:
        guard_id = guard['id']
        state = {'available': True, 'reason': '', 'overlap_shift': None}
        approved_leave = approved_time_off_request_for_date(conn, company_id, guard_id, shift_date)
        if approved_leave:
            state['available'] = False
            state['reason'] = 'approved_leave'
        else:
            overlap_shift = overlapping_shift_for_guard(
                conn,
                company_id,
                guard_id,
                shift_date,
                start_time,
                end_time,
                exclude_shift_id=exclude_shift_id,
            )
            if overlap_shift:
                state['available'] = False
                state['reason'] = 'overlap_shift'
                state['overlap_shift'] = overlap_shift
        metadata[guard_id] = state
    return metadata


def approved_time_off_lookup_by_guard_and_date(conn, company_id, range_start, range_end):
    lookup = {}
    rows = conn.execute('''
        SELECT guard_id, start_date, end_date, type
        FROM time_off_requests
        WHERE company_id=? AND status='approved' AND end_date>=? AND start_date<=?
    ''', (company_id, range_start, range_end)).fetchall()
    for row in rows:
        cursor = datetime.strptime(row['start_date'], '%Y-%m-%d').date()
        end = datetime.strptime(row['end_date'], '%Y-%m-%d').date()
        while cursor <= end:
            cursor_key = cursor.isoformat()
            if range_start <= cursor_key <= range_end:
                lookup[(row['guard_id'], cursor_key)] = {
                    'start_date': row['start_date'],
                    'end_date': row['end_date'],
                    'type': row['type'],
                }
            cursor += timedelta(days=1)
    return lookup


def process_shift_clock_action(user, shift_id, action):
    if not shift_id or action not in {'in', 'out'}:
        return False, 'Invalid clock action'
    conn = db()
    try:
        shift = conn.execute('SELECT * FROM shifts WHERE id=?', (shift_id,)).fetchone()
        if not shift or not require_company_access(user, shift['company_id']):
            return False, 'Shift not accessible'
        if user['role'] != 'guard' or shift_assignment_value(shift) != user['id']:
            return False, 'Only the assigned guard can clock this shift'
        if not supervisor_can_access_site(conn, user, shift['site_id']):
            return False, 'Shift is not at your assigned site'

        timestamp = utc_now_str()
        if action == 'in':
            if shift['clock_in_time']:
                return False, 'Shift already clocked in'
            conn.execute('UPDATE shifts SET clock_in_time=?, status=? WHERE id=?', (timestamp, 'clocked_in', shift_id))
            message = 'clocked in'
        else:
            if not shift['clock_in_time']:
                return False, 'Shift must be clocked in before clocking out'
            if shift['clock_out_time']:
                return False, 'Shift already clocked out'
            worked = calculate_worked_hours(shift['clock_in_time'], timestamp)
            conn.execute('UPDATE shifts SET clock_out_time=?, status=?, worked_hours=?, overtime_alert=? WHERE id=?', (timestamp, 'completed', worked, 1 if worked > 8 else 0, shift_id))
            message = 'clocked out'
        conn.commit()
    finally:
        conn.close()
    return True, message


def company_filter_clause(user):
    if user['role'] == 'superadmin':
        return '', ()
    return ' WHERE company_id=? ', (user['company_id'],)


def get_company_scope_id(user):
    return user['company_id']


def normalize_report_row(report, fallback_type=None):
    row = dict(report)
    report_type_value = row.get('report_type') or fallback_type or 'N/A'
    report_date = row.get('report_date')
    report_time = row.get('report_time')
    created_at = row.get('created_at')
    timestamp_value = report_date and report_time and f"{report_date} {report_time}" or created_at or 'N/A'
    summary_value = row.get('summary') or row.get('narrative') or 'N/A'
    row.update({
        'report_type': report_type_value,
        'status': row.get('status') or 'N/A',
        'priority': row.get('priority') or 'N/A',
        'site': row.get('site') or row.get('site_name') or 'N/A',
        'officer': row.get('officer') or row.get('officer_name') or 'N/A',
        'timestamp': timestamp_value,
        'created_at': created_at or timestamp_value,
        'summary_preview': summary_value,
        'summary': summary_value,
    })
    return row


def normalized_recent_reports(base_reports, daily_activity_reports, incident_reports):
    normalized_rows = [normalize_report_row(report) for report in base_reports]
    normalized_rows.extend(normalize_report_row(report, fallback_type='Daily Activity') for report in daily_activity_reports)
    normalized_rows.extend(normalize_report_row(report, fallback_type='Incident') for report in incident_reports)
    return sorted(normalized_rows, key=lambda x: str(x.get('created_at') or ''), reverse=True)


def report_status_options():
    return ['Open', 'Under Review', 'Escalated', 'Closed']


def report_management_context(conn, user, query):
    company_id = user['company_id']
    page = max(1, int((query.get('page') or '1') if str(query.get('page') or '1').isdigit() else 1))
    page_size = 15
    selected_type = (query.get('type') or '').strip().lower()
    selected_status = (query.get('status') or '').strip().lower()
    selected_officer = (query.get('officer_id') or '').strip()
    selected_site = (query.get('site_id') or '').strip()
    q = (query.get('q') or '').strip().lower()
    all_rows = []

    daily_rows = conn.execute('''
        SELECT d.id as report_id, 'daily_activity' as report_kind, 'Daily Activity' as report_type, d.status, '' as priority, d.created_at, d.summary as narrative,
               '' as persons_involved, '' as witnesses, d.photo_path, '' as attachment_path, s.name as site_name, u.full_name as officer_name, d.officer_id, d.site_id, d.supervisor_notes, d.admin_notes, d.resolved_at
        FROM daily_activity_reports d
        JOIN sites s ON d.site_id=s.id
        JOIN users u ON d.officer_id=u.id
        WHERE d.company_id=?
    ''', (company_id,)).fetchall()
    incident_rows = conn.execute('''
        SELECT i.id as report_id, 'incident' as report_kind, 'Incident' as report_type, i.status, i.priority, i.created_at, i.narrative,
               i.persons_involved, i.witnesses, '' as photo_path, i.attachment_path, s.name as site_name, u.full_name as officer_name, i.officer_id, i.site_id, i.supervisor_notes, i.admin_notes, i.resolved_at
        FROM incident_reports i
        JOIN sites s ON i.site_id=s.id
        JOIN users u ON i.officer_id=u.id
        WHERE i.company_id=?
    ''', (company_id,)).fetchall()
    if user['role'] != 'guard':
        all_rows = list(daily_rows) + list(incident_rows)
    else:
        all_rows = [r for r in list(daily_rows) + list(incident_rows) if str(r['officer_id']) == str(user['id'])]
    history_rows = conn.execute('''
        SELECT h.*, u.full_name AS changed_by_name, u.role AS changed_by_role
        FROM report_status_history h
        LEFT JOIN users u ON u.id=h.changed_by
        WHERE h.company_id=?
        ORDER BY h.changed_at DESC
    ''', (company_id,)).fetchall()
    history_by_key = {}
    for row in history_rows:
        history_row = dict(row)
        history_row['report_type_label'] = 'Incident' if history_row.get('report_kind') == 'incident' else 'Daily Activity'
        history_by_key.setdefault(f"{row['report_kind']}:{row['report_id']}", []).append(history_row)
    note_rows = conn.execute('''
        SELECT n.*, u.full_name AS created_by_name, u.role AS created_by_role
        FROM report_notes n
        LEFT JOIN users u ON u.id=n.created_by
        WHERE n.company_id=?
        ORDER BY n.created_at DESC
    ''', (company_id,)).fetchall()
    notes_by_key = {}
    for row in note_rows:
        notes_by_key.setdefault(f"{row['report_kind']}:{row['report_id']}", []).append(dict(row))
    filtered = []
    for row in all_rows:
        row = dict(row)
        row['uploaded_at'] = row.get('created_at')
        row['uploaded_by'] = row.get('officer_name') or 'Unknown officer'
        if row.get('report_kind') == 'daily_activity' and row.get('photo_path'):
            row['photo_attachment'] = attachment_meta(row.get('photo_path'))
            row['photo_attachment']['secure_url'] = f"/report-files/{row['report_kind']}/{row['report_id']}/photo"
        else:
            row['photo_attachment'] = None
        if row.get('report_kind') == 'incident' and row.get('attachment_path'):
            row['file_attachment'] = attachment_meta(row.get('attachment_path'))
            row['file_attachment']['secure_url'] = f"/report-files/{row['report_kind']}/{row['report_id']}/attachment"
        else:
            row['file_attachment'] = None
        key = f"{row['report_kind']}:{row['report_id']}"
        row['status_history'] = history_by_key.get(key, [])
        row['note_history'] = notes_by_key.get(key, [])
        activity_timeline = [{
            'event_at': row.get('created_at'),
            'event_type': 'submission',
            'label': 'Submission',
            'description': f"Submitted by {row.get('officer_name') or 'Unknown officer'}",
            'actor_name': row.get('officer_name') or 'Unknown officer',
        }]
        for history in row['status_history']:
            new_status = history.get('new_status') or 'N/A'
            event_label = 'Closure' if new_status.lower() == 'closed' else 'Status Change'
            activity_timeline.append({
                'event_at': history.get('changed_at'),
                'event_type': 'closure' if new_status.lower() == 'closed' else 'status_change',
                'label': event_label,
                'description': f"{history.get('old_status') or 'N/A'} → {new_status}",
                'actor_name': history.get('changed_by_name') or 'Unknown',
            })
        for note in row['note_history']:
            note_label = 'Supervisor Note' if (note.get('note_type') or '').lower() == 'supervisor' else 'Admin Note'
            activity_timeline.append({
                'event_at': note.get('created_at'),
                'event_type': 'note_addition',
                'label': note_label,
                'description': note.get('note_text') or '',
                'actor_name': note.get('created_by_name') or 'Unknown',
            })
        has_closure_event = any(item.get('event_type') == 'closure' for item in activity_timeline)
        if row.get('resolved_at') and not has_closure_event:
            activity_timeline.append({
                'event_at': row.get('resolved_at'),
                'event_type': 'closure',
                'label': 'Closure',
                'description': 'Report closed',
                'actor_name': 'Unknown',
            })
        row['activity_timeline'] = sorted(activity_timeline, key=lambda item: item.get('event_at') or '', reverse=True)
        if selected_type and row['report_kind'] != selected_type:
            continue
        if selected_status and (row.get('status') or '').strip().lower() != selected_status:
            continue
        if selected_officer and str(row.get('officer_id') or '') != selected_officer:
            continue
        if selected_site and str(row.get('site_id') or '') != selected_site:
            continue
        hay = ' '.join([str(row.get('narrative') or ''), str(row.get('officer_name') or ''), str(row.get('site_name') or ''), str(row.get('persons_involved') or '')]).lower()
        if q and q not in hay:
            continue
        filtered.append(row)
    filtered = sorted(filtered, key=lambda x: x.get('created_at') or '', reverse=True)
    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    pages = max(1, (total + page_size - 1) // page_size)
    officers = conn.execute("SELECT id, full_name FROM users WHERE company_id=? AND role='guard' ORDER BY full_name", (company_id,)).fetchall()
    sites = conn.execute("SELECT id, name FROM sites WHERE company_id=? ORDER BY name", (company_id,)).fetchall()
    return {
        'managed_reports': filtered[start:end],
        'report_filters': {'type': selected_type, 'status': selected_status, 'officer_id': selected_officer, 'site_id': selected_site, 'q': q},
        'report_pages': {'current': page, 'total': pages, 'has_prev': page > 1, 'has_next': page < pages},
        'report_filter_officers': officers,
        'report_filter_sites': sites,
        'report_status_options': report_status_options(),
    }


def get_dashboard_context(user, view='week', shift_form_values=None):
    conn = db()
    company_id = get_company_scope_id(user)
    allowed_site_ids = supervisor_site_ids(conn, user)
    today = date.today()
    if view == 'month':
        range_start = today.replace(day=1)
        next_month = (range_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        range_end = next_month - timedelta(days=1)
    else:
        range_start = today - timedelta(days=today.weekday())
        range_end = range_start + timedelta(days=6)

    my_shifts = []
    available_shifts = []

    shift_form_values = shift_form_values or {}
    if user['role'] == 'guard':
        guard_site_filter = tuple(sorted(allowed_site_ids or set()))
        if guard_site_filter:
            guard_site_placeholders = ','.join(['?'] * len(guard_site_filter))
            my_shifts = conn.execute(f'''
                SELECT shifts.*, COALESCE(shifts.user_id, shifts.guard_id) as user_id, COALESCE(shifts.guard_id, shifts.user_id) as guard_id, sites.name as site_name, sites.address, sites.client_company_name
                FROM shifts JOIN sites ON shifts.site_id = sites.id
                WHERE COALESCE(shifts.user_id, shifts.guard_id)=? AND shifts.company_id=? AND shifts.site_id IN ({guard_site_placeholders})
                ORDER BY shift_date, start_time
            ''', (user['id'], company_id, *guard_site_filter)).fetchall()
            available_shifts = conn.execute(f'''
                SELECT shifts.*, COALESCE(shifts.user_id, shifts.guard_id) as user_id, COALESCE(shifts.guard_id, shifts.user_id) as guard_id, sites.name as site_name, sites.address, sites.client_company_name
                FROM shifts JOIN sites ON shifts.site_id = sites.id
                WHERE shifts.company_id=? AND COALESCE(shifts.user_id, shifts.guard_id) IS NULL AND shifts.status='open' AND shifts.shift_date>=? AND shifts.site_id IN ({guard_site_placeholders})
                ORDER BY shift_date, start_time
            ''', (company_id, today.isoformat(), *guard_site_filter)).fetchall()
        else:
            my_shifts = []
            available_shifts = []
        shifts = my_shifts
        sites = conn.execute('SELECT * FROM sites WHERE company_id=? ORDER BY name', (company_id,)).fetchall()
        clients = conn.execute('SELECT * FROM clients WHERE company_id=? ORDER BY name', (company_id,)).fetchall()
        reports = conn.execute('''
            SELECT reports.*, sites.name as site_name FROM reports
            JOIN sites ON reports.site_id=sites.id
            WHERE reports.company_id=? AND reports.officer_name=?
            ORDER BY reports.created_at DESC LIMIT 10
        ''', (company_id, user['full_name'])).fetchall()
        recent_reports = reports
        guards = []
        dar_recent = conn.execute('''
            SELECT d.*, s.name as site_name
            FROM daily_activity_reports d
            JOIN sites s ON d.site_id=s.id
            WHERE d.company_id=? AND d.officer_id=?
            ORDER BY d.created_at DESC LIMIT 10
        ''', (company_id, user['id'])).fetchall()
        incident_recent = conn.execute('''
            SELECT i.*, s.name as site_name, u.full_name as officer_name
            FROM incident_reports i
            JOIN sites s ON i.site_id=s.id
            JOIN users u ON i.officer_id=u.id
            WHERE i.company_id=? AND i.officer_id=?
            ORDER BY i.created_at DESC LIMIT 10
        ''', (company_id, user['id'])).fetchall()
    else:
        shifts = conn.execute('''
            SELECT shifts.*, COALESCE(shifts.user_id, shifts.guard_id) as user_id, COALESCE(shifts.guard_id, shifts.user_id) as guard_id, users.full_name, sites.name as site_name, sites.address, sites.client_company_name
            FROM shifts
            JOIN sites ON shifts.site_id=sites.id
            LEFT JOIN users ON COALESCE(shifts.user_id, shifts.guard_id)=users.id
            WHERE shifts.company_id=?
            ORDER BY shift_date, start_time
        ''', (company_id,)).fetchall()
        sites = conn.execute('SELECT * FROM sites WHERE company_id=? ORDER BY name', (company_id,)).fetchall()
        clients = conn.execute('SELECT * FROM clients WHERE company_id=? ORDER BY name', (company_id,)).fetchall()
        reports = conn.execute('''
            SELECT reports.*, sites.name as site_name FROM reports
            JOIN sites ON reports.site_id=sites.id
            WHERE reports.company_id=?
            ORDER BY reports.created_at DESC
        ''', (company_id,)).fetchall()
        recent_reports = reports[:8]
        guards = conn.execute("SELECT * FROM users WHERE company_id=? AND role='guard' ORDER BY full_name", (company_id,)).fetchall()
        dar_recent = conn.execute('''
            SELECT d.*, s.name as site_name, u.full_name as officer_name
            FROM daily_activity_reports d
            JOIN sites s ON d.site_id=s.id
            JOIN users u ON d.officer_id=u.id
            WHERE d.company_id=?
            ORDER BY d.created_at DESC LIMIT 8
        ''', (company_id,)).fetchall()
        incident_recent = conn.execute('''
            SELECT i.*, s.name as site_name, u.full_name as officer_name
            FROM incident_reports i
            JOIN sites s ON i.site_id=s.id
            JOIN users u ON i.officer_id=u.id
            WHERE i.company_id=?
            ORDER BY i.created_at DESC LIMIT 8
        ''', (company_id,)).fetchall()
        if allowed_site_ids is not None:
            shifts = [row for row in shifts if row['site_id'] in allowed_site_ids]
            available_shifts = [row for row in available_shifts if row['site_id'] in allowed_site_ids]
            sites = [row for row in sites if row['id'] in allowed_site_ids]
            reports = [row for row in reports if row['site_id'] in allowed_site_ids]
            recent_reports = reports[:8]
            dar_recent = [row for row in dar_recent if row['site_id'] in allowed_site_ids]
            incident_recent = [row for row in incident_recent if row['site_id'] in allowed_site_ids]
            guard_rows = conn.execute('''
                SELECT DISTINCT u.* FROM users u
                JOIN shifts sh ON COALESCE(sh.user_id, sh.guard_id)=u.id
                WHERE u.company_id=? AND u.role='guard' AND sh.site_id IN ({})
                ORDER BY u.full_name
            '''.format(','.join(['?'] * max(1, len(allowed_site_ids)))), tuple([company_id] + sorted(allowed_site_ids))).fetchall() if allowed_site_ids else []
            guards = guard_rows
    if user['role'] == 'guard' and allowed_site_ids is not None:
        sites = [row for row in sites if row['id'] in allowed_site_ids]
        reports = [row for row in reports if row['site_id'] in allowed_site_ids]
        recent_reports = reports
        dar_recent = [row for row in dar_recent if row['site_id'] in allowed_site_ids]
        incident_recent = [row for row in incident_recent if row['site_id'] in allowed_site_ids]

    shift_form = {
        'site_id': (shift_form_values.get('site_id') or '').strip(),
        'user_id': (shift_form_values.get('user_id') or '').strip(),
        'shift_date': (shift_form_values.get('shift_date') or '').strip(),
        'start_time': (shift_form_values.get('start_time') or '').strip(),
        'end_time': (shift_form_values.get('end_time') or '').strip(),
        'notes': (shift_form_values.get('notes') or '').strip(),
    }
    if user['role'] in {'company_admin', 'superadmin', 'supervisor'} and not shift_form['shift_date']:
        shift_form['shift_date'] = today.isoformat()
    guard_option_rows = []
    guard_availability = {}
    if user['role'] in {'company_admin', 'superadmin', 'supervisor'}:
        guard_availability = guard_availability_metadata(
            conn,
            company_id,
            guards,
            shift_form.get('shift_date'),
            shift_form.get('start_time'),
            shift_form.get('end_time'),
            exclude_shift_id=shift_form_values.get('exclude_shift_id'),
        )
        for guard in guards:
            availability_state = guard_availability.get(guard['id']) or {'available': True, 'reason': '', 'overlap_shift': None}
            label_suffix = ''
            if availability_state['reason'] == 'approved_leave':
                label_suffix = ' (Unavailable - Approved Time Off)'
            elif availability_state['reason'] == 'overlap_shift' and availability_state.get('overlap_shift'):
                overlap = availability_state['overlap_shift']
                label_suffix = f" (Already scheduled {overlap['start_time']}-{overlap['end_time']})"
            guard_option_rows.append({
                'id': guard['id'],
                'full_name': guard['full_name'],
                'available': availability_state['available'],
                'reason': availability_state['reason'],
                'label_suffix': label_suffix,
            })
    guards_module_rows = conn.execute(f'''
        SELECT g.*, gsa.site_id, s.name as assigned_site_name, u.id as login_user_id, u.username as login_username, u.email as login_email, u.pin_hash as login_pin_hash
        FROM guards g
        {current_guard_assignment_join('g')}
        LEFT JOIN users u ON u.guard_id=g.id AND u.company_id=g.company_id AND u.role='guard'
        WHERE g.company_id=?
        ORDER BY g.created_at DESC
    ''', (company_id,)).fetchall()
    if allowed_site_ids is not None:
        guards_module_rows = [row for row in guards_module_rows if row['site_id'] in allowed_site_ids]
    active_sites = conn.execute('SELECT id, name FROM sites WHERE company_id=? AND active=1 ORDER BY name', (company_id,)).fetchall()
    if allowed_site_ids is not None:
        active_sites = [row for row in active_sites if row['id'] in allowed_site_ids]

    if user['role'] == 'guard':
        schedule_rows = [shift for shift in my_shifts if range_start.isoformat() <= shift['shift_date'] <= range_end.isoformat()]
        open_shift_alerts = available_shifts[:10]
        my_open_shift_options = available_shifts
    else:
        schedule_rows = conn.execute('''
            SELECT shifts.*, COALESCE(shifts.user_id, shifts.guard_id) as user_id, COALESCE(shifts.guard_id, shifts.user_id) as guard_id, users.full_name, sites.name as site_name
            FROM shifts
            LEFT JOIN users ON COALESCE(shifts.user_id, shifts.guard_id)=users.id
            JOIN sites ON shifts.site_id=sites.id
            WHERE shifts.company_id=? AND shift_date BETWEEN ? AND ?
            ORDER BY shift_date, start_time
        ''', (company_id, range_start.isoformat(), range_end.isoformat())).fetchall()
        if allowed_site_ids is not None:
            schedule_rows = [row for row in schedule_rows if row['site_id'] in allowed_site_ids]

        open_shift_alerts = conn.execute('''
            SELECT shifts.*, COALESCE(shifts.user_id, shifts.guard_id) as user_id, COALESCE(shifts.guard_id, shifts.user_id) as guard_id, sites.name as site_name
            FROM shifts JOIN sites ON shifts.site_id=sites.id
            WHERE shifts.company_id=? AND COALESCE(shifts.user_id, shifts.guard_id) IS NULL AND shift_date>=?
            ORDER BY shift_date, start_time LIMIT 10
        ''', (company_id, today.isoformat())).fetchall()
        if allowed_site_ids is not None:
            open_shift_alerts = [row for row in open_shift_alerts if row['site_id'] in allowed_site_ids]
        my_open_shift_options = []
    approved_time_off_lookup = approved_time_off_lookup_by_guard_and_date(
        conn,
        company_id,
        range_start.isoformat(),
        range_end.isoformat(),
    )
    schedule_rows_with_time_off = []
    for shift in schedule_rows:
        shift_data = dict(shift)
        assigned_guard_id = shift_data.get('user_id') or shift_data.get('guard_id')
        approved_leave = approved_time_off_lookup.get((assigned_guard_id, shift_data.get('shift_date'))) if assigned_guard_id else None
        overlap_shift = overlapping_shift_for_guard(
            conn,
            company_id,
            assigned_guard_id,
            shift_data.get('shift_date'),
            shift_data.get('start_time'),
            shift_data.get('end_time'),
            exclude_shift_id=shift_data.get('id'),
        ) if assigned_guard_id else None
        shift_data['has_approved_time_off'] = bool(approved_leave)
        shift_data['has_overlap_conflict'] = bool(overlap_shift)
        shift_data['approved_time_off_detail'] = (
            f"{(approved_leave.get('type') or 'approved').title()} leave "
            f"{approved_leave.get('start_date')} to {approved_leave.get('end_date')}"
        ) if approved_leave else ''
        shift_data['overlap_conflict_detail'] = (
            f"Overlaps {overlap_shift['shift_date']} {overlap_shift['start_time']}-{overlap_shift['end_time']}"
        ) if overlap_shift else ''
        if approved_leave:
            shift_data['conflict_status'] = 'on_leave'
        elif overlap_shift:
            shift_data['conflict_status'] = 'overlap_conflict'
        else:
            shift_data['conflict_status'] = ''
        schedule_rows_with_time_off.append(shift_data)
    schedule_rows = schedule_rows_with_time_off

    swap_requests = conn.execute('''
        SELECT ssr.*, s.shift_date, s.start_time, s.end_time, st.name as site_name,
               u1.full_name as requested_by_name, u2.full_name as requested_to_name
        FROM shift_swap_requests ssr
        JOIN shifts s ON ssr.shift_id=s.id
        JOIN sites st ON s.site_id=st.id
        JOIN users u1 ON ssr.requested_by=u1.id
        LEFT JOIN users u2 ON ssr.requested_to=u2.id
        WHERE ssr.company_id=?
        ORDER BY ssr.created_at DESC LIMIT 10
    ''', (company_id,)).fetchall()
    if allowed_site_ids is not None:
        swap_requests = [row for row in swap_requests if row['site_id'] in allowed_site_ids]

    time_corrections = conn.execute('''
        SELECT tc.*, s.site_id, s.shift_date, s.start_time, s.end_time, u.full_name as requested_by_name
        FROM time_corrections tc
        JOIN shifts s ON tc.shift_id=s.id
        JOIN users u ON tc.requested_by=u.id
        WHERE tc.company_id=?
        ORDER BY tc.created_at DESC LIMIT 10
    ''', (company_id,)).fetchall()
    if allowed_site_ids is not None:
        time_corrections = [row for row in time_corrections if row.get('site_id') in allowed_site_ids]
    my_time_off_requests = conn.execute('''
        SELECT tor.*, u.full_name as guard_name, reviewer.full_name as reviewed_by_name, reviewer.role as reviewed_by_role
        FROM time_off_requests tor
        JOIN users u ON tor.guard_id=u.id
        LEFT JOIN users reviewer ON tor.reviewed_by=reviewer.id
        WHERE tor.company_id=? AND tor.guard_id=?
        ORDER BY tor.created_at DESC
    ''', (company_id, user['id'])).fetchall()
    admin_time_off_requests = conn.execute('''
        SELECT tor.*, u.full_name as guard_name, reviewer.full_name as reviewed_by_name, reviewer.role as reviewed_by_role
        FROM time_off_requests tor
        JOIN users u ON tor.guard_id=u.id
        LEFT JOIN users reviewer ON tor.reviewed_by=reviewer.id
        WHERE tor.company_id=?
        ORDER BY tor.created_at DESC
    ''', (company_id,)).fetchall()
    if user['role'] == 'supervisor':
        admin_time_off_requests = [
            row for row in admin_time_off_requests
            if (row.get('status') or '').strip().lower() == 'pending'
        ]
    elif allowed_site_ids is not None:
        if allowed_site_ids:
            placeholders = ','.join(['?'] * len(allowed_site_ids))
            admin_time_off_requests = conn.execute(
                f'''
                SELECT tor.*, u.full_name as guard_name, reviewer.full_name as reviewed_by_name, reviewer.role as reviewed_by_role
                FROM time_off_requests tor
                JOIN users u ON tor.guard_id=u.id
                LEFT JOIN users reviewer ON tor.reviewed_by=reviewer.id
                WHERE tor.company_id=?
                  AND (
                    EXISTS (
                        SELECT 1
                        FROM guard_site_assignments gsa
                        WHERE gsa.company_id=tor.company_id
                          AND gsa.guard_id=tor.guard_id
                          AND gsa.site_id IN ({placeholders})
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM shifts sh
                        WHERE sh.company_id=tor.company_id
                          AND COALESCE(sh.user_id, sh.guard_id)=tor.guard_id
                          AND sh.shift_date BETWEEN tor.start_date AND tor.end_date
                          AND sh.site_id IN ({placeholders})
                    )
                  )
                ORDER BY tor.created_at DESC
                ''',
                tuple([company_id] + sorted(allowed_site_ids) + sorted(allowed_site_ids)),
            ).fetchall()
        else:
            admin_time_off_requests = []

    checkpoints = conn.execute('''
        SELECT pc.*, u.full_name, s.name as site_name
        FROM patrol_checkpoints pc
        JOIN users u ON pc.user_id=u.id
        JOIN sites s ON pc.site_id=s.id
        WHERE pc.company_id=?
        ORDER BY pc.check_time DESC LIMIT 10
    ''', (company_id,)).fetchall()

    availability = conn.execute('''
        SELECT a.*, u.full_name FROM availability a JOIN users u ON a.user_id=u.id
        WHERE a.company_id=? ORDER BY u.full_name, weekday
    ''', (company_id,)).fetchall()

    stats = {
        'guards_on_duty': fetch_scalar(conn, '''
            SELECT COUNT(DISTINCT COALESCE(sh.user_id, sh.guard_id)) AS cnt
            FROM shifts sh
            WHERE sh.company_id=? AND sh.shift_date=? AND COALESCE(sh.user_id, sh.guard_id) IS NOT NULL
        ''', (company_id, today.isoformat())),
        'open_incidents': fetch_scalar(conn, '''
            SELECT COUNT(*) AS cnt FROM incident_reports WHERE company_id=? AND LOWER(COALESCE(status, 'open'))!='closed'
        ''', (company_id,)),
        'sites_active_today': fetch_scalar(conn, '''
            SELECT COUNT(DISTINCT site_id) AS cnt FROM shifts WHERE company_id=? AND shift_date=?
        ''', (company_id, today.isoformat())),
        'recent_reports': fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM reports WHERE company_id=? AND date(created_at)>=date(?)', (company_id, (today - timedelta(days=7)).isoformat())),
        'checkpoint_logs_today': fetch_scalar(conn, 'SELECT COUNT(*) AS cnt FROM patrol_checkpoints WHERE company_id=? AND date(check_time)=date(?)', (company_id, today.isoformat())),
        'weekly_hours': fetch_scalar(conn, 'SELECT COALESCE(SUM(worked_hours),0) AS cnt FROM shifts WHERE company_id=? AND shift_date BETWEEN ? AND ?', (company_id, (today - timedelta(days=today.weekday())).isoformat(), (today - timedelta(days=today.weekday()) + timedelta(days=6)).isoformat())),
        'active_patrols': fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status='in_progress'", (company_id,)),
        'completed_tours': fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status='completed' AND date(completed_at)=date(?)", (company_id, today.isoformat())),
        'missed_checkpoints': fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status NOT IN ('completed','excused') AND date(started_at)=date(?)", (company_id, today.isoformat())),
        'excused_patrols': fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status='excused' AND date(excused_at)=date(?)", (company_id, today.isoformat())),
    }
    if user['role'] == 'guard':
        stats['active_patrols'] = fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND guard_id=? AND status='in_progress'", (company_id, user['id']))
        stats['completed_tours'] = fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND guard_id=? AND status='completed' AND date(completed_at)=date(?)", (company_id, user['id'], today.isoformat()))
        stats['missed_checkpoints'] = fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND guard_id=? AND status NOT IN ('completed','excused') AND date(started_at)=date(?)", (company_id, user['id'], today.isoformat()))
        stats['excused_patrols'] = fetch_scalar(conn, "SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND guard_id=? AND status='excused' AND date(excused_at)=date(?)", (company_id, user['id'], today.isoformat()))
    if allowed_site_ids is not None:
        site_params = tuple(sorted(allowed_site_ids))
        if site_params:
            placeholders = ','.join(['?'] * len(site_params))
            stats['guards_on_duty'] = fetch_scalar(
                conn,
                f'''SELECT COUNT(DISTINCT COALESCE(sh.user_id, sh.guard_id)) AS cnt
                    FROM shifts sh
                    WHERE sh.company_id=? AND sh.shift_date=? AND COALESCE(sh.user_id, sh.guard_id) IS NOT NULL
                    AND sh.site_id IN ({placeholders})''',
                (company_id, today.isoformat(), *site_params),
            )
            stats['open_incidents'] = fetch_scalar(
                conn,
                f"SELECT COUNT(*) AS cnt FROM incident_reports WHERE company_id=? AND LOWER(COALESCE(status, 'open'))!='closed' AND site_id IN ({placeholders})",
                (company_id, *site_params),
            )
            stats['active_patrols'] = fetch_scalar(conn, f"SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status='in_progress' AND site_id IN ({placeholders})", (company_id, *site_params))
            stats['completed_tours'] = fetch_scalar(conn, f"SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status='completed' AND date(completed_at)=date(?) AND site_id IN ({placeholders})", (company_id, today.isoformat(), *site_params))
            stats['missed_checkpoints'] = fetch_scalar(conn, f"SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status NOT IN ('completed','excused') AND date(started_at)=date(?) AND site_id IN ({placeholders})", (company_id, today.isoformat(), *site_params))
            stats['excused_patrols'] = fetch_scalar(conn, f"SELECT COUNT(*) AS cnt FROM patrol_tour_runs WHERE company_id=? AND status='excused' AND date(excused_at)=date(?) AND site_id IN ({placeholders})", (company_id, today.isoformat(), *site_params))
        else:
            stats['guards_on_duty'] = 0
            stats['open_incidents'] = 0
            stats['active_patrols'] = 0
            stats['completed_tours'] = 0
            stats['missed_checkpoints'] = 0
            stats['excused_patrols'] = 0
    guard_dashboard_summary = {
        'current_shift': None,
        'assigned_site': None,
        'hours_worked_week': 0,
        'open_reports': 0,
    }
    if user['role'] == 'guard':
        now_time = datetime.now().strftime('%H:%M')
        today_shift = next((shift for shift in my_shifts if shift['shift_date'] == today.isoformat()), None)
        active_shift = next(
            (
                shift for shift in my_shifts
                if shift['shift_date'] == today.isoformat() and shift['start_time'] <= now_time <= shift['end_time']
            ),
            None,
        )
        guard_dashboard_summary['current_shift'] = active_shift or today_shift
        assigned_site = guard_primary_assigned_site(conn, user, preferred_site_id=row_value(user, 'session_site_id'))
        guard_dashboard_summary['assigned_site'] = assigned_site['name'] if assigned_site else 'No site assigned.'
        guard_dashboard_summary['assigned_site_id'] = assigned_site['id'] if assigned_site else None
        guard_dashboard_summary['hours_worked_week'] = fetch_scalar(
            conn,
            'SELECT COALESCE(SUM(worked_hours),0) AS cnt FROM shifts WHERE company_id=? AND COALESCE(user_id, guard_id)=? AND shift_date BETWEEN ? AND ?',
            (
                company_id,
                user['id'],
                (today - timedelta(days=today.weekday())).isoformat(),
                (today - timedelta(days=today.weekday()) + timedelta(days=6)).isoformat(),
            ),
        )
        guard_dashboard_summary['open_reports'] = fetch_scalar(
            conn,
            'SELECT COUNT(*) AS cnt FROM reports WHERE company_id=? AND officer_name=? AND status!=?',
            (company_id, user['full_name'], 'closed'),
        )

    overtime_rows = conn.execute('''
        SELECT u.full_name, COALESCE(SUM(s.worked_hours),0) as total_hours
        FROM users u LEFT JOIN shifts s ON u.id=COALESCE(s.user_id, s.guard_id) AND s.shift_date BETWEEN ? AND ?
        WHERE u.company_id=? AND u.role='guard'
        GROUP BY u.id ORDER BY total_hours DESC
    ''', ((today - timedelta(days=today.weekday())).isoformat(), (today - timedelta(days=today.weekday()) + timedelta(days=6)).isoformat(), company_id)).fetchall()
    overtime_alerts = [row for row in overtime_rows if row['total_hours'] > 40]
    staff_users = conn.execute(
        """
        SELECT id, full_name, username, email, role, active
        FROM users
        WHERE company_id=? AND role IN ('guard', 'supervisor', 'company_admin', 'admin', 'superadmin')
        ORDER BY
            CASE role
                WHEN 'superadmin' THEN 0
                WHEN 'company_admin' THEN 1
                WHEN 'admin' THEN 1
                WHEN 'supervisor' THEN 2
                WHEN 'guard' THEN 3
                ELSE 4
            END,
            full_name
        """,
        (company_id,),
    ).fetchall()
    supervisor_assignments = conn.execute(
        '''
        SELECT ssa.supervisor_user_id, ssa.site_id, s.name as site_name
        FROM supervisor_site_assignments ssa
        JOIN sites s ON s.id=ssa.site_id
        WHERE ssa.company_id=?
        ''',
        (company_id,),
    ).fetchall()
    supervisor_sites_by_user = {}
    for row in supervisor_assignments:
        supervisor_sites_by_user.setdefault(row['supervisor_user_id'], []).append({'id': row['site_id'], 'name': row['site_name']})
    staff_users = [dict(row) for row in staff_users]
    for row in staff_users:
        row['supervisor_sites'] = supervisor_sites_by_user.get(row['id'], [])

    patrol_data = patrol_dashboard_data(conn, user)
    conn.close()
    return {
        'stats': stats,
        **patrol_data,
        'shifts': shifts,
        'my_shifts': my_shifts if user['role'] == 'guard' else [],
        'available_shifts': available_shifts if user['role'] == 'guard' else [],
        'sites': sites,
        'active_sites': active_sites,
        'clients': clients,
        'reports': reports,
        'recent_reports': normalized_recent_reports(recent_reports, dar_recent, incident_recent),
        'dar_recent': dar_recent,
        'incident_recent': incident_recent,
        'guards': guards,
        'guards_module_rows': guards_module_rows,
        'schedule_rows': schedule_rows,
        'range_start': range_start,
        'range_end': range_end,
        'schedule_view': view,
        'open_shift_alerts': open_shift_alerts,
        'my_open_shift_options': my_open_shift_options,
        'swap_requests': swap_requests,
        'time_corrections': time_corrections,
        'my_time_off_requests': my_time_off_requests,
        'admin_time_off_requests': admin_time_off_requests,
        'checkpoints': checkpoints,
        'availability': availability,
        'overtime_alerts': overtime_alerts,
        'staff_users': staff_users,
        'shift_form': shift_form,
        'guard_option_rows': guard_option_rows,
        'guard_availability': guard_availability,
        'guard_dashboard_summary': guard_dashboard_summary,
        'patrol_excuse_reasons': PATROL_EXCUSE_REASONS,
        'supervisor_dashboard_summary': {
            'open_incidents': stats['open_incidents'],
            'guards_on_duty': stats['guards_on_duty'],
            'pending_approvals': len([r for r in time_corrections if (r.get('status') or '').lower() == 'pending']) + len([r for r in admin_time_off_requests if (r.get('status') or '').lower() == 'pending']),
            'upcoming_schedule_gaps': len(open_shift_alerts),
        },
    }


def login_page(start_response, error=None):
    return html_response(start_response, render('login.html', title=PRODUCT_FULL_NAME, error=error))


def log_route_exception(route_name, exc):
    print(f"[route_error] route={route_name} error={exc}", flush=True)
    print(traceback.format_exc(), flush=True)


def dashboard_error_page(start_response):
    body = b"<h1>Dashboard failed to load. Check server logs.</h1><p>Please try again in a moment.</p>"
    return html_response(start_response, body, status='500 Internal Server Error')


def export_reports_pdf(company_id):
    conn = db()
    company = conn.execute('SELECT * FROM companies WHERE id=?', (company_id,)).fetchone()
    rows = conn.execute('''
        SELECT r.*, s.name as site_name, s.client_company_name FROM reports r
        JOIN sites s ON r.site_id=s.id
        WHERE r.company_id=? ORDER BY r.created_at DESC
    ''', (company_id,)).fetchall()
    conn.close()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.5 * inch, bottomMargin=0.5 * inch)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='Muted', parent=styles['BodyText'], textColor=colors.HexColor('#666666')))
    story = [
        Paragraph('SteeleOps Control Center', styles['Title']),
        Paragraph(company['name'], styles['Heading2']),
        Paragraph('Security Operations Simplified', styles['Muted']),
        Spacer(1, 12),
    ]
    if not rows:
        story.append(Paragraph('No reports available for this company.', styles['BodyText']))
    else:
        for row in rows:
            meta = [
                ['Report Type', row['report_type'].title()],
                ['Status', row['status'].title()],
                ['Priority', row['priority'].title()],
                ['Date', row['report_date']],
                ['Time', row['report_time']],
                ['Site', row['site_name']],
                ['Client', row['client_company_name'] or '—'],
                ['Officer', row['officer_name']],
            ]
            table = Table(meta, colWidths=[1.4 * inch, 4.8 * inch])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.whitesmoke),
                ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#c0c0c0')),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f3f4f6')),
                ('PADDING', (0, 0), (-1, -1), 6),
            ]))
            story.append(table)
            story.append(Spacer(1, 8))
            story.append(Paragraph(f"<b>Summary:</b> {row['summary']}", styles['BodyText']))
            story.append(Paragraph(f"<b>Attachment:</b> {row['attachment_name'] or 'None'} | <b>Photo:</b> {row['photo_name'] or 'None'}", styles['Muted']))
            story.append(Paragraph(f"<b>Created:</b> {row['created_at']}", styles['Muted']))
            story.append(Spacer(1, 14))
    doc.build(story)
    return buffer.getvalue()


def payroll_rows(company_id, start_date, end_date, guard_id=None):
    conn = db()
    params = [company_id, start_date, end_date]
    guard_filter = ''
    if guard_id:
        guard_filter = ' AND u.id=? '
        params.append(guard_id)
    rows = conn.execute(f'''
        SELECT u.id as guard_id, u.full_name, u.email, u.hourly_rate, COALESCE(MAX(si.name),'Multiple') AS site_name,
               COUNT(s.id) as shifts_count,
               COALESCE(SUM(s.worked_hours), 0) as total_hours,
               COALESCE(SUM(CASE WHEN s.worked_hours > 8 THEN s.worked_hours - 8 ELSE 0 END),0) as overtime_hours,
               COALESCE(SUM(s.worked_hours * u.hourly_rate), 0) as gross_pay
        FROM users u
        LEFT JOIN shifts s ON u.id=COALESCE(s.user_id, s.guard_id) AND s.shift_date BETWEEN ? AND ?
        LEFT JOIN sites si ON si.id=s.site_id
        WHERE u.company_id=? AND u.role='guard' {guard_filter}
        GROUP BY u.id
        ORDER BY u.full_name
    ''', [start_date, end_date, company_id] + ([guard_id] if guard_id else [])).fetchall()
    conn.close()
    return rows


def parse_payroll_period_dates(start_date_raw, end_date_raw):
    today = date.today()
    default_start = (today - timedelta(days=today.weekday()))
    default_end = default_start + timedelta(days=13)
    try:
        start_value = datetime.strptime((start_date_raw or '').strip(), '%Y-%m-%d').date() if start_date_raw else default_start
    except Exception:
        start_value = default_start
    try:
        end_value = datetime.strptime((end_date_raw or '').strip(), '%Y-%m-%d').date() if end_date_raw else default_end
    except Exception:
        end_value = default_end
    if end_value < start_value:
        end_value = start_value
    return start_value.isoformat(), end_value.isoformat()




def get_payroll_display_values(payroll_row, guard_record=None):
    pay_rate = float((payroll_row or {}).get('hourly_rate') or 0)
    clock_overtime_hours = float((payroll_row or {}).get('overtime_hours') or 0)
    clock_total_hours = float((payroll_row or {}).get('total_hours') or 0)
    clock_regular_hours = float(max(clock_total_hours - clock_overtime_hours, 0))

    manual_override_used = bool(guard_record and guard_record.get('manual_override_used'))
    if manual_override_used:
        regular_hours = float(guard_record.get('regular_hours') or 0)
        overtime_hours = float(guard_record.get('overtime_hours') or 0)
        source = 'manual_override'
    elif clock_total_hours > 0:
        regular_hours = clock_regular_hours
        overtime_hours = clock_overtime_hours
        source = 'clock_records'
    else:
        regular_hours = 0.0
        overtime_hours = 0.0
        source = 'zero'

    total_hours = regular_hours + overtime_hours
    overtime_rate = pay_rate * 1.5
    estimated_gross_pay = (regular_hours * pay_rate) + (overtime_hours * overtime_rate)
    return {
        'regular_hours': regular_hours,
        'overtime_hours': overtime_hours,
        'total_hours': total_hours,
        'estimated_gross_pay': estimated_gross_pay,
        'source': source,
    }
def payroll_guard_record_map(conn, company_id, period_id):
    if not period_id:
        return {}
    rows = conn.execute('SELECT * FROM payroll_guard_records WHERE company_id=? AND period_id=?', (company_id, period_id)).fetchall()
    return {row['guard_id']: row for row in rows}


def quickbooks_payroll_payload(company_id, start_date, end_date, guard_record_map=None):
    rows = payroll_rows(company_id, start_date, end_date)
    payload = []
    for row in rows:
        guard_record = (guard_record_map or {}).get(row['guard_id'])
        display = get_payroll_display_values(row, guard_record)
        payload.append({
            'employee_name': row['full_name'],
            'total_hours': round(float(display['total_hours']), 2),
            'regular_hours': round(float(display['regular_hours']), 2),
            'overtime_hours': round(float(display['overtime_hours']), 2),
            'pay_rate': round(float(row['hourly_rate'] or 0), 2),
        })
    return payload


def payroll_csv(company_id, start_date, end_date, guard_record_map=None):
    rows = payroll_rows(company_id, start_date, end_date)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Employee Name', 'Employee Email', 'Pay Period Start', 'Pay Period End', 'Regular Hours', 'Overtime Hours', 'Pay Rate', 'Overtime Rate', 'Gross Pay Estimate', 'Site / Location', 'Notes'])
    for row in rows:
        guard_record = (guard_record_map or {}).get(row['guard_id'])
        pay_rate = float(row['hourly_rate'] or 0)
        overtime_rate = pay_rate * 1.5

        if guard_record and guard_record.get('manual_override_used'):
            regular_hours = float(guard_record.get('regular_hours') or 0)
            overtime_hours = float(guard_record.get('overtime_hours') or 0)
        elif guard_record and str(guard_record.get('status') or '').lower() == 'approved':
            regular_hours = float(guard_record.get('regular_hours') or 0)
            overtime_hours = float(guard_record.get('overtime_hours') or 0)
        else:
            regular_hours = max(float(row['total_hours'] or 0) - float(row['overtime_hours'] or 0), 0)
            overtime_hours = float(row['overtime_hours'] or 0)

        total_hours = regular_hours + overtime_hours
        gross_pay = (regular_hours * pay_rate) + (overtime_hours * overtime_rate)
        print('Exporting payroll:', row['full_name'], regular_hours, overtime_hours, gross_pay)

        writer.writerow([
            row['full_name'],
            row.get('email', ''),
            start_date,
            end_date,
            round(regular_hours, 2),
            round(overtime_hours, 2),
            round(pay_rate, 2),
            round(overtime_rate, 2),
            round(gross_pay, 2),
            row.get('site_name', 'Multiple'),
            f'Prepared in SteeleOps for QuickBooks processing only. Total Hours: {round(total_hours, 2)}',
        ])
    return output.getvalue().encode('utf-8')


def payroll_period_status(conn, period_id):
    period = conn.execute('SELECT * FROM payroll_periods WHERE id=?', (period_id,)).fetchone()
    if not period:
        return 'Pending Approval'
    if period['status'] == 'sent_to_quickbooks':
        return 'Sent to QuickBooks'
    pending = conn.execute("SELECT COUNT(*) AS cnt FROM payroll_guard_records WHERE period_id=? AND status NOT IN ('approved','excluded','sent_to_quickbooks')", (period_id,)).fetchone()
    return 'Ready to Process' if pending and pending['cnt'] == 0 else 'Pending Approval'


def application(environ, start_response):
    init_db()
    start_missed_clock_scheduler_once()
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET').upper()
    user = get_current_user(environ)
    query = parse_query(environ)

    if path in {'/health', '/healthz', '/ready', '/readyz'}:
        return json_response(start_response, {'status': 'ok', 'service': 'steeleops'})
    if path.startswith('/static/') or path.startswith('/uploads/'):
        return serve_static(environ, start_response, path.lstrip('/'))

    if path == '/':
        try:
            return redirect(start_response, '/dashboard' if user else '/login')
        except Exception as exc:
            log_route_exception('/', exc)
            return html_response(start_response, b'<h1>Something went wrong. Please try again.</h1>', status='500 Internal Server Error')

    if path == '/login' and method == 'GET':
        return login_page(start_response)

    if path == '/login' and method == 'POST':
        data, _ = parse_post(environ)
        username = data.get('username', '').strip()
        password = data.get('password', '')
        if not login_allowed(username):
            return login_page(start_response, 'Too many failed attempts. Please wait 15 minutes and try again.')
        conn = db()
        found = conn.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
        conn.close()
        if not found or not verify_password(password, found['password']):
            record_login_attempt(username, False)
            return login_page(start_response, 'Invalid username or password.')
        record_login_attempt(username, True)
        session_id = create_session(found['id'])
        return redirect(start_response, '/dashboard', [('Set-Cookie', cookie_header(session_id))])

    if path == '/logout':
        _, sid = get_session(environ)
        destroy_session(sid)
        return redirect(start_response, '/login', [('Set-Cookie', delete_cookie_header())])

    if path == '/dashboard':
        try:
            user, response = require_login(environ, start_response)
            if response:
                return response
            ctx = get_dashboard_context(user, query.get('view', 'week'))
            return html_response(start_response, render('dashboard.html', title=PRODUCT_FULL_NAME, user=user, **ctx))
        except Exception as exc:
            log_route_exception('/dashboard', exc)
            return dashboard_error_page(start_response)

    if path == '/profile':
        user, response = require_login(environ, start_response)
        if response:
            return response
        return profile_page(start_response, user)

    if path == '/profile/update' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        conn.execute('UPDATE users SET full_name=?, phone=?, email=?, license_number=? WHERE id=?', (
            data.get('full_name', user['full_name']), data.get('phone', ''), data.get('email', ''), data.get('license_number', ''), user['id']
        ))
        conn.commit(); conn.close()
        return redirect(start_response, '/profile')

    if path == '/profile/password' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        current_password = data.get('current_password', '')
        new_password = data.get('new_password', '')
        if len(new_password) < 6 or not verify_password(current_password, user['password']):
            fresh_user = get_current_user(environ)
            return profile_page(start_response, fresh_user, error='Password change failed. Check current password and use at least 6 characters.')
        conn = db()
        conn.execute('UPDATE users SET password=? WHERE id=?', (hash_password(new_password), user['id']))
        conn.commit(); conn.close()
        fresh_user = get_current_user(environ)
        return profile_page(start_response, fresh_user, message='Password updated successfully.')

    if path == '/availability/save' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        for weekday in range(7):
            is_available = 1 if data.get(f'available_{weekday}') == 'on' else 0
            start = data.get(f'start_{weekday}', '08:00')
            end = data.get(f'end_{weekday}', '20:00')
            conn.execute('''
                UPDATE availability SET available_start=?, available_end=?, is_available=? WHERE user_id=? AND weekday=?
            ''', (start, end, is_available, user['id'], weekday))
        conn.commit(); conn.close()
        return redirect(start_response, '/profile')

    if path == '/shift/clock' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        shift_id = data.get('shift_id')
        action = data.get('action')
        if not shift_id or action not in {'in', 'out'}:
            return bad_request(start_response)
        conn = db()
        shift = conn.execute('SELECT * FROM shifts WHERE id=?', (shift_id,)).fetchone()
        if not shift or not require_company_access(user, shift['company_id']) or (user['role'] == 'guard' and shift_assignment_value(shift) != user['id']):
            conn.close()
            return bad_request(start_response, 'Shift not accessible')
        timestamp = utc_now_str()
        if action == 'in':
            conn.execute('UPDATE shifts SET clock_in_time=?, status=? WHERE id=?', (timestamp, 'clocked_in', shift_id))
        else:
            worked = calculate_worked_hours(shift['clock_in_time'], timestamp)
            conn.execute('UPDATE shifts SET clock_out_time=?, status=?, worked_hours=?, overtime_alert=? WHERE id=?', (
                timestamp, 'completed', worked, 1 if worked > 8 else 0, shift_id
            ))
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/report/new' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, files = parse_post(environ)
        required = ['report_type', 'report_date', 'report_time', 'site_id', 'summary']
        if not all(data.get(k) for k in required):
            return bad_request(start_response, 'Missing fields')
        company_id = user['company_id']
        if not company_id:
            return bad_request(start_response, 'User has no company.')
        attachment_name, attachment_path = save_upload(files.get('attachment'), 'attachments') if files.get('attachment') else (None, None)
        photo_name, photo_path = save_upload(files.get('photo'), 'photos') if files.get('photo') else (None, None)
        officer_name = user['full_name'] if user['role'] == 'guard' else data.get('officer_name', user['full_name'])
        conn = db()
        conn.execute('''
            INSERT INTO reports (company_id, report_type, report_date, report_time, site_id, officer_name, summary, status, priority, attachment_name, attachment_path, photo_name, photo_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (company_id, data['report_type'], data['report_date'], data['report_time'], int(data['site_id']), officer_name,
              data['summary'], data.get('status', 'open'), data.get('priority', 'medium'), attachment_name, attachment_path, photo_name, photo_path,
              utc_now_str()))
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/checkpoint/new' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        conn.execute('''
            INSERT INTO patrol_checkpoints (company_id, user_id, site_id, checkpoint_name, check_time, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user['company_id'], user['id'], data.get('site_id'), data.get('checkpoint_name'), data.get('check_time') or utc_now_str(), data.get('notes', '')))
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/swap/request' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        conn.execute('''
            INSERT INTO shift_swap_requests (company_id, shift_id, requested_by, requested_to, status, notes, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
        ''', (user['company_id'], data.get('shift_id'), user['id'], data.get('requested_to') or None, data.get('notes', ''), utc_now_str()))
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/swap/approve' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        req = conn.execute('SELECT * FROM shift_swap_requests WHERE id=? AND company_id=?', (data.get('request_id'), user['company_id'])).fetchone()
        if req:
            if data.get('decision') == 'approved' and req['requested_to']:
                target_shift = conn.execute('SELECT id, shift_date, start_time, end_time FROM shifts WHERE id=? AND company_id=?', (req['shift_id'], user['company_id'])).fetchone()
                conflict = approved_time_off_request_for_date(conn, user['company_id'], req['requested_to'], target_shift['shift_date'] if target_shift else None)
                if conflict:
                    guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (req['requested_to'], user['company_id'])).fetchone()
                    conn.close()
                    return redirect_with_feedback(
                        start_response,
                        '/dashboard',
                        error=approved_time_off_conflict_error(target_shift['shift_date'], conflict, guard['full_name'] if guard else None),
                    )
                overlap_conflict = overlapping_shift_for_guard(
                    conn,
                    user['company_id'],
                    req['requested_to'],
                    target_shift['shift_date'] if target_shift else None,
                    target_shift['start_time'] if target_shift else None,
                    target_shift['end_time'] if target_shift else None,
                    exclude_shift_id=req['shift_id'],
                )
                if overlap_conflict:
                    guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (req['requested_to'], user['company_id'])).fetchone()
                    conn.close()
                    return redirect_with_feedback(
                        start_response,
                        '/dashboard',
                        error=overlapping_shift_conflict_error(overlap_conflict, guard['full_name'] if guard else None),
                    )
                assignment_clause, _ = shift_assignment_update_clause(conn)
                if assignment_clause:
                    conn.execute(f'UPDATE shifts SET {assignment_clause}, status="assigned" WHERE id=?', tuple([req['requested_to']] * len(shift_assignment_columns(conn)) + [req['shift_id']]))
                else:
                    conn.execute('UPDATE shifts SET status="assigned" WHERE id=?', (req['shift_id'],))
                conn.execute('UPDATE shift_swap_requests SET status="approved" WHERE id=?', (req['id'],))
            else:
                conn.execute('UPDATE shift_swap_requests SET status="declined" WHERE id=?', (req['id'],))
            conn.commit()
        conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/time-correction/request' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        conn.execute('''
            INSERT INTO time_corrections (company_id, shift_id, requested_by, requested_clock_in, requested_clock_out, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user['company_id'], data.get('shift_id'), user['id'], data.get('requested_clock_in'), data.get('requested_clock_out'), data.get('reason', ''), utc_now_str()))
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/time-correction/approve' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        req = conn.execute('SELECT * FROM time_corrections WHERE id=? AND company_id=?', (data.get('request_id'), user['company_id'])).fetchone()
        if req:
            if data.get('decision') == 'approved':
                worked = calculate_worked_hours(req['requested_clock_in'], req['requested_clock_out'])
                conn.execute('''UPDATE shifts SET clock_in_time=?, clock_out_time=?, worked_hours=?, overtime_alert=? WHERE id=?''', (
                    req['requested_clock_in'], req['requested_clock_out'], worked, 1 if worked > 8 else 0, req['shift_id']))
                conn.execute('UPDATE time_corrections SET status="approved" WHERE id=?', (req['id'],))
            else:
                conn.execute('UPDATE time_corrections SET status="declined" WHERE id=?', (req['id'],))
            conn.commit()
        conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/guards':
        user, response = require_admin(environ, start_response)
        if response: return response
        html = render_page(environ, 'dashboard.html', title='SteeleOps Guards', user=user, **get_dashboard_context(user, query.get('view', 'week')))
        return html_response(start_response, html, extra_headers=csrf_headers(environ))
    if path == '/admin/guards/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        first_name = (data.get('first_name') or '').strip()
        last_name = (data.get('last_name') or '').strip()
        if not first_name or not last_name:
            return bad_request(start_response, 'First and last name are required')
        conn = db()
        try:
            insert_guard(
                conn,
                user['company_id'],
                first_name=first_name,
                last_name=last_name,
                phone=data.get('phone', ''),
                email=data.get('email', ''),
                license_number=data.get('license_number', ''),
                status=data.get('status', 'active') if data.get('status') in ('active', 'inactive') else 'active',
                rating=float(data.get('rating') or 5),
                training_status=data.get('training_status') or '',
                created_at=utc_now_str(),
            )
            new_row = conn.execute('SELECT * FROM guards WHERE company_id=? ORDER BY id DESC LIMIT 1', (user['company_id'],)).fetchone()
            new_id = new_row['id'] if new_row else None
            if new_row:
                upsert_guard_login(conn, new_row, guard_login_payload(data, new_row, user['company_id']))
            conn.commit()
        except ValueError as exc:
            conn.rollback(); conn.close(); return bad_request(start_response, str(exc))
        conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=new_id, message='guard profile created', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/update' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (data.get('guard_id'), user['company_id'])).fetchone()
        if not guard:
            conn.close(); return bad_request(start_response, 'Guard not found')
        guard_name, guard_first_name, guard_last_name = guard_name_parts(
            first_name=data.get('first_name') or guard['first_name'],
            last_name=data.get('last_name') or guard['last_name'],
        )
        guard_params = [guard_first_name, guard_last_name, data.get('phone', guard['phone'] or '').strip(), data.get('email', guard['email'] or '').strip(), data.get('license_number', guard['license_number'] or '').strip(), data.get('status', guard['status']) if data.get('status', guard['status']) in ('active', 'inactive') else guard['status'], data.get('training_status', guard['training_status'] or '').strip(), guard['id']]
        guard_sql = 'UPDATE guards SET first_name=?, last_name=?, phone=?, email=?, license_number=?, status=?, training_status=? WHERE id=?'
        if 'name' in column_names(conn, 'guards'):
            guard_sql = 'UPDATE guards SET name=?, first_name=?, last_name=?, phone=?, email=?, license_number=?, status=?, training_status=? WHERE id=?'
            guard_params.insert(0, guard_name)
        try:
            conn.execute(guard_sql, tuple(guard_params))
            save_guard_site_assignment(conn, user['company_id'], guard['id'], data.get('site_id'))
            updated_guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (guard['id'], user['company_id'])).fetchone()
            upsert_guard_login(conn, updated_guard, guard_login_payload(data, updated_guard, user['company_id']))
            conn.commit()
        except ValueError as exc:
            conn.rollback(); conn.close(); return bad_request(start_response, str(exc))
        conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=guard['id'], message='guard profile updated', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/deactivate' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (data.get('guard_id'), user['company_id'])).fetchone()
        if not guard:
            conn.close(); return bad_request(start_response, 'Guard not found')
        conn.execute("UPDATE guards SET status='inactive' WHERE id=? AND company_id=?", (guard['id'], user['company_id']))
        conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=guard['id'], message='guard deactivated', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/assign' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (data.get('guard_id'), user['company_id'])).fetchone()
        if not guard:
            conn.close(); return bad_request(start_response, 'Guard not found')
        try:
            save_guard_site_assignment(conn, user['company_id'], guard['id'], data.get('site_id'))
            conn.commit()
        except ValueError as exc:
            conn.rollback(); conn.close(); return bad_request(start_response, str(exc))
        conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=guard['id'], message='guard assignment updated', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        try:
            conn.execute('''
                INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at)
                VALUES (?, ?, ?, ?, 'guard', ?, ?, ?, ?, 1, ?)
            ''', (user['company_id'], data.get('username'), hash_password(data.get('password', 'guard123')), data.get('full_name'), data.get('phone', ''), data.get('email', ''), data.get('license_number', ''), float(data.get('hourly_rate') or 18), utc_now_str()))
            new_row = conn.execute('SELECT id FROM users WHERE username=?', (data.get('username'),)).fetchone()
            new_id = new_row['id'] if new_row else None
            for weekday in range(7):
                conn.execute('''INSERT INTO availability (company_id, user_id, weekday, available_start, available_end, is_available) VALUES (?, ?, ?, '08:00', '20:00', 1)''', (user['company_id'], new_id, weekday))
            conn.commit()
        finally:
            conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/admin/site/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        conn = db()
        conn.execute('''INSERT INTO sites (company_id, name, client_company_name, address, notes, active) VALUES (?, ?, ?, ?, ?, 1)''',
                     (user['company_id'], data.get('name'), data.get('client_company_name'), data.get('address'), data.get('notes')))
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/admin/shift/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        data, _ = parse_post(environ)
        scheduled_hours = calculate_shift_hours_from_strings(data.get('start_time'), data.get('end_time'))
        user_id = data.get('user_id') or None
        status = 'assigned' if user_id else 'open'
        conn = db()
        if user_id:
            conflict = approved_time_off_request_for_date(conn, user['company_id'], user_id, data.get('shift_date'))
            if conflict:
                guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (user_id, user['company_id'])).fetchone()
                conn.close()
                return redirect_with_feedback(
                    start_response,
                    '/dashboard',
                    error=approved_time_off_conflict_error(data.get('shift_date'), conflict, guard['full_name'] if guard else None),
                )
            overlap_conflict = overlapping_shift_for_guard(
                conn,
                user['company_id'],
                user_id,
                data.get('shift_date'),
                data.get('start_time'),
                data.get('end_time'),
            )
            if overlap_conflict:
                guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (user_id, user['company_id'])).fetchone()
                conn.close()
                return redirect_with_feedback(
                    start_response,
                    '/dashboard',
                    error=overlapping_shift_conflict_error(overlap_conflict, guard['full_name'] if guard else None),
                )
        shift_sql, shift_params = shift_insert_sql_and_params(
            conn,
            ['company_id', 'site_id', 'shift_date', 'start_time', 'end_time', 'status', 'scheduled_hours', 'worked_hours', 'overtime_alert', 'notes'],
            [user['company_id'], data.get('site_id'), data.get('shift_date'), data.get('start_time'), data.get('end_time'), status, scheduled_hours, 0, 0, data.get('notes', '')],
            user_id,
        )
        conn.execute(shift_sql, shift_params)
        conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/admin/company/logo' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        _, files = parse_post(environ)
        _, logo_path = save_upload(files.get('logo'), 'logos') if files.get('logo') else (None, None)
        if logo_path:
            conn = db(); conn.execute('UPDATE companies SET logo_path=? WHERE id=?', (logo_path, user['company_id'])); conn.commit(); conn.close()
        return redirect(start_response, '/dashboard')

    if path == '/admin/patrol/export/history.csv':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        try:
            csv_data = patrol_history_csv(conn, user)
        finally:
            conn.close()
        start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_patrol_history.csv"')], 'text/csv; charset=utf-8'))
        return [csv_data]
    if path == '/admin/patrol/export/missed-checkpoints.csv':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        try:
            csv_data = missed_checkpoints_csv(conn, user)
        finally:
            conn.close()
        start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_missed_checkpoints.csv"')], 'text/csv; charset=utf-8'))
        return [csv_data]
    if path == '/admin/reports/export':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        pdf = export_reports_pdf(user['company_id'])
        start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_reports.pdf"')], 'application/pdf'))
        return [pdf]

    if path == '/admin/payroll':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        start_date = query.get('start', (date.today() - timedelta(days=date.today().weekday())).isoformat())
        end_date = query.get('end', (date.today() - timedelta(days=date.today().weekday()) + timedelta(days=13)).isoformat())
        rows = payroll_rows(user['company_id'], start_date, end_date, query.get('guard_id'))
        html = render('dashboard.html', title=PRODUCT_FULL_NAME, user=user, **get_dashboard_context(user, query.get('view', 'week')), payroll_rows=rows, payroll_start=start_date, payroll_end=end_date)
        return html_response(start_response, html)

    if path == '/admin/payroll/export.csv':
        user, response = require_admin(environ, start_response)
        if response:
            return response
        start_date = query.get('start', (date.today() - timedelta(days=date.today().weekday())).isoformat())
        end_date = query.get('end', (date.today() - timedelta(days=date.today().weekday()) + timedelta(days=13)).isoformat())
        csv_data = payroll_csv(user['company_id'], start_date, end_date, record_map)
        start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_payroll.csv"')], 'text/csv; charset=utf-8'))
        return [csv_data]

    return not_found(start_response)


LAYOUT_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{{ title }}</title>
  <link rel="stylesheet" href="/static/styles.css" />
</head>
<body>
  {% block body %}{% endblock %}
</body>
</html>'''

LOGIN_HTML = r'''{% extends "layout.html" %}
{% block body %}
<div class="login-shell">
  <div class="login-card">
    <div class="brand-panel">
      {% if default_company_logo_url %}<img src="{{ default_company_logo_url }}" alt="{{ default_company_name }} logo" class="brand-shield brand-shield-large company-logo-auth">{% else %}<img src="{{ provider_logo_url }}" alt="{{ provider_brand_name }} shield logo" class="brand-shield brand-shield-large">{% endif %}
      <div>
        <div class="eyebrow">{{ product_short_name }}</div>
        <h1>{{ product_full_name }}</h1>
        <p class="tagline">Security Operations Simplified</p>
        <p class="brand-subtitle">{{ brand_subtitle }}</p>
      </div>
      <div class="hero-copy">
        One platform for schedules, reports, patrol checkpoints, time tracking, and payroll-ready exports.
      </div>
      <div class="feature-pills">
        <span>Multi-Company</span><span>Mobile Ready</span><span>Dark Theme</span>
      </div>
    </div>
    <div class="form-panel">
      <div class="logo-placeholder">
        {% if default_company_logo_url %}<img src="{{ default_company_logo_url }}" alt="{{ default_company_name }} logo" class="brand-shield brand-shield-form company-logo-auth">{% else %}<img src="{{ provider_logo_url }}" alt="{{ provider_brand_name }} shield logo" class="brand-shield brand-shield-form">{% endif %}
      </div>
      <h2>Sign in</h2>
      <p class="small-muted">Access the {{ product_full_name }}</p>
      <p class="brand-subtitle compact">{{ brand_subtitle }}</p>
      {% if error %}<div class="alert error">{{ error }}</div>{% endif %}
      <form method="post" action="/login" class="stack">{{ csrf_input|safe }}
        <label>Username<input type="text" name="username" required></label>
        <label>Password<input type="password" name="password" required></label>
        <button type="submit" class="btn primary">Sign In</button>
      </form>
      <div class="demo-box">
        <strong>Demo Accounts</strong><br>
        superadmin / admin123<br>
        admin / admin123<br>
        guard1 / guard123
      </div>
    </div>
  </div>
</div>
{% endblock %}'''

APP_SHELL_HTML = r'''{% extends "layout.html" %}
{% block body %}
<div class="app-shell">
  <aside class="sidebar">
    <div class="sidebar-brand">
      {% if user.company_logo_url %}<img src="{{ user.company_logo_url }}" alt="{{ user.company_name or provider_brand_name }} logo" class="company-logo">{% else %}<img src="{{ provider_logo_url }}" alt="{{ provider_brand_name }} shield logo" class="brand-shield brand-shield-sidebar">{% endif %}
      <div class="sidebar-brand-copy">
        <div class="eyebrow">{{ product_short_name }} Platform</div>
        <h2>{{ user.company_name or provider_brand_name }}</h2>
        <div class="small-muted">{{ product_full_name }}</div>
      </div>
    </div>
    <div class="nav-links">
      {% for item in nav_items %}
      <a href="{{ item.href }}" class="{% if item.active %}active{% endif %}">{{ item.label }}</a>
      {% endfor %}
    </div>
  </aside>

  <main class="content">
    <section class="topbar card">
      {% if user.company_logo_url %}<img src="{{ user.company_logo_url }}" alt="{{ user.company_name or provider_brand_name }} logo" class="company-logo topbar-logo">{% else %}<img src="{{ provider_logo_url }}" alt="{{ provider_brand_name }} shield logo" class="brand-shield brand-shield-topbar">{% endif %}
      <div>
        <div class="eyebrow">{{ product_short_name }} Platform</div>
        <h1>{{ page_title or product_full_name }}</h1>
        <p class="small-muted">{{ brand_subtitle }}</p>
      </div>
      <div class="user-chip">{{ user.full_name }} · {{ user.role.replace('_', ' ').title() }}</div>
    </section>
    {% if flash_message %}<div class="alert success">{{ flash_message }}</div>{% endif %}
    {% if flash_error %}<div class="alert error">{{ flash_error }}</div>{% endif %}
    {% if user.role == 'guard' %}<div id="offline-sync-status" class="offline-sync-status" aria-live="polite">Sync status loading…</div>{% endif %}

    {% block page_content %}{% endblock %}
  {% if user.role == 'guard' %}
  <script>
  (function () {
    var key = 'steeleopsOfflineQueue:v1';
    var statusEl = document.getElementById('offline-sync-status');
    function loadQueue() { try { return JSON.parse(localStorage.getItem(key) || '[]'); } catch (err) { return []; } }
    function saveQueue(queue) { localStorage.setItem(key, JSON.stringify(queue)); updateStatus(); }
    function makeUuid() { return (crypto && crypto.randomUUID) ? crypto.randomUUID() : 'local-' + Date.now() + '-' + Math.random().toString(16).slice(2); }
    function nowIso() { return new Date().toISOString(); }
    function updateStatus(message) {
      if (!statusEl) return;
      var pending = loadQueue().filter(function (item) { return item.status !== 'synced'; }).length;
      var online = navigator.onLine;
      statusEl.className = 'offline-sync-status ' + (pending ? 'pending' : 'synced') + (online ? ' online' : ' offline');
      statusEl.textContent = message || (pending ? ('Pending Sync: ' + pending + ' item' + (pending === 1 ? '' : 's') + (online ? ' · syncing automatically' : ' · offline')) : (online ? 'All records synced' : 'Offline · new records will be saved locally'));
    }
    function fileToDataUrl(file) { return new Promise(function (resolve, reject) { var reader = new FileReader(); reader.onload = function () { resolve({ name: file.name, type: file.type, size: file.size, data_url: reader.result }); }; reader.onerror = reject; reader.readAsDataURL(file); }); }
    function formDataObject(form) {
      var data = {};
      Array.prototype.forEach.call(new FormData(form).entries(), function (entry) {
        if (entry[1] instanceof File) return;
        data[entry[0]] = entry[1];
      });
      return data;
    }
    function ensureHidden(form, name, value) {
      var input = form.querySelector('input[name="' + name + '"]');
      if (!input) { input = document.createElement('input'); input.type = 'hidden'; input.name = name; form.appendChild(input); }
      input.value = value;
      return value;
    }
    async function queueForm(form) {
      var uuidInput = form.querySelector('input[name="local_uuid"]');
      var tsInput = form.querySelector('input[name="device_timestamp"]');
      var localUuid = ensureHidden(form, 'local_uuid', (uuidInput && uuidInput.value) || makeUuid());
      var deviceTimestamp = ensureHidden(form, 'device_timestamp', (tsInput && tsInput.value) || nowIso());
      var data = formDataObject(form);
      data.local_uuid = localUuid;
      data.device_timestamp = deviceTimestamp;
      var fileField = form.dataset.offlineFileField;
      var attachments = [];
      if (fileField) {
        var inputs = form.querySelectorAll('input[type="file"][name="' + fileField + '"]');
        for (var i = 0; i < inputs.length; i++) {
          for (var j = 0; j < inputs[i].files.length; j++) attachments.push(await fileToDataUrl(inputs[i].files[j]));
        }
      }
      var queue = loadQueue();
      queue.push({ kind: form.dataset.offlineKind, local_uuid: localUuid, device_timestamp: deviceTimestamp, data: data, attachments: attachments, status: 'pending' });
      saveQueue(queue);
      form.reset();
      updateStatus('Pending Sync: saved locally and will auto-sync when internet returns.');
    }
    async function syncQueue() {
      var queue = loadQueue().filter(function (item) { return item.status !== 'synced'; });
      if (!navigator.onLine || !queue.length) { updateStatus(); return; }
      updateStatus('Pending Sync: syncing ' + queue.length + ' item' + (queue.length === 1 ? '' : 's') + '…');
      try {
        var response = await fetch('/api/offline-sync', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ records: queue }) });
        if (!response.ok) throw new Error('Sync failed with HTTP ' + response.status);
        var payload = await response.json();
        var results = payload.results || [];
        var remaining = queue.filter(function (record) {
          var result = results.find(function (item) { return item.local_uuid === record.local_uuid; });
          return !result || result.status === 'error';
        });
        saveQueue(remaining);
        updateStatus(remaining.length ? 'Pending Sync: some items need another retry.' : 'All records synced');
      } catch (err) {
        updateStatus('Pending Sync: retrying when internet returns.');
      }
    }
    document.addEventListener('submit', function (event) {
      var form = event.target.closest && event.target.closest('form.offline-queue-form');
      if (!form) return;
      var uuidInput = form.querySelector('input[name="local_uuid"]');
      var tsInput = form.querySelector('input[name="device_timestamp"]');
      ensureHidden(form, 'local_uuid', (uuidInput && uuidInput.value) || makeUuid());
      ensureHidden(form, 'device_timestamp', (tsInput && tsInput.value) || nowIso());
      if (!navigator.onLine) {
        event.preventDefault();
        queueForm(form);
      }
    }, true);
    window.addEventListener('online', syncQueue);
    window.addEventListener('offline', updateStatus);
    updateStatus();
    if (navigator.onLine) syncQueue();
  }());
  </script>
  {% endif %}
  </main>
</div>
{% endblock %}'''

ADMIN_COMPANY_LOGO_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="card">
  <div class="section-head"><h3>Company Logo</h3><span>Branding controls</span></div>
  <p class="small-muted">Upload the company logo for {{ user.company_name or provider_brand_name }}. {{ product_full_name }} remains the software name, and {{ provider_brand_name }} remains the operating company brand.</p>
  <div class="logo-management-grid">
    <div class="logo-preview-card">
      <div class="small-muted">Current company logo</div>
      {% if user.company_logo_url %}
        <img src="{{ user.company_logo_url }}" alt="{{ user.company_name or provider_brand_name }} logo" class="company-logo-preview">
      {% else %}
        <img src="{{ provider_logo_url }}" alt="{{ provider_brand_name }} shield logo" class="brand-shield brand-shield-large">
        <p class="small-muted">No custom logo uploaded. The default {{ provider_brand_name }} shield is shown.</p>
      {% endif %}
    </div>
    <form method="post" action="/admin/company/logo" enctype="multipart/form-data" class="stack compact">
      {{ csrf_input|safe }}
      <label>Upload Company Logo<input type="file" name="logo" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" required></label>
      <div class="small-muted">PNG uploads are supported. JPG, GIF, WebP, and SVG images are also accepted.</div>
      <button class="btn primary" type="submit">Save Logo</button>
      <a class="btn ghost" href="/dashboard">Back to Dashboard</a>
    </form>
  </div>
</section>
{% endblock %}'''

DASHBOARD_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    {% if patrol_issue_alert %}
    <section class="patrol-alert patrol-alert-{{ patrol_issue_alert.severity }}" role="alert" aria-live="polite">
      <div>
        <div class="eyebrow">SteeleOps Patrol Monitoring</div>
        <strong>{{ patrol_issue_alert.message }}</strong>
        <div class="small-muted">{{ patrol_issue_alert.incomplete_count }} incomplete · {{ patrol_issue_alert.missed_count }} missed · {{ '%.1f'|format(patrol_issue_alert.completion_percentage|float) }}% completion</div>
      </div>
      <a class="btn primary" href="{{ patrol_issue_alert.href }}">View Patrol Issues</a>
    </section>
    {% endif %}

    {% if user.role == 'guard' %}
    <section class="card guard-action-dashboard">
      <div class="section-head">
        <div>
          <div class="eyebrow">Guard Dashboard</div>
          <h2>{{ guard_dashboard_summary.assigned_site }}</h2>
          <span>Fast actions for your assigned post.</span>
        </div>
      </div>
      <div class="guard-action-grid">
        {% set current_shift = guard_dashboard_summary.current_shift %}
        {% if current_shift and current_shift.user_id and not current_shift.clock_in_time %}
        <form method="post" action="/clock-in" class="guard-action-form"><input type="hidden" name="shift_id" value="{{ current_shift.id }}"><button class="guard-action-btn primary" type="submit"><span>Clock In</span><small>{{ current_shift.start_time }} - {{ current_shift.end_time }}</small></button></form>
        {% elif current_shift and current_shift.user_id and current_shift.clock_in_time and not current_shift.clock_out_time %}
        <form method="post" action="/clock-out" class="guard-action-form"><input type="hidden" name="shift_id" value="{{ current_shift.id }}"><button class="guard-action-btn primary" type="submit"><span>Clock Out</span><small>Clocked in {{ current_shift.clock_in_time }}</small></button></form>
        {% else %}
        <button class="guard-action-btn" type="button" disabled><span>Clock In / Clock Out</span><small>No active assigned shift</small></button>
        {% endif %}
        <a class="guard-action-btn" href="/patrols{% if guard_dashboard_summary.assigned_site_id %}?site_id={{ guard_dashboard_summary.assigned_site_id }}{% endif %}"><span>Start Patrol</span><small>Open assigned patrol tools</small></a>
        <a class="guard-action-btn" href="/guard/daily-activity-reports{% if guard_dashboard_summary.assigned_site_id %}?site_id={{ guard_dashboard_summary.assigned_site_id }}{% endif %}"><span>Submit DAR</span><small>Daily activity report</small></a>
        <a class="guard-action-btn" href="/guard/incident-reports{% if guard_dashboard_summary.assigned_site_id %}?site_id={{ guard_dashboard_summary.assigned_site_id }}{% endif %}"><span>Incident Report</span><small>Report an incident</small></a>
      </div>
    </section>
    {% endif %}

    {% if user.role != 'client' %}
    <section class="grid stats-grid">
      {% if user.role == 'guard' %}
      <div class="stat card guard-stat-highlight">
        <div class="stat-label">My Current Shift</div>
        <div class="stat-text">{% if guard_dashboard_summary.current_shift %}{{ guard_dashboard_summary.current_shift.shift_date }} · {{ guard_dashboard_summary.current_shift.start_time }} - {{ guard_dashboard_summary.current_shift.end_time }}{% else %}No shift today{% endif %}</div>
      </div>
      <div class="stat card">
        <div class="stat-label">My Assigned Site</div>
        <div class="stat-text">{{ guard_dashboard_summary.assigned_site }}</div>
      </div>
      <div class="stat card">
        <div class="stat-label">Hours Worked This Week</div>
        <div class="stat-number">{{ '%.2f'|format(guard_dashboard_summary.hours_worked_week|float) }}</div>
      </div>
      <div class="stat card">
        <div class="stat-label">My Open Reports</div>
        <div class="stat-number">{{ guard_dashboard_summary.open_reports }}</div>
      </div>
      <div class="stat card"><div class="stat-label">Active Patrols{% if patrol_issue_alert and patrol_issue_alert.active_count %}<span class="issue-badge warning">{{ patrol_issue_alert.active_count }}</span>{% endif %}</div><div class="stat-number">{{ stats.active_patrols }}</div></div>
      <div class="stat card"><div class="stat-label">Completed Tours</div><div class="stat-number">{{ stats.completed_tours }}</div></div>
      <div class="stat card"><div class="stat-label">Excused Patrols</div><div class="stat-number">{{ stats.excused_patrols or 0 }}</div></div>
      <div class="stat card"><div class="stat-label">Missed Patrols{% if patrol_issue_alert and patrol_issue_alert.missed_count %}<span class="issue-badge danger">{{ patrol_issue_alert.missed_count }}</span>{% endif %}</div><div class="stat-number">{{ stats.missed_checkpoints }}</div></div>
      {% else %}
      <div class="stat card"><div class="stat-label">Guards On Duty</div><div class="stat-number">{{ stats.guards_on_duty }}</div></div>
      <div class="stat card"><div class="stat-label">Company-wide Open Incidents</div><div class="stat-number">{{ stats.open_incidents }}</div></div>
      <div class="stat card"><div class="stat-label">Sites Active Today</div><div class="stat-number">{{ stats.sites_active_today }}</div></div>
      <div class="stat card"><div class="stat-label">Active Patrols{% if patrol_issue_alert and patrol_issue_alert.active_count %}<span class="issue-badge warning">{{ patrol_issue_alert.active_count }}</span>{% endif %}</div><div class="stat-number">{{ stats.active_patrols }}</div></div>
      <div class="stat card"><div class="stat-label">Completed Tours</div><div class="stat-number">{{ stats.completed_tours }}</div></div>
      <div class="stat card"><div class="stat-label">Excused Patrols</div><div class="stat-number">{{ stats.excused_patrols or 0 }}</div></div>
      <div class="stat card"><div class="stat-label">Missed Patrols{% if patrol_issue_alert and patrol_issue_alert.missed_count %}<span class="issue-badge danger">{{ patrol_issue_alert.missed_count }}</span>{% endif %}</div><div class="stat-number">{{ stats.missed_checkpoints }}</div></div>
      {% endif %}
    </section>

    {% if user.role in ['company_admin', 'superadmin', 'supervisor', 'admin'] %}
    <section class="card">
      <div class="section-head">
        <div><h3>Patrol Analytics Dashboard</h3><span>Completion rates, excused patrols, missed patrols, guard and site performance</span></div>
        <div class="actions">
          <a class="btn ghost" href="/admin/patrol/export/history.csv">Export Patrol History CSV</a>
          <a class="btn ghost" href="/admin/patrol/export/missed-checkpoints.csv">Export Missed Checkpoints CSV</a>
        </div>
      </div>
      <div class="grid stats-grid">
        <div class="stat card"><div class="stat-label">Total Tours Assigned</div><div class="stat-number">{{ patrol_completion_summary.total_assigned }}</div></div>
        <div class="stat card"><div class="stat-label">Total Tours Completed</div><div class="stat-number">{{ patrol_completion_summary.total_completed }}</div></div>
        <div class="stat card"><div class="stat-label">Excused Patrols</div><div class="stat-number">{{ patrol_completion_summary.total_excused }}</div></div>
        <div class="stat card"><div class="stat-label">Missed Patrols</div><div class="stat-number">{{ patrol_completion_summary.total_missed }}</div></div>
        <div class="stat card"><div class="stat-label">Patrol Completion Rate</div><div class="stat-number">{{ '%.1f'|format(patrol_completion_summary.completion_percentage|float) }}%</div></div>
        <div class="stat card"><div class="stat-label">Patrols Today</div><div class="stat-number">{{ patrol_dashboard_widgets.patrols_today }}</div></div>
        <div class="stat card"><div class="stat-label">Completed Tours Today</div><div class="stat-number">{{ patrol_dashboard_widgets.completed_today }}</div></div>
        <div class="stat card"><div class="stat-label">Excused Patrols Today</div><div class="stat-number">{{ patrol_dashboard_widgets.excused_today }}</div></div>
        <div class="stat card"><div class="stat-label">Missed Patrols Today{% if patrol_issue_alert and patrol_issue_alert.missed_count %}<span class="issue-badge danger">{{ patrol_issue_alert.missed_count }}</span>{% endif %}</div><div class="stat-number">{{ patrol_dashboard_widgets.missed_today }}</div></div>
      </div>
      <div class="grid two-col">
        <div class="card compact-card">
          <div class="section-head"><h4>Top Performing Guards</h4><span>Ranked by completion rate</span></div>
          {% for guard in top_performing_guards %}
          <div class="list-item"><strong>{{ guard.guard_name }}</strong><span>{{ '%.1f'|format(guard.completion_percentage|float) }}% · {{ guard.month_completed }} this month · {{ guard.missed_count }} missed</span></div>
          {% else %}<div class="empty">No patrol performance yet.</div>{% endfor %}
        </div>
        <div class="card compact-card">
          <div class="section-head"><h4>Missed Checkpoints by Guard</h4><span>Recent missed checkpoint totals</span></div>
          {% for item in missed_checkpoints_by_guard %}
          <div class="list-item"><strong>{{ item.name }}</strong><span>{{ item.missed_count }} missed</span></div>
          {% else %}<div class="empty">No missed checkpoints recorded.</div>{% endfor %}
        </div>
      </div>
      <div class="table-wrap">
        <h4>Guard Performance</h4>
        <table><thead><tr><th>Guard</th><th>This Week</th><th>This Month</th><th>Avg Completion</th><th>Missed</th></tr></thead><tbody>
          {% for guard in guard_performance %}<tr><td>{{ guard.guard_name }}</td><td>{{ guard.week_completed }}</td><td>{{ guard.month_completed }}</td><td>{{ '%.1f'|format(guard.average_completion_minutes|float) }} min</td><td>{{ guard.missed_count }}</td></tr>
          {% else %}<tr><td colspan="5">No guard patrol history.</td></tr>{% endfor %}
        </tbody></table>
      </div>
      <div class="table-wrap">
        <h4>Site Performance</h4>
        <table><thead><tr><th>Site</th><th>Completion %</th><th>Last Completed Patrol</th><th>Active Routes</th><th>Assigned / Completed</th></tr></thead><tbody>
          {% for site in site_performance %}<tr><td>{{ site.site_name }}</td><td>{{ '%.1f'|format(site.completion_percentage|float) }}%</td><td>{{ site.last_completed_patrol or '—' }}</td><td>{{ site.active_routes }}</td><td>{{ site.total_assigned }} / {{ site.total_completed }}</td></tr>
          {% else %}<tr><td colspan="5">No site patrol history.</td></tr>{% endfor %}
        </tbody></table>
      </div>
      <div class="grid two-col">
        <div class="card compact-card">
          <div class="section-head"><h4>Missed Checkpoints by Site</h4><span>Recent missed checkpoint totals</span></div>
          {% for item in missed_checkpoints_by_site %}<div class="list-item"><strong>{{ item.name }}</strong><span>{{ item.missed_count }} missed</span></div>{% else %}<div class="empty">No missed checkpoints by site.</div>{% endfor %}
        </div>
        <div class="card compact-card">
          <div class="section-head"><h4>Missed Checkpoint History</h4><span>Date/time audit trail</span></div>
          {% for miss in missed_checkpoint_history %}
          <div class="list-item detailed"><div><strong>{{ miss.checkpoint_name }}</strong><div class="small-muted">{{ miss.scanned_at }} · {{ miss.guard_name }} · {{ miss.site_name }} · {{ miss.tour_name }}</div></div></div>
          {% else %}<div class="empty">No missed checkpoint history.</div>{% endfor %}
        </div>
      </div>
    </section>

    <section class="card admin-action-card">
      <div class="section-head">
        <div>
          <h3>Admin · Alert Tools</h3>
          <div class="small-muted">Trigger the missed clock check and review alert counts without leaving the dashboard.</div>
        </div>
      </div>
      <form id="missed-clock-check-form" class="stack compact">{{ csrf_input|safe }}
        <div class="actions">
          <button class="btn primary" type="submit" id="run-missed-clock-check-btn">Run Missed Clock Check</button>
          <span class="small-muted">Use this to verify alert generation and SMS send attempts.</span>
        </div>
      </form>
      <div id="missed-clock-check-feedback" class="stack compact" hidden></div>
    </section>
    <script>
    (function () {
      var form = document.getElementById('missed-clock-check-form');
      if (!form) return;
      var button = document.getElementById('run-missed-clock-check-btn');
      var feedback = document.getElementById('missed-clock-check-feedback');
      function renderMessage(type, html) {
        feedback.hidden = false;
        feedback.innerHTML = '<div class="alert ' + type + '">' + html + '</div>';
      }
      form.addEventListener('submit', function (event) {
        event.preventDefault();
        var csrf = form.querySelector('input[name="csrf_token"]');
        var originalLabel = button.textContent;
        button.disabled = true;
        button.textContent = 'Running...';
        fetch('/admin/run-missed-clock-check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
          body: new URLSearchParams({ csrf_token: csrf ? csrf.value : '' }).toString()
        })
          .then(function (response) {
            return response.text().then(function (body) {
              var data = {};
              if (body) {
                try { data = JSON.parse(body); } catch (error) {
                  if (!response.ok) throw new Error('Request failed.');
                  throw new Error('Unexpected response from server.');
                }
              }
              if (!response.ok) {
                throw new Error((data && (data.error || data.message)) || 'Request failed.');
              }
              return data;
            });
          })
          .then(function (data) {
            var alerts = Array.isArray(data.alerts) ? data.alerts : [];
            var items = alerts.length
              ? '<ul class="result-list">' + alerts.map(function (alert) { return '<li>' + alert + '</li>'; }).join('') + '</ul>'
              : '<div class="small-muted">No new missed clock alerts were created.</div>';
            renderMessage('success',
              '<strong>Missed clock check completed.</strong>' +
              '<div class="result-grid">' +
                '<div><span class="small-muted">Alerts created</span><strong>' + (data.created_count || 0) + '</strong></div>' +
                '<div><span class="small-muted">SMS sent</span><strong>' + (data.sent_count || 0) + '</strong></div>' +
                '<div><span class="small-muted">Skipped</span><strong>' + (data.skipped_count || 0) + '</strong></div>' +
              '</div>' + items
            );
          })
          .catch(function (error) {
            renderMessage('error', '<strong>Unable to run missed clock check.</strong><div>' + error.message + '</div>');
          })
          .finally(function () {
            button.disabled = false;
            button.textContent = originalLabel;
          });
      });
    }());
    </script>
    {% endif %}

    <section class="grid two-col">
      <div class="card">
        <div class="section-head"><h3>{{ schedule_view.title() }} Schedule View</h3><span>{{ range_start }} to {{ range_end }}</span></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Time</th><th>Guard</th><th>Site</th><th>Status</th><th>Hours</th></tr></thead>
            <tbody>
            {% for shift in schedule_rows %}
              <tr>
                <td>{{ shift.shift_date }}</td>
                <td>{{ shift.start_time }} - {{ shift.end_time }}</td>
                <td>
                  {{ shift.full_name or 'Open Shift' }}
                  {% if shift.has_approved_time_off %}<div class="small-muted">{{ shift.approved_time_off_detail }}</div>{% elif shift.has_overlap_conflict %}<div class="small-muted">{{ shift.overlap_conflict_detail }}</div>{% endif %}
                </td>
                <td>{{ shift.site_name }}</td>
                <td>
                  <span class="badge {{ shift.status }}">{{ shift.status }}</span>
                  {% if shift.conflict_status == 'on_leave' %}
                  <span class="badge conflict-leave" title="{{ shift.approved_time_off_detail }}">On Leave</span>
                  {% elif shift.conflict_status == 'overlap_conflict' %}
                  <span class="badge conflict-overlap" title="{{ shift.overlap_conflict_detail }}">Overlap Conflict</span>
                  {% endif %}
                </td>
                <td>{{ shift.worked_hours or shift.scheduled_hours }}</td>
              </tr>
            {% else %}<tr><td colspan="6">No schedule rows in this view.</td></tr>{% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="section-head"><h3>Open Shift Alerts</h3><span>Unassigned coverage needs</span></div>
        {% for shift in open_shift_alerts %}
          <div class="list-item"><strong>{{ shift.site_name }}</strong><span>{{ shift.shift_date }} · {{ shift.start_time }}-{{ shift.end_time }}</span></div>
        {% else %}<div class="empty">No open shift alerts.</div>{% endfor %}
        {% if user.role == 'guard' %}
        <hr>
        <form method="post" action="/shift/claim" class="stack compact">
          <h4>Claim Open Shift</h4>
          <label>Open Shift<select name="shift_id">{% for shift in my_open_shift_options %}<option value="{{ shift.id }}">{{ shift.shift_date }} · {{ shift.site_name }} · {{ shift.start_time }}-{{ shift.end_time }}</option>{% endfor %}</select></label>
          <button class="btn" type="submit" {% if not my_open_shift_options %}disabled{% endif %}>Claim Shift</button>
        </form>
        {% endif %}
        {% if user.role in ['company_admin', 'superadmin', 'supervisor'] %}
        <hr>
        <form method="post" action="/admin/shift/new" class="stack compact">
          <h4>Create Shift</h4>
          <label>Site<select name="site_id">{% for site in sites %}<option value="{{ site.id }}" {% if shift_form.site_id and shift_form.site_id|int == site.id %}selected{% endif %}>{{ site.name }}</option>{% endfor %}</select></label>
          <label>Assign Guard<select name="user_id"><option value="">Open Shift</option>{% for guard_option in guard_option_rows %}<option value="{{ guard_option.id }}" {% if shift_form.user_id and shift_form.user_id|int == guard_option.id %}selected{% endif %} {% if not guard_option.available %}disabled{% endif %}>{{ guard_option.full_name }}{{ guard_option.label_suffix }}</option>{% endfor %}</select></label>
          <div class="small-muted">Unavailable guards are disabled when they are on approved leave or already assigned to an overlapping shift.</div>
          <div class="row-2"><label>Date<input type="date" name="shift_date" value="{{ shift_form.shift_date }}" required></label><label>Start<input type="time" name="start_time" value="{{ shift_form.start_time }}" required></label></div>
          <div class="row-2"><label>End<input type="time" name="end_time" value="{{ shift_form.end_time }}" required></label><label>Notes<input type="text" name="notes" value="{{ shift_form.notes }}"></label></div>
          <button class="btn primary" type="submit">Create Shift</button>
        </form>
        {% endif %}
      </div>
    </section>

    <section class="grid two-col">
      {% if user.role == 'guard' %}
      <div class="card compact-card">
        <div class="section-head"><h3>My Shift Today</h3><span>Assigned shifts for your account</span></div>
        {% for shift in my_shifts %}
          <div class="list-item detailed">
            <div>
              <strong>{{ shift.site_name }}</strong>
              <div class="small-muted">Date: {{ shift.shift_date }}</div>
              <div class="small-muted">Time: {{ shift.start_time }} - {{ shift.end_time }}</div>
              <div class="small-muted">Status: <span class="badge {{ shift.status }}">{{ shift.status }}</span></div>
              <div class="small-muted">Clock In: {{ shift.clock_in_time or '—' }} | Clock Out: {{ shift.clock_out_time or '—' }}</div>
            </div>
            <div class="actions">
              {% if not shift.clock_in_time and shift.user_id %}
              <form method="post" action="/clock-in"><input type="hidden" name="shift_id" value="{{ shift.id }}"><button class="btn">Clock In</button></form>
              {% elif shift.clock_in_time and not shift.clock_out_time and shift.user_id %}
              <form method="post" action="/clock-out"><input type="hidden" name="shift_id" value="{{ shift.id }}"><button class="btn primary">Clock Out</button></form>
              {% endif %}
            </div>
          </div>
        {% else %}<div class="empty">No assigned shifts.</div>{% endfor %}
      </div>
      <div class="card">
        <div class="section-head"><h3>Available Shifts</h3><span>Open same-company shifts you can claim</span></div>
        {% for shift in available_shifts %}
          <div class="list-item detailed">
            <div>
              <strong>{{ shift.site_name }}</strong>
              <div class="small-muted">Date: {{ shift.shift_date }}</div>
              <div class="small-muted">Time: {{ shift.start_time }} - {{ shift.end_time }}</div>
              <div class="small-muted">Status: <span class="badge {{ shift.status }}">{{ shift.status }}</span></div>
            </div>
            <div class="actions">
              <form method="post" action="/shift/claim"><input type="hidden" name="shift_id" value="{{ shift.id }}"><button class="btn primary" type="submit">Claim Shift</button></form>
            </div>
          </div>
        {% else %}<div class="empty">No available shifts to claim.</div>{% endfor %}
        <hr>
        <form method="post" action="/time-correction/request" class="stack compact">
          <h4>Time Correction Request</h4>
          <label>Shift<select name="shift_id">{% for shift in my_shifts[:12] if shift.user_id %}<option value="{{ shift.id }}">{{ shift.shift_date }} · {{ shift.site_name }} · {{ shift.start_time }}</option>{% endfor %}</select></label>
          <div class="row-2"><label>Requested Clock In<input type="datetime-local" name="requested_clock_in" required></label><label>Requested Clock Out<input type="datetime-local" name="requested_clock_out" required></label></div>
          <label>Reason<textarea name="reason" rows="3"></textarea></label>
          <button class="btn" type="submit" {% if not my_shifts %}disabled{% endif %}>Submit Request</button>
        </form>
        <hr>
        <form method="post" action="/time-off/request" class="stack compact" onsubmit="this.querySelector('button[type=submit]').disabled=true; this.querySelector('button[type=submit]').textContent='Submitting...';">
          <h4>Time Off Request</h4>
          <div class="row-2"><label>Start Date<input type="date" name="start_date" required></label><label>End Date<input type="date" name="end_date" required></label></div>
          <label>Type<select name="type"><option value="paid">Paid</option><option value="unpaid">Unpaid</option></select></label>
          <label>Reason<textarea name="reason" rows="2"></textarea></label>
          <button class="btn primary" type="submit">Submit Time Off Request</button>
        </form>
        <hr>
        <h4>Time Off Request History</h4>
        {% for item in my_time_off_requests %}
          <div class="list-item detailed">
            <div>
              <strong>{{ item.start_date }} → {{ item.end_date }}</strong>
              <div class="small-muted">Type: {{ item.type }}</div>
              {% if item.reason %}<div class="small-muted">Reason: {{ item.reason }}</div>{% endif %}
              {% if item.reviewed_at %}<div class="small-muted">Reviewed: {{ item.reviewed_at }}{% if item.reviewed_by_name %} by {{ item.reviewed_by_name }}{% if item.reviewed_by_role %} ({{ item.reviewed_by_role }}){% endif %}{% endif %}</div>{% endif %}
            </div>
            <div class="actions"><span class="badge {{ item.status }}">{{ item.status }}</span></div>
          </div>
        {% else %}<div class="empty">No time off requests yet.</div>{% endfor %}
      </div>
      {% else %}
      <div class="card">
        <div class="section-head"><h3>Shift Actions & Time Tracking</h3><span>Clock events are tied to shifts</span></div>
        {% for shift in shifts[:8] %}
          <div class="list-item detailed">
            <div>
              <strong>{{ shift.site_name }}</strong>
              <div class="small-muted">{{ shift.shift_date }} · {{ shift.start_time }}-{{ shift.end_time }}</div>
              <div class="small-muted">Clock In: {{ shift.clock_in_time or '—' }} | Clock Out: {{ shift.clock_out_time or '—' }}</div>
            </div>
            <div class="actions">
              <span class="small-muted">Assigned guard self-service only</span>
            </div>
          </div>
        {% else %}<div class="empty">No shifts available.</div>{% endfor %}
      </div>
      <div class="card">
        <div class="section-head"><h3>Overtime Alerts</h3><span>Current weekly watchlist</span></div>
        {% for row in overtime_alerts %}
          <div class="list-item"><strong>{{ row.full_name }}</strong><span>{{ row.total_hours }} hrs</span></div>
        {% else %}<div class="empty">No overtime alerts this week.</div>{% endfor %}
        <hr>
        <form method="post" action="/time-correction/request" class="stack compact">
          <h4>Time Correction Request</h4>
          <label>Shift<select name="shift_id">{% for shift in shifts[:12] if shift.user_id %}<option value="{{ shift.id }}">{{ shift.shift_date }} · {{ shift.site_name }} · {{ shift.start_time }}</option>{% endfor %}</select></label>
          <div class="row-2"><label>Requested Clock In<input type="datetime-local" name="requested_clock_in" required></label><label>Requested Clock Out<input type="datetime-local" name="requested_clock_out" required></label></div>
          <label>Reason<textarea name="reason" rows="3"></textarea></label>
          <button class="btn" type="submit">Submit Request</button>
        </form>
      </div>
      {% endif %}
    </section>


    {% endif %}
    {% if user.role in ['company_admin', 'superadmin', 'supervisor', 'admin'] %}
    <section class="grid two-col">
      <div class="card">
        <div class="section-head"><h3>Admin · Guards & Sites</h3><span>Create accounts and posts</span></div>
        <form method="post" action="/admin/guard/new" class="stack compact">
          <h4>Create Staff Account</h4>
          <div class="row-2"><label>Full Name<input type="text" name="full_name" required></label><label>Username<input type="text" name="username" required></label></div>
          <div class="row-3"><label>Password<input type="text" name="password" value="password123"></label><label>Role<select name="role"><option value="guard">guard</option><option value="supervisor">supervisor</option><option value="admin">admin</option></select></label><label>Hourly Rate<input type="number" step="0.01" name="hourly_rate" value="18.00"></label></div>
          <div class="row-3"><label>Email<input type="email" name="email"></label><label>Phone<input type="text" name="phone"></label><label>License #<input type="text" name="license_number"></label></div>
          <button class="btn primary" type="submit">Create User</button>
        </form>
        <hr>
        <h4>Staff / Users</h4>
        {% for staff_user in staff_users %}
        <form method="post" action="/admin/user/update" class="list-item detailed">
          <input type="hidden" name="user_id" value="{{ staff_user.id }}">
          <div>
            <strong>{{ staff_user.full_name }}</strong>
            <div class="small-muted">{{ staff_user.username }}{% if staff_user.email %} · {{ staff_user.email }}{% endif %}</div>
          </div>
          <div class="actions">
            <select name="role">
              <option value="guard" {% if staff_user.role == 'guard' %}selected{% endif %}>guard</option>
              <option value="supervisor" {% if staff_user.role == 'supervisor' %}selected{% endif %}>supervisor</option>
              <option value="admin" {% if staff_user.role in ['admin', 'company_admin', 'superadmin'] %}selected{% endif %}>admin</option>
            </select>
            <button class="btn ghost" type="submit">Save</button>
          </div>
        </form>
        {% else %}<div class="empty">No users found.</div>{% endfor %}
        <hr>
        <form method="post" action="/admin/client/new" class="stack compact">
          <h4>Create Client</h4>
          <div class="row-2"><label>Client Name<input type="text" name="name" required></label><label>Contact Name<input type="text" name="contact_name"></label></div>
          <div class="row-2"><label>Contact Email<input type="email" name="contact_email"></label><label>Contact Phone<input type="text" name="contact_phone"></label></div>
          <label>Notes<textarea name="notes" rows="2"></textarea></label>
          <button class="btn" type="submit">Add Client</button>
        </form>
        <hr>
        <form method="post" action="/admin/site/new" class="stack compact">
          <h4>Create Site</h4>
          <div class="row-2"><label>Site Name<input type="text" name="name" required></label><label>Client<select name="client_id"><option value="">None</option>{% for client in clients %}<option value="{{ client.id }}">{{ client.name }}</option>{% endfor %}</select></label></div>
          <label>Client Company Name<input type="text" name="client_company_name"></label>
          <label>Address<input type="text" name="address"></label>
          <label>Notes<textarea name="notes" rows="2"></textarea></label>
          <button class="btn" type="submit">Add Site</button>
        </form>
      </div>
      <div class="card">
        <div class="section-head"><h3>Admin · Branding</h3><span>Company controls</span></div>
        <form method="post" action="/admin/company/logo" enctype="multipart/form-data" class="stack compact">
          <label>Upload Company Logo<input type="file" name="logo" accept="image/*"></label>
          <button class="btn" type="submit">Save Logo</button>
        </form>
      </div>
    </section>
    {% endif %}

    {% if user.role in ['company_admin', 'superadmin', 'supervisor', 'admin'] %}
    <section class="card">
      <div class="section-head"><h3>Pending Time Corrections</h3><span>Approval queue</span></div>
      {% for item in time_corrections %}
      <div class="list-item detailed">
        <div>
          <strong>{{ item.requested_by_name }}</strong>
          <div class="small-muted">Shift {{ item.shift_date }} {{ item.start_time }}-{{ item.end_time }}</div>
          <div class="small-muted">Requested: {{ item.requested_clock_in }} → {{ item.requested_clock_out }}</div>
        </div>
        <div class="actions">
          <span class="badge {{ item.status }}">{{ item.status }}</span>
          {% if item.status == 'pending' %}
          <form method="post" action="/time-correction/approve"><input type="hidden" name="request_id" value="{{ item.id }}"><input type="hidden" name="decision" value="approved"><button class="btn">Approve</button></form>
          <form method="post" action="/time-correction/approve"><input type="hidden" name="request_id" value="{{ item.id }}"><input type="hidden" name="decision" value="declined"><button class="btn ghost">Decline</button></form>
          {% endif %}
        </div>
      </div>
      {% else %}<div class="empty">No pending time corrections.</div>{% endfor %}
    </section>
    <section class="card">
      <div class="section-head"><h3>Time Off Requests</h3><span>Guard leave review</span></div>
      {% for item in admin_time_off_requests %}
      <div class="list-item detailed">
        <div>
          <strong>{{ item.guard_name }}</strong>
          <div class="small-muted">{{ item.start_date }} → {{ item.end_date }} · {{ item.type }}</div>
          {% if item.reason %}<div class="small-muted">Reason: {{ item.reason }}</div>{% endif %}
          {% if item.reviewed_at %}<div class="small-muted">Reviewed: {{ item.reviewed_at }}{% if item.reviewed_by_name %} by {{ item.reviewed_by_name }}{% if item.reviewed_by_role %} ({{ item.reviewed_by_role }}){% endif %}{% endif %}</div>{% endif %}
        </div>
        <div class="actions">
          <span class="badge {{ item.status }}">{{ item.status }}</span>
          {% if item.status == 'pending' %}
          <form method="post" action="/time-off/approve" onsubmit="return confirm('Approve this time off request?');"><input type="hidden" name="request_id" value="{{ item.id }}"><input type="hidden" name="decision" value="approved"><input type="text" name="review_note" placeholder="Review note (optional)"><button class="btn">Approve</button></form>
          <form method="post" action="/time-off/approve" onsubmit="return confirm('Deny this time off request?');"><input type="hidden" name="request_id" value="{{ item.id }}"><input type="hidden" name="decision" value="denied"><input type="text" name="review_note" placeholder="Review note (optional)"><button class="btn ghost">Deny</button></form>
          {% endif %}
        </div>
      </div>
      {% else %}<div class="empty">No time off requests.</div>{% endfor %}
    </section>
    {% endif %}
{% endblock %}'''

SCHEDULE_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <section class="grid two-col">
      <div class="card">
        <div class="section-head"><h3>{{ schedule_view.title() }} Schedule View</h3><span>{{ range_start }} to {{ range_end }}</span></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Date</th><th>Time</th><th>Guard</th><th>Site</th><th>Status</th><th>Hours</th></tr></thead>
            <tbody>
            {% for shift in schedule_rows %}
              <tr>
                <td>{{ shift.shift_date }}</td>
                <td>{{ shift.start_time }} - {{ shift.end_time }}</td>
                <td>
                  {{ shift.full_name or 'Open Shift' }}
                  {% if shift.has_approved_time_off %}<div class="small-muted">{{ shift.approved_time_off_detail }}</div>{% elif shift.has_overlap_conflict %}<div class="small-muted">{{ shift.overlap_conflict_detail }}</div>{% endif %}
                </td>
                <td>{{ shift.site_name }}</td>
                <td>
                  <span class="badge {{ shift.status }}">{{ shift.status }}</span>
                  {% if shift.conflict_status == 'on_leave' %}
                  <span class="badge conflict-leave" title="{{ shift.approved_time_off_detail }}">On Leave</span>
                  {% elif shift.conflict_status == 'overlap_conflict' %}
                  <span class="badge conflict-overlap" title="{{ shift.overlap_conflict_detail }}">Overlap Conflict</span>
                  {% endif %}
                </td>
                <td>{{ shift.worked_hours or shift.scheduled_hours }}</td>
              </tr>
            {% else %}<tr><td colspan="6">No schedule rows in this view.</td></tr>{% endfor %}
            </tbody>
          </table>
        </div>
      </div>
      <div class="card">
        <div class="section-head"><h3>Open Shift Alerts</h3><span>Unassigned coverage needs</span></div>
        {% for shift in open_shift_alerts %}
          <div class="list-item"><strong>{{ shift.site_name }}</strong><span>{{ shift.shift_date }} · {{ shift.start_time }}-{{ shift.end_time }}</span></div>
        {% else %}<div class="empty">No open shift alerts.</div>{% endfor %}
        {% if user.role == 'guard' %}
        <hr>
        <form method="post" action="/shift/claim" class="stack compact">
          <h4>Claim Open Shift</h4>
          <label>Open Shift<select name="shift_id">{% for shift in my_open_shift_options %}<option value="{{ shift.id }}">{{ shift.shift_date }} · {{ shift.site_name }} · {{ shift.start_time }}-{{ shift.end_time }}</option>{% endfor %}</select></label>
          <button class="btn" type="submit" {% if not my_open_shift_options %}disabled{% endif %}>Claim Shift</button>
        </form>
        {% endif %}
        {% if user.role in ['company_admin', 'superadmin', 'supervisor', 'admin'] %}
        <hr>
        <form method="post" action="/admin/shift/new" class="stack compact">
          <h4>Create Shift</h4>
          <label>Site<select name="site_id">{% for site in sites %}<option value="{{ site.id }}" {% if shift_form.site_id and shift_form.site_id|int == site.id %}selected{% endif %}>{{ site.name }}</option>{% endfor %}</select></label>
          <label>Assign Guard<select name="user_id"><option value="">Open Shift</option>{% for guard_option in guard_option_rows %}<option value="{{ guard_option.id }}" {% if shift_form.user_id and shift_form.user_id|int == guard_option.id %}selected{% endif %} {% if not guard_option.available %}disabled{% endif %}>{{ guard_option.full_name }}{{ guard_option.label_suffix }}</option>{% endfor %}</select></label>
          <div class="small-muted">Unavailable guards are disabled when they are on approved leave or already assigned to an overlapping shift.</div>
          <div class="row-2"><label>Date<input type="date" name="shift_date" value="{{ shift_form.shift_date }}" required></label><label>Start<input type="time" name="start_time" value="{{ shift_form.start_time }}" required></label></div>
          <div class="row-2"><label>End<input type="time" name="end_time" value="{{ shift_form.end_time }}" required></label><label>Notes<input type="text" name="notes" value="{{ shift_form.notes }}"></label></div>
          <button class="btn primary" type="submit">Create Shift</button>
        </form>
        {% endif %}
      </div>
    </section>
{% endblock %}'''

GUARDS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <section class="card" id="guards">
      <div class="section-head"><h3>Staff Users</h3><span>View and manage guards, supervisors, and admins</span></div>
      <form method="post" action="/admin/guards/new" class="stack compact">
        <h4>Add Guard</h4>
        <div class="row-2"><label>First Name<input type="text" name="first_name" required></label><label>Last Name<input type="text" name="last_name" required></label></div>
        <div class="row-3"><label>Email<input type="email" name="email" placeholder="Used for the guard login if you create one"></label><label>Phone<input type="text" name="phone"></label><label>License #<input type="text" name="license_number"></label></div>
        <div class="row-3"><label>Status<select name="status"><option value="active">active</option><option value="inactive">inactive</option></select></label><label>Training Status<input type="text" name="training_status" placeholder="pending"></label><label>Rating<input type="number" step="0.1" min="1" max="5" name="rating" value="5"></label></div>
        <div class="row-3"><label>Username<input type="text" name="username" placeholder="Optional login username"></label><label>Temporary Password<input type="text" name="temporary_password" placeholder="Required when creating a login"></label><label>Set PIN<input type="text" name="pin" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" placeholder="4-digit PIN"></label></div>
        <div class="helper-links inline-tools"><button class="btn ghost" type="submit" name="generate_pin" value="1">Generate random PIN</button><span class="small-muted">PIN is hashed and used by quick login.</span></div>
        <button class="btn primary" type="submit">Add Guard</button>
      </form>
      <hr>
      {% for guard in guards_module_rows %}
      <div class="list-item detailed">
        <div>
          <strong>{{ guard.first_name }} {{ guard.last_name }}</strong>
          <div class="small-muted">{{ guard.email or 'No email' }} · {{ guard.phone or 'No phone' }} · {{ guard.status }} · {{ guard.training_status or 'training n/a' }} · Rating {{ guard.rating or 5 }}</div>
          <div class="small-muted">Assigned Site: {{ guard.assigned_site_name or 'Unassigned' }}</div>
          <div class="small-muted">Login: {% if guard.login_user_id %}{{ guard.login_username }}{% if guard.login_email %} · {{ guard.login_email }}{% endif %}{% else %}No login account yet{% endif %}</div>
          <div class="small-muted">Quick PIN: {% if guard.login_pin_hash %}Configured{% else %}Not set{% endif %}</div>
        </div>
        <div class="actions vertical">
          <form method="post" action="/admin/guard/update" class="inline-form"><input type="hidden" name="guard_id" value="{{ guard.id }}"><input type="text" name="first_name" value="{{ guard.first_name }}" placeholder="First" required><input type="text" name="last_name" value="{{ guard.last_name }}" placeholder="Last" required><input type="email" name="email" value="{{ guard.login_email or guard.email or '' }}" placeholder="Email"><input type="text" name="phone" value="{{ guard.phone or '' }}" placeholder="Phone"><input type="text" name="license_number" value="{{ guard.license_number or '' }}" placeholder="License #"><select name="status"><option value="active" {% if guard.status == 'active' %}selected{% endif %}>active</option><option value="inactive" {% if guard.status == 'inactive' %}selected{% endif %}>inactive</option></select><input type="text" name="training_status" value="{{ guard.training_status or '' }}" placeholder="Training"><label>Assigned Site<select name="site_id"><option value="">Unassigned</option>{% for site in active_sites %}<option value="{{ site.id }}" {% if guard.site_id == site.id %}selected{% endif %}>{{ site.name }}</option>{% endfor %}</select></label><input type="text" name="username" value="{{ guard.login_username or '' }}" placeholder="Username"><input type="text" name="temporary_password" placeholder="Temporary password"><input type="text" name="pin" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" placeholder="Set PIN"><button class="btn ghost" type="submit" name="generate_pin" value="1">Generate PIN</button><button class="btn" type="submit">Save</button></form>
          {% if guard.status == 'active' %}<form method="post" action="/admin/guard/deactivate" class="inline-form"><input type="hidden" name="guard_id" value="{{ guard.id }}"><button class="btn ghost" type="submit">Deactivate</button></form>{% endif %}
        </div>
      </div>
      {% else %}<div class="empty">No guards added yet.</div>{% endfor %}
    </section>
    <section class="card">
      <div class="section-head"><h3>Staff Accounts</h3><span>Role and supervisor site assignment management</span></div>
      {% for staff in staff_users %}
      <div class="list-item detailed">
        <div>
          <strong>{{ staff.full_name }}</strong>
          <div class="small-muted">{{ staff.username }} · {{ staff.email or 'No email' }} · {{ staff.role }}</div>
          {% if staff.role == 'supervisor' %}
          <div class="small-muted">Assigned Sites: {% if staff.supervisor_sites %}{% for site in staff.supervisor_sites %}{{ site.name }}{% if not loop.last %}, {% endif %}{% endfor %}{% else %}None{% endif %}</div>
          {% endif %}
        </div>
        <form method="post" action="/admin/user/update" class="inline-form">
          <input type="hidden" name="user_id" value="{{ staff.id }}">
          <label>Role<select name="role"><option value="guard" {% if staff.role == 'guard' %}selected{% endif %}>guard</option><option value="supervisor" {% if staff.role == 'supervisor' %}selected{% endif %}>supervisor</option><option value="admin" {% if staff.role in ['company_admin','admin'] %}selected{% endif %}>admin</option></select></label>
          {% if staff.role == 'supervisor' %}
          <div class="stack compact">
            <div class="small-muted">Assigned Sites</div>
            {% for site in active_sites %}
            <label><input type="checkbox" name="supervisor_site_{{ site.id }}" value="1" {% if staff.supervisor_sites and (site.id in staff.supervisor_sites|map(attribute='id')|list) %}checked{% endif %}> {{ site.name }}</label>
            {% endfor %}
          </div>
          {% endif %}
          <button class="btn" type="submit">Save</button>
        </form>
      </div>
      {% else %}<div class="empty">No staff users found.</div>{% endfor %}
    </section>
{% endblock %}'''


PATROLS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <section id="patrol-issues" class="grid stats-grid">
      <div class="stat card"><div class="stat-label">Active Patrols{% if patrol_issue_alert and patrol_issue_alert.active_count %}<span class="issue-badge warning">{{ patrol_issue_alert.active_count }}</span>{% endif %}</div><div class="stat-number">{{ stats.active_patrols }}</div></div>
      <div class="stat card"><div class="stat-label">Completed Tours</div><div class="stat-number">{{ stats.completed_tours }}</div></div>
      <div class="stat card"><div class="stat-label">Excused Patrols</div><div class="stat-number">{{ stats.excused_patrols or 0 }}</div></div>
      <div class="stat card"><div class="stat-label">Missed Patrols{% if patrol_issue_alert and patrol_issue_alert.missed_count %}<span class="issue-badge danger">{{ patrol_issue_alert.missed_count }}</span>{% endif %}</div><div class="stat-number">{{ stats.missed_checkpoints }}</div></div>
    </section>

    {% if user.role in ['company_admin', 'superadmin', 'admin', 'supervisor'] %}
    <section class="grid two-col">
      <div class="card"><div class="section-head"><h3>Patrol Tours</h3><span>Create routes and manage QR/NFC checkpoints</span></div><form method="post" action="/admin/patrol/tour/new" class="stack compact"><h4>Create Patrol Route</h4><div class="row-2"><label>Site<select name="site_id" required>{% for site in sites %}<option value="{{ site.id }}">{{ site.name }}</option>{% endfor %}</select></label><label>Tour Name<input type="text" name="name" placeholder="Perimeter Tour" required></label></div><label>Description<input type="text" name="description"></label><label>Checkpoints<textarea name="checkpoints" rows="5" required>Front Gate
Loading Dock
Fence Line North
Parking Lot
Back Entrance</textarea></label><button class="btn primary" type="submit">Create Tour + Checkpoints</button></form><hr>{% for tour in patrol_tours %}<div class="list-item detailed"><div><strong>{{ tour.name }}</strong><div class="small-muted">{{ tour.site_name }} · {{ tour.checkpoint_count }} checkpoints · {% if tour.active %}Active{% else %}Inactive{% endif %}</div></div><div class="actions"><a class="btn ghost" href="/patrol/tour?id={{ tour.id }}">Manage Checkpoints</a></div></div>{% else %}<div class="empty">No patrol tours configured.</div>{% endfor %}</div>
      <div class="card"><div class="section-head"><h3>Review Incomplete Patrols</h3><span>Add admin notes, excuse valid exceptions, or keep patrols as missed</span></div>{% for run in missed_tours[:12] %}<div class="list-item detailed"><div><strong>{{ run.tour_name }}</strong><div class="small-muted">{{ run.site_name }} · {{ run.guard_name }} · Started {{ run.started_at }} · {{ run.status.replace('_', ' ').title() }}</div><div class="small-muted">Progress {{ run.scanned_checkpoints }}/{{ run.total_checkpoints }} · Missed checkpoints {{ run.missed_checkpoint_count or 0 }}</div><form method="post" action="/admin/patrol/review" class="stack compact top-gap"><input type="hidden" name="run_id" value="{{ run.id }}"><label>Reason<select name="reason" required>{% for reason in patrol_excuse_reasons %}<option value="{{ reason }}">{{ reason }}</option>{% endfor %}</select></label><label>Admin/Supervisor Note<textarea name="note" rows="3" required placeholder="Explain the safety or operational exception."></textarea></label><div class="actions"><button class="btn primary" type="submit" name="action" value="excuse">Mark Excused</button><button class="btn ghost" type="submit" name="action" value="missed">Keep as Missed</button><a class="btn ghost" href="/patrol/run?id={{ run.id }}">Review Tour</a></div></form></div></div>{% else %}<div class="empty">No incomplete patrols need review.</div>{% endfor %}</div>
    </section>
    <section class="grid two-col">
      <div class="card"><div class="section-head"><h3>Completed Tours</h3><span>Review recent completed patrol history</span></div>{% for run in completed_tours[:12] %}<div class="list-item detailed"><div><strong>{{ run.tour_name }}</strong><div class="small-muted">{{ run.site_name }} · {{ run.guard_name }} · {{ run.completed_at }}</div><div class="small-muted">Scanned {{ run.scanned_checkpoints }}/{{ run.total_checkpoints }} · Missed checkpoints {{ run.missed_checkpoint_count or 0 }}</div></div><div class="actions"><a class="btn ghost" href="/patrol/run?id={{ run.id }}">Review Tour</a></div></div>{% else %}<div class="empty">No completed patrol tours yet.</div>{% endfor %}</div>
      <div class="card"><div class="section-head"><h3>Excused Patrols</h3><span>Valid exceptions excluded from missed patrol totals</span></div>{% for run in excused_tours[:12] %}<div class="list-item detailed"><div><strong>{{ run.tour_name }}</strong><div class="small-muted">{{ run.site_name }} · {{ run.guard_name }} · Excused {{ run.excused_at }} by {{ run.excused_by_name or 'Admin/Supervisor' }}</div><div class="small-muted">{{ run.excused_reason }} · {{ run.excused_note }}</div></div><div class="actions"><a class="btn ghost" href="/patrol/run?id={{ run.id }}">Review Tour</a></div></div>{% else %}<div class="empty">No excused patrols yet.</div>{% endfor %}</div>
    </section>
    {% elif user.role == 'guard' %}
    <section class="grid two-col">
      <div class="card"><div class="section-head"><h3>Mobile Patrol Tours</h3><span>Start a patrol tour and scan QR/NFC checkpoints</span></div><form method="post" action="/patrol/start" class="stack compact"><label>Tour<select name="tour_id" required>{% for tour in active_patrol_tours %}<option value="{{ tour.id }}">{{ tour.site_name }} · {{ tour.name }} ({{ tour.checkpoint_count }} checkpoints)</option>{% endfor %}</select></label><button class="btn primary" type="submit" {% if not active_patrol_tours %}disabled{% endif %}>Start Patrol Tour</button></form>{% if not active_patrol_tours %}<div class="empty">No active patrol tours are configured yet.</div>{% endif %}</div>
      <div class="card"><div class="section-head"><h3>My Patrol Runs</h3><span>Continue or review recent tours; submit notes if a patrol cannot be safely completed</span></div>{% for run in patrol_runs[:12] %}<div class="list-item detailed"><div><strong>{{ run.tour_name }}</strong><div class="small-muted">{{ run.site_name }} · {{ run.started_at }} · {{ run.status.replace('_', ' ').title() }}</div><div class="small-muted">Progress: {{ run.scanned_checkpoints }}/{{ run.total_checkpoints }} · Missed: {{ run.missed_checkpoint_count or 0 }}</div></div><div class="actions"><a class="btn ghost" href="/patrol/run?id={{ run.id }}">Open</a></div></div>{% else %}<div class="empty">No patrol runs yet.</div>{% endfor %}</div>
    </section>
    {% elif user.role == 'client' %}
    <section class="grid two-col">
      <div class="card"><div class="section-head"><h3>Patrol History</h3><span>Read-only completed and excused tours</span></div>{% for run in client_patrol_history %}<div class="list-item detailed"><div><strong>{{ run.tour_name }}</strong><div class="small-muted">{{ run.site_name }} · {% if run.excused_at %}Excused {{ run.excused_at }} by {{ run.excused_by_name or 'Admin/Supervisor' }}{% else %}Completed {{ run.completed_at }}{% endif %}</div><div class="small-muted">Scanned {{ run.scanned_checkpoints }}/{{ run.total_checkpoints }} · {% if run.excused_at %}Missed 0 · Not Counted{% else %}Missed {{ run.missed_checkpoint_count or 0 }}{% endif %}{% if run.excused_reason %} · {{ run.excused_reason }}{% endif %}</div></div><div class="actions"><a class="btn ghost" href="/patrol/run?id={{ run.id }}">View</a></div></div>{% else %}<div class="empty">No patrol history yet.</div>{% endfor %}</div>
    </section>
    {% endif %}
{% endblock %}
'''
PATROL_RUN_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="card">
  <div class="section-head"><h3>{{ run.tour_name }}</h3><span>{{ run.site_name }} · {{ run.status.replace('_', ' ').title() }}</span></div>
  <div class="row-4"><div><strong>Guard</strong><div class="small-muted">{{ run.guard_name }} · ID {{ run.guard_id }}</div></div><div><strong>Site ID</strong><div class="small-muted">{{ run.site_id }}</div></div><div><strong>Tour ID</strong><div class="small-muted">{{ run.tour_id }}</div></div><div><strong>Missed Checkpoints</strong><div class="small-muted">{% if (run.status == 'excused' or run.excused_at) %}0 · Not Counted{% else %}{{ run.missed_checkpoint_count or 0 }}{% endif %}</div></div></div>
  <div class="small-muted">Started {{ run.started_at }}{% if run.completed_at %} · Completed {{ run.completed_at }}{% endif %}{% if run.excused_at %} · Excused {{ run.excused_at }} by {{ run.excused_by_name or 'Admin/Supervisor' }}{% endif %}</div>
  {% if run.excused_at %}<div class="notice success top-gap"><strong>Excused / Not Counted</strong><div class="summary-list"><div><span>Reason</span><strong>{{ run.excused_reason or 'Not specified' }}</strong></div><div><span>Admin/Supervisor Note</span><strong>{{ run.excused_note or 'No note provided' }}</strong></div><div><span>Excused By</span><strong>{{ run.excused_by_name or 'Admin/Supervisor' }}</strong></div><div><span>Date/Time Excused</span><strong>{{ run.excused_at }}</strong></div></div></div>{% endif %}
</section>
{% if user.role == 'guard' or can_review_patrol %}
<section class="card">
  <div class="section-head"><h3>Patrol Notes</h3><span>{% if user.role == 'guard' %}Guards can submit notes/explanations only; admins and supervisors decide exceptions.{% else %}Admin and supervisor notes are preserved for client accountability.{% endif %}</span></div>
  <form method="post" action="/patrol/note" class="stack compact"><input type="hidden" name="run_id" value="{{ run.id }}"><label>Note / Explanation<textarea name="note" rows="3" required placeholder="Add context for this patrol."></textarea></label><button class="btn primary" type="submit">Add Note</button></form>
  {% if can_review_patrol and run.status not in ['completed', 'excused'] %}
  <hr>
  <form method="post" action="/admin/patrol/review" class="stack compact"><input type="hidden" name="run_id" value="{{ run.id }}"><label>Required Reason<select name="reason" required>{% for reason in patrol_excuse_reasons %}<option value="{{ reason }}">{{ reason }}</option>{% endfor %}</select></label><label>Required Admin/Supervisor Note<textarea name="note" rows="3" required placeholder="Document why this incomplete patrol should be excused or kept missed."></textarea></label><div class="actions"><button class="btn primary" type="submit" name="action" value="excuse">Mark Excused</button><button class="btn ghost" type="submit" name="action" value="missed">Keep as Missed</button></div></form>
  {% endif %}
</section>
{% endif %}
<section class="card">
  <div class="section-head"><h3>Patrol Checklist</h3><span>Scan QR, tap/enter NFC, or use manual testing fallback</span></div>
  <div class="checkpoint-grid">
    {% for checkpoint in checkpoints %}
    <article class="checkpoint-card {% if (run.status == 'excused' or run.excused_at) and (checkpoint.missed_checkpoint or not checkpoint.scanned_at) %}done{% elif checkpoint.missed_checkpoint %}missed{% elif checkpoint.scanned_at %}done{% endif %}">
      <div class="checkpoint-card-head">
        <div><span class="checkpoint-order">{{ checkpoint.sort_order }}</span> <strong>{{ checkpoint.checkpoint_name }}</strong><div class="small-muted">Checkpoint ID {{ checkpoint.id }}</div></div>
        <div>{% if (run.status == 'excused' or run.excused_at) and (checkpoint.missed_checkpoint or not checkpoint.scanned_at) %}<span class="badge completed">Excused / Not Required</span>{% elif checkpoint.missed_checkpoint %}<span class="badge declined">Missed</span>{% elif checkpoint.scanned_at %}<span class="badge completed">Completed</span>{% else %}<span class="badge pending">Pending</span>{% endif %}</div>
      </div>
      <div class="identifier-list"><div><span>QR</span><code>{{ checkpoint.qr_code }}</code></div><div><span>NFC</span><code>{{ checkpoint.nfc_tag_id }}</code></div></div>
      {% if (run.status == 'excused' or run.excused_at) and (checkpoint.missed_checkpoint or not checkpoint.scanned_at) %}<div class="small-muted">Checkpoint not required because this patrol was excused / not counted.</div>{% elif checkpoint.scanned_at %}<div class="small-muted">{{ checkpoint.scan_method }} completed at {{ checkpoint.scanned_at }}{% if checkpoint.gps_latitude or checkpoint.gps_longitude %} · GPS {{ checkpoint.gps_latitude }}, {{ checkpoint.gps_longitude }}{% endif %}</div>{% endif %}
      {% if user.role == 'guard' and run.status == 'in_progress' and not checkpoint.scanned_at %}
      <div class="scan-actions">
        <form method="post" action="/patrol/scan" class="stack compact offline-queue-form" data-offline-kind="patrol_scan"><input type="hidden" name="run_id" value="{{ run.id }}"><input type="hidden" name="checkpoint_id" value="{{ checkpoint.id }}"><input type="hidden" name="scan_method" value="QR"><label>QR Identifier<input type="text" name="scan_value" placeholder="Scan or enter QR value" required></label><div class="row-2"><label>GPS Lat<input type="text" name="gps_latitude"></label><label>GPS Lng<input type="text" name="gps_longitude"></label></div><button class="btn primary" type="submit">Scan QR</button></form>
        <form method="post" action="/patrol/scan" class="stack compact offline-queue-form" data-offline-kind="patrol_scan"><input type="hidden" name="run_id" value="{{ run.id }}"><input type="hidden" name="checkpoint_id" value="{{ checkpoint.id }}"><input type="hidden" name="scan_method" value="NFC"><label>NFC Identifier<input type="text" name="scan_value" placeholder="Tap or enter NFC value" required></label><button class="btn" type="submit">Tap/Enter NFC</button></form>
        <form method="post" action="/patrol/scan" class="stack compact offline-queue-form" data-offline-kind="patrol_scan"><input type="hidden" name="run_id" value="{{ run.id }}"><input type="hidden" name="checkpoint_id" value="{{ checkpoint.id }}"><input type="hidden" name="scan_method" value="MANUAL"><label>Manual Note<input type="text" name="scan_value" placeholder="Testing fallback note"></label><button class="btn ghost" type="submit">Manual Entry Fallback</button></form>
      </div>
      {% endif %}
    </article>
    {% else %}<div class="empty">No checkpoints configured.</div>{% endfor %}
  </div>
  {% if user.role == 'guard' and run.status == 'in_progress' %}<form method="post" action="/patrol/complete" class="stack compact top-gap"><input type="hidden" name="run_id" value="{{ run.id }}"><button class="btn primary" type="submit">Complete Tour / Mark Unfinished Checkpoints Missed</button></form>{% endif %}
</section>
<section class="card">
  <div class="section-head"><h3>Patrol Timeline</h3><span>Audit trail for client accountability</span></div>
  <div class="timeline-list">{% for event in patrol_timeline %}<div class="list-item detailed"><div><strong>{{ event.event_label }}</strong><div class="small-muted">{{ event.created_at }}{% if event.actor_name %} · {{ event.actor_name }}{% endif %}{% if event.reason %} · {{ event.reason }}{% endif %}</div>{% if event.event_note %}<div>{{ event.event_note }}</div>{% endif %}</div></div>{% else %}<div class="empty">No patrol timeline entries yet.</div>{% endfor %}</div>
</section>
{% endblock %}
'''

PATROL_TOUR_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="card">
  <div class="section-head"><div><h3>{{ tour.name }}</h3><span>{{ tour.site_name }} · {{ tour.checkpoint_count }} active checkpoints</span></div><a class="btn ghost" href="/patrols">Back to Patrols</a></div>
  <div class="summary-list">
    <div><span>Site</span><strong>{{ tour.site_name }}</strong></div>
    <div><span>Status</span><strong>{% if tour.active %}Active{% else %}Inactive{% endif %}</strong></div>
    <div><span>Checkpoints</span><strong>{{ tour.checkpoint_count }}</strong></div>
  </div>
  <p>{{ tour.description or 'No description.' }}</p>
  {% if user.role in ['company_admin', 'superadmin', 'admin', 'supervisor'] %}
  <form method="post" action="/admin/patrol/checkpoint/new" class="stack compact">
    <input type="hidden" name="tour_id" value="{{ tour.id }}">
    <div class="row-3"><label>Checkpoint Name<input type="text" name="checkpoint_name" placeholder="Front Gate" required></label><label>QR Identifier (optional)<input type="text" name="qr_code" placeholder="Auto-generated if blank"></label><label>NFC Tag ID (optional)<input type="text" name="nfc_tag_id" placeholder="Auto-generated if blank"></label></div>
    <div class="row-2"><label>Sort Order<input type="number" name="sort_order" value="{{ checkpoints|length + 1 }}"></label><label>Status<select name="active"><option value="1">Active</option><option value="0">Inactive</option></select></label></div>
    <button class="btn primary" type="submit">Add Checkpoint</button>
  </form>
  {% endif %}
</section>
<section class="card">
  <div class="section-head"><h3>Checkpoint Manager</h3><span>Individual checkpoint cards with QR/NFC tools</span></div>
  <div class="checkpoint-grid">
    {% for checkpoint in checkpoints %}
    <article class="checkpoint-card">
      <div class="checkpoint-card-head">
        <div><span class="checkpoint-order">{{ checkpoint.sort_order }}</span> <strong>{{ checkpoint.checkpoint_name }}</strong><div class="small-muted">Checkpoint ID {{ checkpoint.id }}</div></div>
        <span class="badge {% if checkpoint.active %}completed{% else %}declined{% endif %}">{% if checkpoint.active %}Active{% else %}Inactive{% endif %}</span>
      </div>
      <div class="identifier-list">
        <div><span>QR Code</span><code>{{ checkpoint.qr_code }}</code><button class="btn ghost copy-btn" type="button" data-copy="{{ checkpoint.qr_code }}">Copy QR</button></div>
        <div><span>NFC Tag</span><code>{{ checkpoint.nfc_tag_id }}</code><button class="btn ghost copy-btn" type="button" data-copy="{{ checkpoint.nfc_tag_id }}">Copy NFC</button></div>
      </div>
      <div class="actions">
        <a class="btn" href="/patrol/checkpoint/qr?id={{ checkpoint.id }}" target="_blank" rel="noopener">View / Print QR</a>
        {% if user.role in ['company_admin', 'superadmin', 'admin', 'supervisor'] %}
        <form method="post" action="/admin/patrol/checkpoint/generate-qr" class="inline-form"><input type="hidden" name="checkpoint_id" value="{{ checkpoint.id }}"><button class="btn ghost" type="submit">Generate QR</button></form>
        <form method="post" action="/admin/patrol/checkpoint/delete" class="inline-form" onsubmit="return confirm('Delete this checkpoint? Existing run history remains available.');"><input type="hidden" name="checkpoint_id" value="{{ checkpoint.id }}"><button class="btn danger" type="submit">Delete</button></form>
        {% endif %}
      </div>
      {% if user.role in ['company_admin', 'superadmin', 'admin', 'supervisor'] %}
      <details class="checkpoint-edit"><summary class="btn ghost">Edit checkpoint</summary>
        <form method="post" action="/admin/patrol/checkpoint/update" class="stack compact">
          <input type="hidden" name="checkpoint_id" value="{{ checkpoint.id }}">
          <div class="row-2"><label>Name<input type="text" name="checkpoint_name" value="{{ checkpoint.checkpoint_name }}" required></label><label>Sort Order<input type="number" name="sort_order" value="{{ checkpoint.sort_order }}"></label></div>
          <div class="row-2"><label>QR Identifier<input type="text" name="qr_code" value="{{ checkpoint.qr_code }}" required></label><label>NFC Tag Identifier<input type="text" name="nfc_tag_id" value="{{ checkpoint.nfc_tag_id }}" required></label></div>
          <label>Status<select name="active"><option value="1" {% if checkpoint.active %}selected{% endif %}>Active</option><option value="0" {% if not checkpoint.active %}selected{% endif %}>Inactive</option></select></label>
          <button class="btn primary" type="submit">Edit / Save</button>
        </form>
      </details>
      {% endif %}
    </article>
    {% else %}<div class="empty">No checkpoints yet. Add the first checkpoint above.</div>{% endfor %}
  </div>
</section>
<script>
(function () {
  document.querySelectorAll('.copy-btn').forEach(function (button) {
    button.addEventListener('click', function () {
      var value = button.getAttribute('data-copy') || '';
      function done() { var old = button.textContent; button.textContent = 'Copied'; setTimeout(function () { button.textContent = old; }, 1200); }
      if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(value).then(done); }
      else { var input = document.createElement('input'); input.value = value; document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove(); done(); }
    });
  });
})();
</script>
{% endblock %}'''

REPORTS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <style>
      .attachment-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:8px}
      .attachment-item{border:1px solid rgba(255,255,255,.12);border-radius:10px;padding:10px;background:rgba(9,12,19,.5)}
      .attachment-thumb{max-width:100%;max-height:130px;border-radius:8px;display:block;object-fit:cover}
      .attachment-links{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
      .history-block{margin-top:10px;padding:10px;border:1px solid rgba(255,255,255,.12);border-radius:10px;background:rgba(10,10,10,.52)}
      .history-row{margin-top:6px}
    </style>
    <section class="card">
      <div class="section-head"><h3>Report Management</h3><span>Incident + Daily Activity reports</span></div>
      <form method="get" action="/reports" class="stack compact">
        <div class="actions" style="gap:8px;flex-wrap:wrap;">
          <a class="btn {% if not report_filters.type %}primary{% else %}ghost{% endif %}" href="/reports">All</a>
          <a class="btn {% if report_filters.type == 'incident' %}primary{% else %}ghost{% endif %}" href="/reports?type=incident">Incident</a>
          <a class="btn {% if report_filters.type == 'daily_activity' %}primary{% else %}ghost{% endif %}" href="/reports?type=daily_activity">Daily Activity</a>
          {% for status in report_status_options %}<a class="btn {% if report_filters.status == status|lower %}primary{% else %}ghost{% endif %}" href="/reports?status={{ status|lower|urlencode }}">{{ status }}</a>{% endfor %}
        </div>
        <div class="row-4">
          <label>Search<input type="search" name="q" value="{{ report_filters.q }}" placeholder="Narrative, officer, site, persons involved"></label>
          <label>Officer<select name="officer_id"><option value="">All officers</option>{% for officer in report_filter_officers %}<option value="{{ officer.id }}" {% if report_filters.officer_id == officer.id|string %}selected{% endif %}>{{ officer.full_name }}</option>{% endfor %}</select></label>
          <label>Site<select name="site_id"><option value="">All sites</option>{% for site in report_filter_sites %}<option value="{{ site.id }}" {% if report_filters.site_id == site.id|string %}selected{% endif %}>{{ site.name }}</option>{% endfor %}</select></label>
          <label>Status<select name="status"><option value="">All</option>{% for status in report_status_options %}<option value="{{ status|lower }}" {% if report_filters.status == status|lower %}selected{% endif %}>{{ status }}</option>{% endfor %}</select></label>
        </div>
        <input type="hidden" name="type" value="{{ report_filters.type }}">
        <div class="actions"><button class="btn primary" type="submit">Apply</button><a class="btn ghost" href="/reports">Reset</a></div>
      </form>
    </section>
    <section class="card">
      <div class="section-head"><h3>Newest First</h3><span>Click a report to expand full details</span></div>
      {% for report in managed_reports %}
      <details class="report-details report-card">
        <summary>
          <div class="report-top"><strong>{{ report.report_type }}</strong><span class="badge {{ report.status|lower|replace(' ', '-') }}">{{ report.status }}</span>{% if report.priority %}<span class="badge">{{ report.priority }}</span>{% endif %}</div>
          <div class="small-muted">{{ report.created_at }} · {{ report.site_name }} · {{ report.officer_name }}</div>
        </summary>
        <p><strong>Narrative / Summary:</strong> {{ report.narrative or 'N/A' }}</p>
        <p><strong>Persons Involved:</strong> {{ report.persons_involved or 'N/A' }}</p>
        <p><strong>Witnesses:</strong> {{ report.witnesses or 'N/A' }}</p>
        {% if report.resolved_at %}<div class="small-muted"><strong>Resolved:</strong> {{ report.resolved_at }}</div>{% endif %}
        <div class="small-muted">Uploaded {{ report.uploaded_at or 'N/A' }}{% if report.uploaded_by %} by {{ report.uploaded_by }}{% endif %}</div>
        {% if report.photo_attachment or report.file_attachment %}
        <div class="attachment-grid">
          {% if report.photo_attachment %}
          <div class="attachment-item">
            <div class="small-muted">Photo Attachment</div>
            {% if report.photo_attachment.is_available %}
              <a href="{{ report.photo_attachment.secure_url }}" target="_blank" rel="noopener">
                <img class="attachment-thumb" src="{{ report.photo_attachment.secure_url }}" alt="Report image attachment">
              </a>
              <div class="attachment-links"><a class="btn ghost" href="{{ report.photo_attachment.secure_url }}" target="_blank" rel="noopener">Preview</a><a class="btn ghost" href="{{ report.photo_attachment.secure_url }}?download=1">Download</a></div>
            {% else %}
              <div class="small-muted">Unavailable (missing from storage)</div>
            {% endif %}
          </div>
          {% endif %}
          {% if report.file_attachment %}
          <div class="attachment-item">
            <div class="small-muted">Document Attachment</div>
            {% if not report.file_attachment.is_available %}
            <div class="small-muted">Unavailable (missing from storage)</div>
            {% elif report.file_attachment.is_image %}
            <a href="{{ report.file_attachment.secure_url }}" target="_blank" rel="noopener"><img class="attachment-thumb" src="{{ report.file_attachment.secure_url }}" alt="Report attachment"></a>
            {% else %}
            <div>{{ report.file_attachment.name }}</div>
            {% endif %}
            {% if report.file_attachment.is_available %}
            <div class="attachment-links"><a class="btn ghost" href="{{ report.file_attachment.secure_url }}" target="_blank" rel="noopener">View</a><a class="btn ghost" href="{{ report.file_attachment.secure_url }}?download=1">Download</a></div>
            {% endif %}
          </div>
          {% endif %}
        </div>
        {% endif %}
        <div class="history-block">
          <div class="small-muted"><strong>Report Activity Timeline</strong> (submission, status changes, note additions, closures)</div>
          {% for event in report.activity_timeline %}<div class="small-muted history-row">• {{ event.event_at or 'N/A' }} — <strong>{{ event.label }}</strong> by {{ event.actor_name or 'Unknown' }}: {{ event.description }}</div>{% else %}<div class="small-muted history-row">No activity yet.</div>{% endfor %}
        </div>
        <div class="history-block">
          <div class="small-muted"><strong>Supervisor Notes</strong></div>
          <div class="small-muted history-row">{{ report.supervisor_notes or 'No supervisor notes yet.' }}</div>
        </div>
        <div class="history-block">
          <div class="small-muted"><strong>Admin Notes</strong></div>
          <div class="small-muted history-row">{{ report.admin_notes or 'No admin notes yet.' }}</div>
        </div>
        {% if user.role in ['company_admin', 'superadmin', 'supervisor', 'admin'] %}
        <form method="post" action="/admin/reports/manage" class="stack compact">
          <input type="hidden" name="report_kind" value="{{ report.report_kind }}">
          <input type="hidden" name="report_id" value="{{ report.report_id }}">
          <label>Status<select name="status">{% for status in report_status_options %}<option value="{{ status }}" {% if report.status == status %}selected{% endif %}>{{ status }}</option>{% endfor %}</select></label>
          <div class="row-2"><label>Supervisor Notes<textarea name="supervisor_note" rows="2" placeholder="Add supervisor review note"></textarea></label><label>Admin Notes<textarea name="admin_note" rows="2" placeholder="Add admin review note"></textarea></label></div>
          <button class="btn primary" type="submit">Update Report</button>
        </form>
        {% endif %}
      </details>
      {% else %}<div class="empty">No reports found.</div>{% endfor %}
      <div class="actions" style="justify-content:space-between;margin-top:10px;">
        {% if report_pages.has_prev %}<a class="btn ghost" href="/reports?page={{ report_pages.current - 1 }}&type={{ report_filters.type }}&status={{ report_filters.status }}&officer_id={{ report_filters.officer_id }}&site_id={{ report_filters.site_id }}&q={{ report_filters.q|urlencode }}">Previous</a>{% else %}<span></span>{% endif %}
        <span class="small-muted">Page {{ report_pages.current }} of {{ report_pages.total }}</span>
        {% if report_pages.has_next %}<a class="btn ghost" href="/reports?page={{ report_pages.current + 1 }}&type={{ report_filters.type }}&status={{ report_filters.status }}&officer_id={{ report_filters.officer_id }}&site_id={{ report_filters.site_id }}&q={{ report_filters.q|urlencode }}">Next</a>{% endif %}
      </div>
    </section>
{% endblock %}'''


GUARD_DAILY_ACTIVITY_REPORTS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="grid two-col">
  <div class="card">
    <div class="section-head"><h3>Daily Activity Report</h3><span>Submit daily guard activity</span></div>
    <form method="post" action="/guard/daily-activity-reports/new" enctype="multipart/form-data" class="stack offline-queue-form" data-offline-kind="daily_activity" data-offline-file-field="photo">
      {{ csrf_input|safe }}
      <div class="row-2"><label>Officer<input type="text" value="{{ user.full_name }}" readonly></label><label>Assigned Site<input type="text" value="{{ assigned_site.name if assigned_site else 'Unassigned' }}" readonly></label></div>
      <div class="row-2"><label>Report Timestamp<input type="text" value="{{ server_now }}" readonly></label><label>Activity Type<select name="activity_type" required><option>Patrol</option><option>Gate Check</option><option>Visitor Log</option><option>Truck Entry</option><option>Parking Patrol</option><option>Perimeter Check</option><option>General Activity</option></select></label></div>
      <label>Summary / Notes<textarea name="summary" rows="5" required></textarea></label>
      <label>Optional Photo(s)<input type="file" name="photo" accept="image/*" multiple></label>
      <button class="btn primary" type="submit" {% if not assigned_site %}disabled{% endif %}>Submit Daily Activity Report</button>
      {% if not assigned_site %}<div class="small-muted">You need an assigned site before submitting a DAR.</div>{% endif %}
    </form>
  </div>
  <div class="card">
    <div class="section-head"><h3>Recent Daily Activity Reports</h3><span>Your latest submissions</span></div>
    {% for report in dar_reports %}<div class="report-card"><div class="report-top"><strong>{{ report.activity_type }}</strong><span class="badge {{ report.status }}">{{ report.status }}</span>{% if report.offline_submitted %}<span class="badge pending">Offline Submitted</span>{% endif %}{% if report.synced_at %}<span class="badge completed">Synced</span>{% endif %}</div><div class="small-muted">{{ report.site_name }} · {{ report.created_at }}</div><p>{{ report.summary }}</p>{% if report.photo_path %}<img class="report-photo" src="/{{ report.photo_path }}" alt="DAR photo">{% endif %}</div>{% else %}<div class="empty">No daily activity reports yet.</div>{% endfor %}
  </div>
</section>
{% endblock %}'''

GUARD_MY_REPORTS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="card">
  <div class="section-head"><h3>My Reports</h3><span>Daily Activity + Incident reports submitted by you</span></div>
  <form method="get" action="/guard/my-reports" class="stack">
    <input type="hidden" name="report_type" value="{{ filters.report_type }}">
    <div class="actions" style="gap:8px;flex-wrap:wrap;">
      <a class="btn {% if not filters.report_type %}primary{% else %}ghost{% endif %}" href="/guard/my-reports?q={{ filters.q|urlencode }}&site_id={{ filters.site_id|urlencode }}&date_from={{ filters.date_from|urlencode }}&date_to={{ filters.date_to|urlencode }}">All</a>
      <a class="btn {% if filters.report_type == 'daily_activity' %}primary{% else %}ghost{% endif %}" href="/guard/my-reports?report_type=daily_activity&q={{ filters.q|urlencode }}&site_id={{ filters.site_id|urlencode }}&date_from={{ filters.date_from|urlencode }}&date_to={{ filters.date_to|urlencode }}">Daily Activity</a>
      <a class="btn {% if filters.report_type == 'incident' %}primary{% else %}ghost{% endif %}" href="/guard/my-reports?report_type=incident&q={{ filters.q|urlencode }}&site_id={{ filters.site_id|urlencode }}&date_from={{ filters.date_from|urlencode }}&date_to={{ filters.date_to|urlencode }}">Incident</a>
    </div>
    <div class="row-4">
      <label>Search<input type="search" name="q" value="{{ filters.q }}" placeholder="Search summary, narrative, or site"></label>
      <label>Site<select name="site_id"><option value="">All sites</option>{% for site in filter_sites %}<option value="{{ site.id }}" {% if filters.site_id == site.id|string %}selected{% endif %}>{{ site.name }}</option>{% endfor %}</select></label>
      <label>Date From<input type="date" name="date_from" value="{{ filters.date_from }}"></label>
      <label>Date To<input type="date" name="date_to" value="{{ filters.date_to }}"></label>
    </div>
    <div class="actions"><button class="btn primary" type="submit">Apply</button><a class="btn ghost" href="/guard/my-reports">Reset</a></div>
  </form>
  <div style="max-height:65vh;overflow:auto;padding-right:6px;">
    {% for report in my_reports %}
    <a href="/guard/my-reports/{{ report.report_kind }}/{{ report.report_id }}" style="text-decoration:none;color:inherit;">
      <div class="report-card" style="transition:transform .2s ease,border-color .2s ease;border:1px solid rgba(255,255,255,.08);margin-top:10px;">
        <div class="report-top"><strong>{{ report.report_type }}</strong><div class="actions"><span class="badge {{ report.status|lower }}">{{ report.status }}</span><span class="badge">Submitted</span>{% if report.priority %}<span class="badge">{{ report.priority }}</span>{% endif %}</div></div>
        <div class="small-muted">{{ report.site_name }} · {{ report.created_at }}</div>
        <p>{{ report.preview }}</p>
        <div class="actions" style="margin-top:8px;"><span class="btn ghost">View Details</span></div>
      </div>
    </a>
    {% else %}<div class="empty">No reports found. Try broadening filters or submit your first report.</div>{% endfor %}
  </div>
</section>
{% endblock %}'''

GUARD_MY_REPORT_DETAIL_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="card">
  <div class="section-head"><h3>Report Detail</h3><span>Full report details</span></div>
  <div class="report-card">
    <div class="report-top"><strong>{{ report.report_type }}</strong><div class="actions"><span class="badge {{ report.status|lower }}">{{ report.status }}</span><span class="badge">Submitted</span>{% if report.priority %}<span class="badge">{{ report.priority }}</span>{% endif %}</div></div>
    <div class="small-muted">{{ report.site_name }} · {{ report.created_at }}</div>
    {% if report.report_kind == 'incident' %}
      <p><strong>Incident Type:</strong> {{ report.incident_type }}</p>
      <p><strong>Narrative:</strong> {{ report.narrative }}</p>
      <p><strong>Persons Involved:</strong> {{ report.persons_involved or 'N/A' }}</p>
      <p><strong>Witnesses:</strong> {{ report.witnesses or 'N/A' }}</p>
      <p><strong>Police Notified:</strong> {{ 'Yes' if report.police_notified else 'No' }}</p>
      <p><strong>Client Notified:</strong> {{ 'Yes' if report.client_notified else 'No' }}</p>
    {% else %}
      <p><strong>Activity Type:</strong> {{ report.activity_type }}</p>
      <p><strong>Summary:</strong> {{ report.summary }}</p>
    {% endif %}
    {% if report.attachments %}{% for file in report.attachments %}<div class="report-card" style="margin-top:8px;"><div class="small-muted">{{ file.file_name }} · {{ file.created_at }}{% if file.uploaded_by_name %} · {{ file.uploaded_by_name }}{% endif %}</div>{% if file.mime_type and file.mime_type.startswith('image/') %}<p><img class="report-photo" src="/report-files/{{ report.report_kind }}/{{ report.report_id }}/attachment/{{ file.id }}" alt="Report attachment"></p>{% endif %}<p><a class="btn ghost" href="/report-files/{{ report.report_kind }}/{{ report.report_id }}/attachment/{{ file.id }}" target="_blank" rel="noopener">Preview</a> <a class="btn ghost" href="/report-files/{{ report.report_kind }}/{{ report.report_id }}/attachment/{{ file.id }}?download=1">Download</a></p></div>{% endfor %}{% else %}
    {% if report.photo_path %}<p><img class="report-photo" src="/report-files/daily_activity/{{ report.report_id }}/photo" alt="Report photo"></p><p><a class="btn ghost" href="/report-files/daily_activity/{{ report.report_id }}/photo?download=1">Download Photo</a></p>{% endif %}
    {% if report.attachment_path %}<p><a class="btn ghost" href="/report-files/incident/{{ report.report_id }}/attachment" target="_blank" rel="noopener">Open Attachment</a> <a class="btn ghost" href="/report-files/incident/{{ report.report_id }}/attachment?download=1">Download</a></p>{% endif %}{% endif %}
    <p><a class="btn ghost" href="/guard/my-reports">Back to My Reports</a></p>
  </div>
</section>
{% endblock %}'''

GUARD_INCIDENT_REPORTS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
<section class="grid two-col">
  <div class="card">
    <div class="section-head"><h3>Incident Report</h3><span>Submit security incidents separately from DAR submissions</span></div>
    <form method="post" action="/guard/incident-reports/new" enctype="multipart/form-data" class="stack offline-queue-form" data-offline-kind="incident" data-offline-file-field="attachment">
      {{ csrf_input|safe }}
      <div class="row-2"><label>Officer<input type="text" value="{{ user.full_name }}" readonly></label><label>Assigned Site<input type="text" value="{{ assigned_site.name if assigned_site else 'Unassigned' }}" readonly></label></div>
      <div class="row-2"><label>Report Timestamp<input type="text" value="{{ server_now }}" readonly></label><label>Status<input type="text" value="Open" readonly></label></div>
      <div class="row-2"><label>Incident Type<select name="incident_type" required><option>Trespassing</option><option>Theft</option><option>Property Damage</option><option>Medical</option><option>Suspicious Activity</option><option>Alarm Response</option><option>Vehicle Incident</option><option>Fight / Disturbance</option><option>Fire / Safety</option><option>Other</option></select></label><label>Priority<select name="priority" required><option>Low</option><option selected>Medium</option><option>High</option><option>Critical</option></select></label></div>
      <label>Detailed Narrative<textarea name="narrative" rows="6" required></textarea></label>
      <div class="row-2"><label>Persons Involved<input type="text" name="persons_involved"></label><label>Witnesses<input type="text" name="witnesses"></label></div>
      <div class="row-2 checkbox-row">
        <label class="checkbox-inline"><input type="checkbox" name="police_notified" value="1"><span>Police Notified</span></label>
        <label class="checkbox-inline"><input type="checkbox" name="client_notified" value="1"><span>Client Notified</span></label>
      </div>
      <label>Optional Photo / Document(s)<input type="file" name="attachment" multiple></label>
      <div class="sticky-submit-wrap"><button class="btn primary sticky-submit" type="submit" {% if not assigned_site %}disabled{% endif %}>Submit Incident Report</button></div>
    </form>
  </div>
  <div class="card">
    <div class="section-head"><h3>Recent Incident Reports</h3><span>Your latest incident submissions</span></div>
    {% for report in incident_reports %}<div class="report-card"><div class="report-top"><strong>{{ report.incident_type }}</strong><span class="badge">{{ report.priority }}</span><span class="badge {{ report.status|lower }}">{{ report.status }}</span>{% if report.offline_submitted %}<span class="badge pending">Offline Submitted</span>{% endif %}{% if report.synced_at %}<span class="badge completed">Synced</span>{% endif %}</div><div class="small-muted">{{ report.site_name }} · {{ report.created_at }}</div><p>{{ report.narrative }}</p></div>{% else %}<div class="empty">No incident reports yet.</div>{% endfor %}
  </div>
</section>
{% endblock %}'''

PAYROLL_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <section class="grid two-col">
      <div class="card">
        <div class="section-head"><h3>Payroll Processing</h3><span>QuickBooks is the payroll source of truth</span></div>
        <form method="get" action="/admin/payroll" class="stack compact">
          <div class="row-2"><label>Pay Period Start<input type="date" name="start" value="{{ payroll_start or '' }}"></label><label>Pay Period End<input type="date" name="end" value="{{ payroll_end or '' }}"></label></div>
          <button class="btn primary" type="submit">Load Pay Period</button>
        </form>
        <div class="small-muted">SteeleOps prepares approved hours only. Taxes, direct deposit, official payroll and paystubs stay in QuickBooks.</div>
      </div>
      <div class="card">
        <div class="section-head"><h3>Status</h3><span>{{ payroll_period.status.replace('_', ' ').title() if payroll_period else 'Pending Approval' }}</span></div>
        <div class="small-muted">QuickBooks Connection: {% if qb_connected %}Connected{% else %}Not Connected{% endif %}</div>
        {% if qb_company_name %}<div class="small-muted">Connected to QuickBooks: {{ qb_company_name }}</div>{% endif %}
        {% if qb_connected %}
        <form method="post" action="/admin/settings/quickbooks/connect" class="stack compact">
          {{ csrf_input|safe }}
          <button class="btn ghost" type="button" disabled>QuickBooks Connected</button>
        </form>
        <form method="post" action="/admin/settings/quickbooks/reconnect" class="stack compact">
          {{ csrf_input|safe }}
          <button class="btn ghost" type="submit">Reconnect QuickBooks</button>
        </form>
        {% else %}
        <form method="post" action="/admin/settings/quickbooks/connect" class="stack compact">
          {{ csrf_input|safe }}
          <button class="btn ghost" type="submit">Connect to QuickBooks</button>
        </form>
        {% endif %}
        <div class="small-muted">Send Status: {{ payroll_send_status or 'Not Sent' }}</div>
        {% if payroll_export_blocked %}<div class="alert error">Approve all payroll rows before export.</div>{% endif %}
        {% if query_preview %}<div class="alert">Payroll batch preview is shown below. CSV download is deprecated.</div>{% endif %}
        {% if payroll_rows is defined %}
        <form method="get" action="/admin/payroll/export.csv" class="stack compact">
          <input type="hidden" name="start" value="{{ payroll_start }}">
          <input type="hidden" name="end" value="{{ payroll_end }}">
          <button class="btn" type="submit" {% if not payroll_can_export %}disabled{% endif %}>Preview Payroll Batch</button>
          {% if not payroll_can_export %}<div class="small-muted">Review and approve all guard hours before previewing the payroll batch.</div>{% endif %}
        </form>
        <form method="post" action="/admin/payroll/send-to-quickbooks" class="stack compact">
          {{ csrf_input|safe }}
          <input type="hidden" name="start" value="{{ payroll_start }}">
          <input type="hidden" name="end" value="{{ payroll_end }}">
          <button class="btn primary" type="submit" {% if (not payroll_can_export) or (not qb_connected) %}disabled{% endif %}>Send to QuickBooks</button>
          {% if not qb_connected %}<div class="small-muted">Connect QuickBooks before sending payroll.</div>{% endif %}
        </form>
        {% else %}<div class="empty">Choose a pay period to preview the payroll batch.</div>{% endif %}
      </div>
    </section>
    <section class="card">
      <div class="section-head"><h3>Payroll Summary</h3><span>{{ payroll_start or 'Select a start date' }}{% if payroll_end %} to {{ payroll_end }}{% endif %}</span></div>
      {% if payroll_rows is defined %}
      <div class="table-wrap">
        <table>
          <thead><tr><th>Guard Name</th><th>Site / Location</th><th>Regular Hours</th><th>Overtime Hours</th><th>Total Hours</th><th>Pay Rate</th><th>Estimated Gross Pay</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>{% for row in payroll_rows %}<tr><td>{{ row.full_name }}</td><td>{{ row.site_name }}</td><td>{{ '%.2f'|format(row.regular_hours or 0) }}</td><td>{{ '%.2f'|format(row.overtime_hours or 0) }}</td><td>{{ '%.2f'|format(row.total_hours or 0) }}</td><td>${{ '%.2f'|format(row.hourly_rate or 0) }}</td><td>${{ '%.2f'|format(row.gross_pay or 0) }}</td><td><span class="badge assigned">{{ row.review_status_label }}</span></td><td><a class="btn ghost" href="/admin/payroll/review?start={{ payroll_start }}&end={{ payroll_end }}&guard_id={{ row.guard_id }}">Review</a></td></tr>{% else %}<tr><td colspan="9">No payroll rows found for this period.</td></tr>{% endfor %}</tbody>
        </table>
      </div>
      {% else %}<div class="empty">Generate a report to view payroll totals.</div>{% endif %}
    </section>
    {% if payroll_review %}
    <section class="card">
      <div class="section-head"><h3>Payroll Review</h3><span>{{ payroll_review.guard_name }} · {{ payroll_review.period_start }} to {{ payroll_review.period_end }}</span></div>
      {% if payroll_review.locked %}<div class="alert">Payroll has already been exported to QuickBooks. This review is view-only.</div>{% endif %}
      {% if payroll_review.error %}<div class="alert error">{{ payroll_review.error }}</div>{% endif %}
      <div class="row-3">
        <div><strong>Site</strong><div class="small-muted">{{ payroll_review.site_name }}</div></div>
        <div><strong>Clock Records</strong><div class="small-muted">{{ payroll_review.clock_records }}</div></div>
        <div><strong>Missing Punch</strong><div class="small-muted">{{ payroll_review.missing_warning }}</div></div>
      </div>
        <div class="row-3">
        <div><strong>Total</strong><div class="small-muted">{{ '%.2f'|format(payroll_review.total_hours) }} hrs</div></div>
        <div><strong>Regular / OT</strong><div class="small-muted">{{ '%.2f'|format(payroll_review.regular_hours) }} / {{ '%.2f'|format(payroll_review.overtime_hours) }}</div></div>
        <div><strong>Rate / Gross</strong><div class="small-muted">${{ '%.2f'|format(payroll_review.pay_rate) }} / ${{ '%.2f'|format(payroll_review.gross_pay) }}</div></div>
      </div>
      {% if payroll_review.manual_override_used %}<div class="badge pending">Manual Hours Override</div>{% endif %}
      {% if payroll_review.show_manual_override %}<div class="small-muted">Manual hours require admin approval before payroll export.</div>{% endif %}
      <div class="small-muted">Guard notes: {{ payroll_review.guard_notes }}</div>
      <form method="post" action="/admin/payroll/review/action" class="stack compact">
        {{ csrf_input|safe }}
        <input type="hidden" name="start" value="{{ payroll_start }}">
        <input type="hidden" name="end" value="{{ payroll_end }}">
        <input type="hidden" name="guard_id" value="{{ payroll_review.guard_id }}">
        <label>Admin Notes<textarea name="admin_notes" rows="3">{{ payroll_review.admin_notes }}</textarea></label>
        {% if payroll_review.show_manual_override %}
        <div class="card">
          <div class="section-head"><h3>Manual Hours Override</h3><span>Controlled admin correction</span></div>
          <div class="row-2">
            <label>Regular Hours<input type="number" min="0" step="0.01" name="manual_regular_hours" value="{{ '%.2f'|format(payroll_review.manual_regular_hours) }}"></label>
            <label>Overtime Hours<input type="number" min="0" step="0.01" name="manual_overtime_hours" value="{{ '%.2f'|format(payroll_review.manual_overtime_hours) }}"></label>
          </div>
          <label>Admin Reason / Notes<textarea name="manual_reason" rows="3">{{ payroll_review.manual_reason }}</textarea></label>
          <button class="btn ghost" name="action" value="save_manual_hours" {% if payroll_review.locked %}disabled{% endif %}>Save Manual Hours</button>
        </div>
        {% endif %}
        <div class="actions">
          <button class="btn" name="action" value="approve" {% if payroll_review.locked or payroll_review.approval_blocked %}disabled{% endif %}>Approve Hours</button>
          <button class="btn ghost" name="action" value="edit" {% if payroll_review.locked %}disabled{% endif %}>Edit Hours</button>
          <button class="btn ghost" name="action" value="flag" {% if payroll_review.locked %}disabled{% endif %}>Flag Issue</button>
          <button class="btn ghost" name="action" value="send_back" {% if payroll_review.locked %}disabled{% endif %}>Send Back</button>
        </div>
      </form>
    </section>
    {% endif %}
{% endblock %}'''

PROFILE_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <div class="grid two-col">
      <div class="card">
        <h3>Profile Details</h3>
        {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
        {% if error %}<div class="alert error">{{ error }}</div>{% endif %}
        <form method="post" action="/profile/update" class="stack">
          <label>Full Name<input type="text" name="full_name" value="{{ user.full_name }}"></label>
          <div class="row-2"><label>Phone<input type="text" name="phone" value="{{ user.phone or '' }}"></label><label>Email<input type="email" name="email" value="{{ user.email or '' }}"></label></div>
          <label>License Number<input type="text" name="license_number" value="{{ user.license_number or '' }}"></label>
          <button class="btn primary">Save Profile</button>
        </form>
      </div>
      <div class="card">
        <h3>Change Password</h3>
        <form method="post" action="/profile/password" class="stack">
          <label>Current Password<input type="password" name="current_password"></label>
          <label>New Password<input type="password" name="new_password"></label>
          <button class="btn">Update Password</button>
        </form>
      </div>
    </div>
    <div class="grid two-col">
      <div class="card">
        <h3>Upcoming Assigned Shifts</h3>
        {% for shift in upcoming_shifts %}<div class="list-item detailed"><div><strong>{{ shift.site_name }}</strong><div class="small-muted">{{ shift.shift_date }} · {{ shift.start_time }}-{{ shift.end_time }}</div></div><span class="badge {{ shift.status }}">{{ shift.status }}</span></div>{% else %}<div class="empty">No upcoming shifts.</div>{% endfor %}
      </div>
      <div class="card">
        <h3>Guard Availability</h3>
        <form method="post" action="/availability/save" class="stack compact">
        {% set days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] %}
        {% for row in availability %}
          <div class="availability-row">
            <label class="checkbox-inline"><input type="checkbox" name="available_{{ row.weekday }}" {% if row.is_available %}checked{% endif %}> {{ days[row.weekday] }}</label>
            <input type="time" name="start_{{ row.weekday }}" value="{{ row.available_start }}">
            <input type="time" name="end_{{ row.weekday }}" value="{{ row.available_end }}">
          </div>
        {% endfor %}
        <button class="btn primary">Save Availability</button>
        </form>
      </div>
    </div>
{% endblock %}'''

ADMIN_PAYSTUB_UPLOAD_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <section class="grid two-col">
      <div class="card">
        <div class="section-head"><h3>Upload Guard Paystub</h3><span>PDF only</span></div>
        <form method="post" action="/admin/paystubs/upload" enctype="multipart/form-data" class="stack">
          {{ csrf_input|safe }}
          <label>Guard
            <select name="guard_id" required>
              <option value="">Select guard</option>
              {% for guard in guards %}
              <option value="{{ guard.id }}">{{ guard.full_name }}</option>
              {% endfor %}
            </select>
          </label>
          <div class="row-2">
            <label>Pay Period Start<input type="date" name="pay_period_start"></label>
            <label>Pay Period End<input type="date" name="pay_period_end"></label>
          </div>
          <label>Paystub PDF<input type="file" name="paystub_file" accept="application/pdf,.pdf" required></label>
          <button class="btn primary" type="submit">Upload Paystub</button>
        </form>
      </div>
      <div class="card">
        <div class="section-head"><h3>Recent Uploads</h3><span>{{ recent_paystubs|length }} entries</span></div>
        {% for row in recent_paystubs %}
        <div class="list-item detailed">
          <div>
            <strong>{{ row.guard_name }}</strong>
            <div class="small-muted">
              {% if row.pay_period_start and row.pay_period_end %}
                {{ row.pay_period_start }} to {{ row.pay_period_end }}
              {% elif row.pay_period_start %}
                Starting {{ row.pay_period_start }}
              {% elif row.pay_period_end %}
                Ending {{ row.pay_period_end }}
              {% else %}
                Pay period not provided
              {% endif %}
            </div>
            <div class="small-muted">Uploaded {{ row.created_at }}</div>
          </div>
          <a class="btn ghost" href="/paystubs/{{ row.id }}/file" target="_blank" rel="noopener">View PDF</a>
        </div>
        {% else %}
        <div class="empty">No paystubs uploaded yet.</div>
        {% endfor %}
      </div>
    </section>
{% endblock %}'''

GUARD_PAYSTUBS_HTML = r'''{% extends "app_shell.html" %}
{% block page_content %}
    <section class="card">
      <div class="section-head"><h3>My Paystubs</h3><span>{{ paystubs|length }} available</span></div>
      {% for row in paystubs %}
      <div class="list-item detailed">
        <div>
          <strong>
            {% if row.pay_period_start and row.pay_period_end %}
              {{ row.pay_period_start }} to {{ row.pay_period_end }}
            {% elif row.pay_period_start %}
              Starting {{ row.pay_period_start }}
            {% elif row.pay_period_end %}
              Ending {{ row.pay_period_end }}
            {% else %}
              Pay period not provided
            {% endif %}
          </strong>
          <div class="small-muted">Uploaded {{ row.created_at }}</div>
        </div>
        <a class="btn" href="/paystubs/{{ row.id }}/file" target="_blank" rel="noopener">View / Download</a>
      </div>
      {% else %}
      <div class="empty">No paystubs available yet.</div>
      {% endfor %}
    </section>
{% endblock %}'''

STYLES_CSS = r'''
:root {
  --bg: #050505;
  --panel: #111111;
  --panel-2: #1a1a1a;
  --muted: #b8b8b8;
  --text: #ffffff;
  --line: rgba(192,192,192,0.18);
  --accent: #dc2626;
  --accent-2: #f87171;
  --success: #22c55e;
  --warn: #f59e0b;
  --danger: #ef4444;
  --shadow: 0 16px 40px rgba(0,0,0,0.28);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, Arial, sans-serif; background: radial-gradient(circle at top, #171717 0%, #050505 52%, #000 100%); color: var(--text); }
a { color: inherit; text-decoration: none; }
img { max-width: 100%; }
.login-shell, .simple-shell { min-height: 100vh; padding: 24px; }
.login-card { max-width: 1100px; margin: 0 auto; display: grid; grid-template-columns: 1.1fr .9fr; background: rgba(5,5,5,.86); border: 1px solid var(--line); border-radius: 28px; overflow: hidden; box-shadow: var(--shadow); }
.brand-panel { padding: 44px; background: linear-gradient(145deg, rgba(185,28,28,.24), rgba(192,192,192,.06)); display: flex; flex-direction: column; gap: 18px; }
.form-panel { padding: 40px; background: rgba(10,10,10,.94); }
.logo-box { width: 78px; height: 78px; display: grid; place-items: center; font-size: 34px; font-weight: 800; border-radius: 22px; background: linear-gradient(145deg, var(--accent), var(--accent-2)); color: #ffffff; }
.logo-box.small { width: 54px; height: 54px; font-size: 24px; border-radius: 16px; }
.shield-logo { clip-path: polygon(50% 0, 88% 13%, 82% 72%, 50% 100%, 18% 72%, 12% 13%); border-radius: 0; box-shadow: inset 0 0 0 2px rgba(255,255,255,.24), 0 16px 32px rgba(220,38,38,.24); }
.logo-placeholder { padding: 18px; border: 1px dashed rgba(255,255,255,.18); border-radius: 18px; text-align: center; margin-bottom: 18px; background: rgba(255,255,255,.02); }
.logo-mark { width: 58px; height: 58px; display: grid; place-items: center; margin: 0 auto 8px; font-size: 26px; font-weight: 800; background: linear-gradient(145deg, var(--accent), var(--accent-2)); color: #ffffff; }
.eyebrow { text-transform: uppercase; letter-spacing: .16em; font-size: 11px; color: var(--accent-2); }
.tagline, .small-muted { color: var(--muted); }
.brand-subtitle { margin: 8px 0 0; color: #e5e5e5; font-size: 13px; font-weight: 700; letter-spacing: .02em; }
.brand-subtitle.compact { margin-top: 6px; color: var(--accent-2); }
.hero-copy { max-width: 480px; line-height: 1.6; color: #f5f5f5; }
.feature-pills span { display: inline-block; margin: 0 8px 8px 0; padding: 8px 12px; border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,.04); }
.demo-box, .alert { margin-top: 16px; padding: 14px; border-radius: 16px; }
.demo-box { background: rgba(255,255,255,.04); border: 1px solid var(--line); color: #f5f5f5; }
.alert.error { background: rgba(239,68,68,.15); border: 1px solid rgba(239,68,68,.35); }
.alert.success { background: rgba(34,197,94,.14); border: 1px solid rgba(34,197,94,.28); }
.patrol-alert { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 18px; border-radius: 20px; box-shadow: var(--shadow); border: 1px solid var(--line); }
.patrol-alert-danger { background: linear-gradient(135deg, rgba(127,29,29,.86), rgba(24,24,27,.96)); border-color: rgba(248,113,113,.58); }
.patrol-alert-warning { background: linear-gradient(135deg, rgba(120,53,15,.84), rgba(24,24,27,.96)); border-color: rgba(251,191,36,.56); }
.issue-badge { display: inline-flex; align-items: center; justify-content: center; min-width: 24px; height: 24px; margin-left: 8px; padding: 0 8px; border-radius: 999px; font-size: 12px; font-weight: 800; color: #fff; }
.issue-badge.danger { background: var(--danger); box-shadow: 0 0 0 3px rgba(239,68,68,.16); }
.issue-badge.warning { background: var(--warn); color: #111; box-shadow: 0 0 0 3px rgba(245,158,11,.16); }
.stack { display: grid; gap: 14px; }
.stack.compact { gap: 10px; }
label { display: grid; gap: 7px; font-size: 14px; color: #f5f5f5; }
input, select, textarea, button { font: inherit; }
input, select, textarea { width: 100%; padding: 12px 14px; border-radius: 14px; border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.04); color: var(--text); }
button, .btn { display: inline-flex; justify-content: center; align-items: center; gap: 8px; padding: 11px 16px; border-radius: 14px; border: 1px solid rgba(255,255,255,.1); background: rgba(255,255,255,.04); color: var(--text); cursor: pointer; }
.btn.primary { background: linear-gradient(145deg, #ef4444, #991b1b); border-color: rgba(255,255,255,.06); color: white; }
.btn.ghost { background: transparent; }
.app-shell { min-height: 100vh; display: grid; grid-template-columns: 280px 1fr; }
.sidebar { border-right: 1px solid var(--line); padding: 24px; background: linear-gradient(180deg, rgba(5,5,5,.96), rgba(12,12,12,.94)); position: sticky; top: 0; height: 100vh; }
.sidebar-brand { display: flex; gap: 14px; align-items: flex-start; margin-bottom: 26px; }
.sidebar-brand-copy { min-width: 0; padding-top: 3px; }
.sidebar-brand-copy h2 { margin: 3px 0 4px; font-size: 18px; line-height: 1.15; overflow-wrap: anywhere; }
.company-logo { width: 56px; height: 70px; flex: 0 0 56px; object-fit: contain; border-radius: 14px; padding: 4px; border: 1px solid rgba(192,192,192,.24); background: rgba(0,0,0,.28); }
.company-logo.topbar-logo { width: 38px; height: 48px; flex-basis: 38px; border-radius: 10px; }
.company-logo-auth { object-fit: contain; border-radius: 16px; padding: 6px; border: 1px solid rgba(192,192,192,.24); background: rgba(0,0,0,.24); }
.logo-management-grid { display: grid; grid-template-columns: 220px 1fr; gap: 20px; align-items: start; }
.logo-preview-card { padding: 16px; border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,.03); text-align: center; }
.company-logo-preview { width: 160px; height: 160px; object-fit: contain; margin: 12px auto; border-radius: 18px; padding: 8px; border: 1px solid rgba(192,192,192,.24); background: rgba(0,0,0,.28); }
.brand-shield { display: block; flex: 0 0 auto; object-fit: contain; filter: drop-shadow(0 16px 32px rgba(220,38,38,.24)); }
.brand-shield-large { width: 82px; height: 102px; }
.brand-shield-form { width: 74px; height: 92px; margin: 0 auto; }
.brand-shield-sidebar { width: 56px; height: 70px; flex-basis: 56px; }
.brand-shield-topbar { width: 38px; height: 48px; }
.nav-links { display: grid; gap: 8px; }
.nav-links a { padding: 12px 14px; border-radius: 14px; color: #f5f5f5; }
.nav-links a:hover { background: rgba(255,255,255,.05); }
.nav-links a.active { background: rgba(220,38,38,.22); color: #fff; box-shadow: inset 0 0 0 1px rgba(248,113,113,.45); }
.content { padding: 20px; display: grid; gap: 14px; }
.card { background: linear-gradient(180deg, rgba(20,20,20,.96), rgba(8,8,8,.96)); border: 1px solid var(--line); border-radius: 20px; padding: 16px; box-shadow: var(--shadow); }
.topbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.user-chip { padding: 10px 14px; border: 1px solid var(--line); border-radius: 999px; color: #f5f5f5; }
.grid { display: grid; gap: 12px; }
.stats-grid { grid-template-columns: repeat(4, 1fr); }
.two-col { grid-template-columns: repeat(2, 1fr); }
.stat-number { font-size: 34px; font-weight: 800; margin-top: 6px; }
.stat-label { color: var(--muted); }
.stat-text { font-size: 16px; font-weight: 700; margin-top: 8px; line-height: 1.35; }
.guard-stat-highlight { border-color: rgba(220,38,38,.55); background: linear-gradient(145deg, rgba(185,28,28,.28), rgba(12,12,12,.96)); }
.compact-card .list-item { padding: 10px 0; }
.section-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 14px; }
.table-wrap { overflow: auto; border-radius: 16px; border: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; min-width: 640px; }
th, td { padding: 12px 14px; border-bottom: 1px solid var(--line); text-align: left; }
th { color: var(--muted); font-size: 13px; font-weight: 600; }
.list-item { display: flex; justify-content: space-between; gap: 16px; padding: 12px 0; border-bottom: 1px solid rgba(255,255,255,.06); }
.list-item.detailed { align-items: center; }
.list-item:last-child { border-bottom: 0; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.badge { display: inline-block; padding: 6px 10px; border-radius: 999px; font-size: 12px; text-transform: capitalize; border: 1px solid rgba(255,255,255,.12); }
.badge.open, .badge.assigned, .badge.clocked_in { background: rgba(220,38,38,.14); }
.badge.pending, .badge.medium { background: rgba(245,158,11,.16); }
.badge.closed, .badge.completed, .badge.approved, .badge.low { background: rgba(34,197,94,.16); }
.badge.declined, .badge.denied, .badge.high { background: rgba(239,68,68,.16); }
.badge.conflict-leave { background: rgba(239,68,68,.24); border-color: rgba(239,68,68,.5); color: #fecaca; }
.badge.conflict-overlap { background: rgba(245,158,11,.22); border-color: rgba(245,158,11,.5); color: #fde68a; }
.report-card { padding: 14px; border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,.02); margin-bottom: 12px; }
.report-top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.report-photo { margin-top: 12px; border-radius: 16px; border: 1px solid var(--line); max-height: 220px; object-fit: cover; width: 100%; }
.row-2, .row-3 { display: grid; gap: 12px; }
.row-2 { grid-template-columns: repeat(2, 1fr); }
.row-3 { grid-template-columns: repeat(3, 1fr); }
hr { border: 0; border-top: 1px solid var(--line); margin: 18px 0; }
.empty { color: var(--muted); padding: 8px 0; }
.simple-header { display: flex; align-items: center; gap: 14px; max-width: 1200px; margin: 0 auto 18px; }
.availability-row { display: grid; grid-template-columns: 120px 1fr 1fr; gap: 10px; align-items: center; }
.checkbox-inline { display: flex; align-items: center; gap: 8px; }
.checkbox-inline input { width: auto; }
.checkbox-row label { margin-top: 4px; }
.checkbox-row .checkbox-inline span { line-height: 1.2; }
.report-details { border-bottom: 1px solid rgba(255,255,255,.06); padding: 8px 0; }
.report-details summary { list-style: none; cursor: pointer; }
.report-details summary::-webkit-details-marker { display: none; }
.sticky-submit-wrap { position: sticky; bottom: 8px; padding-top: 4px; background: linear-gradient(180deg, rgba(8,8,8,0), rgba(8,8,8,.96) 36%); }
.sticky-submit { width: 100%; }
.slim-gap { margin-top: 16px; }

.guard-action-dashboard { border-color: rgba(248,113,113,.38); }
.guard-action-dashboard h2 { margin: 4px 0 6px; font-size: clamp(1.45rem, 5vw, 2.1rem); }
.guard-action-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.guard-action-form { display: block; }
.guard-action-btn { width: 100%; min-height: 112px; padding: 20px; border-radius: 22px; border: 1px solid rgba(255,255,255,.14); background: rgba(255,255,255,.05); color: var(--text); display: flex; flex-direction: column; justify-content: center; align-items: flex-start; gap: 8px; text-align: left; box-shadow: 0 14px 34px rgba(0,0,0,.22); }
.guard-action-btn span { font-size: clamp(1.2rem, 4.8vw, 1.65rem); font-weight: 850; line-height: 1.1; }
.guard-action-btn small { color: var(--muted); font-weight: 650; }
.guard-action-btn.primary { background: linear-gradient(145deg, #ef4444, #991b1b); border-color: rgba(255,255,255,.18); }
.guard-action-btn.primary small { color: rgba(255,255,255,.86); }
.guard-action-btn:disabled { opacity: .62; cursor: not-allowed; }
@media (max-width: 720px) { .guard-action-grid { grid-template-columns: 1fr; } .guard-action-btn { min-height: 96px; } }

@media (max-width: 1040px) {
  .login-card, .app-shell, .stats-grid, .two-col, .row-2, .row-3 { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; }
  .topbar, .section-head, .list-item, .simple-header, .patrol-alert { flex-direction: column; align-items: flex-start; }
  .availability-row { grid-template-columns: 1fr; }
  .content { padding: 14px; gap: 10px; }
  .card { padding: 14px; border-radius: 16px; }
  .stat-number { font-size: 28px; }
  .stat-text { font-size: 15px; }
}
'''



# === Beta launch hardening overrides ===
import argparse
import json
import re as _re
import socket
import threading
import traceback
try:
    import boto3
except Exception:
    boto3 = None

CSRF_COOKIE_NAME = os.getenv('CSRF_COOKIE_NAME', 'steeleops_csrf')
STORAGE_BACKEND = os.getenv('STORAGE_BACKEND', 'local').lower()
S3_BUCKET = os.getenv('S3_BUCKET', '').strip()
S3_REGION = os.getenv('S3_REGION', '').strip()
S3_ENDPOINT_URL = os.getenv('S3_ENDPOINT_URL', '').strip()
S3_ACCESS_KEY_ID = os.getenv('S3_ACCESS_KEY_ID', '').strip()
S3_SECRET_ACCESS_KEY = os.getenv('S3_SECRET_ACCESS_KEY', '').strip()
S3_PUBLIC_BASE_URL = os.getenv('S3_PUBLIC_BASE_URL', '').rstrip('/')
RESET_TOKEN_HOURS = int(os.getenv('RESET_TOKEN_HOURS', '2'))
ALLOW_BROWSER_PASSWORD_RESET_LINKS = os.getenv('ALLOW_BROWSER_PASSWORD_RESET_LINKS', '1' if APP_ENV != 'production' else '0') == '1'
APP_BASE_URL = os.getenv('APP_BASE_URL', '').rstrip('/')
MISSED_CLOCK_SCHEDULER_ENABLED = os.getenv('MISSED_CLOCK_SCHEDULER_ENABLED', '1') == '1'
MISSED_CLOCK_SCHEDULER_INTERVAL_SECONDS = int(os.getenv('MISSED_CLOCK_SCHEDULER_INTERVAL_SECONDS', '600'))
MISSED_CLOCK_SCHEDULER_STALE_SECONDS = int(
    os.getenv(
        'MISSED_CLOCK_SCHEDULER_STALE_SECONDS',
        str(max(MISSED_CLOCK_SCHEDULER_INTERVAL_SECONDS * 2, MISSED_CLOCK_SCHEDULER_INTERVAL_SECONDS + 300)),
    )
)
MISSED_CLOCK_SCHEDULER_LOCK_NAME = 'missed_clock_check_scheduler'
MISSED_CLOCK_SCHEDULER_STARTUP_DELAY_SECONDS = int(os.getenv('MISSED_CLOCK_SCHEDULER_STARTUP_DELAY_SECONDS', '15'))
SCHEDULER_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}"
_scheduler_start_lock = threading.Lock()
_missed_clock_scheduler_thread = None
_missed_clock_scheduler_stop = threading.Event()


def upload_relative_path(folder, filename):
    safe_folder = (folder or 'general').strip('/').replace('\\', '/')
    safe_file = os.path.basename(filename or '')
    return f"uploads/{safe_folder}/{safe_file}"



def public_asset_url(path):
    value = (path or '').strip()
    if not value:
        return ''
    if value.startswith(('http://', 'https://', '//')):
        return value
    return '/' + value.lstrip('/')


def first_uploaded_file(files, field_name):
    value = (files or {}).get(field_name)
    if not value:
        return None
    if isinstance(value, list):
        return next((item for item in value if item and item.get('filename')), None)
    return value if value.get('filename') else None


def default_company_branding():
    try:
        conn = db()
        row = conn.execute(
            '''
            SELECT name, logo_path
            FROM companies
            WHERE logo_path IS NOT NULL AND TRIM(logo_path) <> ''
            ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END, id
            LIMIT 1
            ''',
            (PROVIDER_BRAND_NAME,),
        ).fetchone()
        conn.close()
    except Exception:
        return {'name': PROVIDER_BRAND_NAME, 'logo_url': ''}
    if not row:
        return {'name': PROVIDER_BRAND_NAME, 'logo_url': ''}
    return {'name': row['name'] or PROVIDER_BRAND_NAME, 'logo_url': public_asset_url(row['logo_path'])}


def is_allowed_company_logo(file_info):
    if not file_info or not file_info.get('filename'):
        return False
    ext = os.path.splitext(os.path.basename(file_info['filename']))[1].lower()
    content = file_info.get('content', b'') or b''
    signatures = {
        '.png': lambda data: data.startswith(b'\x89PNG\r\n\x1a\n'),
        '.jpg': lambda data: data.startswith(b'\xff\xd8\xff'),
        '.jpeg': lambda data: data.startswith(b'\xff\xd8\xff'),
        '.gif': lambda data: data.startswith((b'GIF87a', b'GIF89a')),
        '.webp': lambda data: data.startswith(b'RIFF') and data[8:12] == b'WEBP',
        '.svg': lambda data: b'<svg' in data[:512].lower(),
    }
    checker = signatures.get(ext)
    return bool(checker and checker(content))


def parse_cookies(environ):
    cookie = environ.get('HTTP_COOKIE', '')
    cookies = {}
    for item in cookie.split(';'):
        if '=' in item:
            k, v = item.strip().split('=', 1)
            cookies[k] = v
    return cookies


def csrf_cookie_header(token, expires=None):
    parts = [f'{CSRF_COOKIE_NAME}={token}', 'Path=/', 'SameSite=Lax']
    if SESSION_COOKIE_SECURE:
        parts.append('Secure')
    if expires:
        parts.append('Expires=' + cookie_expires_gmt(expires))
    return '; '.join(parts)


def _signed_value(raw):
    sig = hmac.new(SECRET_KEY.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()
    return f'{raw}.{sig}'


def _unsign_value(value):
    if not value or '.' not in value:
        return None
    raw, sig = value.rsplit('.', 1)
    expected = hmac.new(SECRET_KEY.encode('utf-8'), raw.encode('utf-8'), hashlib.sha256).hexdigest()
    return raw if hmac.compare_digest(sig, expected) else None


def get_or_create_csrf_token(environ):
    if '_csrf_cached' in environ:
        return environ['_csrf_cached']
    existing = _unsign_value(parse_cookies(environ).get(CSRF_COOKIE_NAME, ''))
    if existing:
        environ['_csrf_cached'] = (existing, None)
        return environ['_csrf_cached']
    raw = secrets.token_urlsafe(24)
    header = ('Set-Cookie', csrf_cookie_header(_signed_value(raw)))
    environ['_csrf_cached'] = (raw, header)
    return environ['_csrf_cached']


def csrf_headers(environ):
    _, header = get_or_create_csrf_token(environ)
    return [header] if header else []


def csrf_hidden_input(environ):
    token, _ = get_or_create_csrf_token(environ)
    return f'<input type="hidden" name="csrf_token" value="{token}">'


def validate_csrf(environ, data):
    submitted = (data or {}).get('csrf_token', '')
    cookie_token = _unsign_value(parse_cookies(environ).get(CSRF_COOKIE_NAME, ''))
    return bool(submitted and cookie_token and hmac.compare_digest(submitted, cookie_token))


def client_ip(environ):
    forwarded = environ.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return environ.get('REMOTE_ADDR', '')


def log_audit(event_type, actor_user_id=None, company_id=None, target_type='', target_id=None, message='', environ=None, metadata=None):
    conn = db()
    if not table_exists(conn, 'audit_logs'):
        conn.close()
        return
    conn.execute(
        'INSERT INTO audit_logs (event_type, actor_user_id, company_id, target_type, target_id, message, ip_address, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (event_type, actor_user_id, company_id, target_type, str(target_id or ''), message, client_ip(environ) if environ else '', json.dumps(metadata or {}, default=str), now_utc().strftime('%Y-%m-%d %H:%M:%S')),
    )
    conn.commit(); conn.close()


def record_login_attempt(username, success, environ=None, user_id=None, company_id=None):
    conn = db()
    conn.execute('INSERT INTO auth_attempts (username, success, attempted_at) VALUES (?, ?, ?)', (username, 1 if success else 0, now_utc().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit(); conn.close()
    log_audit('login_attempt', actor_user_id=user_id, company_id=company_id, target_type='auth', target_id=username, message='success' if success else 'failed', environ=environ, metadata={'username': username, 'success': bool(success)})


def parse_post(environ):
    cached = environ.get('_parsed_post')
    if cached:
        return cached
    content_type = environ.get('CONTENT_TYPE', '')
    if content_type.startswith('multipart/form-data'):
        parsed = parse_multipart(environ, content_type)
    else:
        try:
            size = int(environ.get('CONTENT_LENGTH', '0') or 0)
        except ValueError:
            size = 0
        raw = environ['wsgi.input'].read(size).decode('utf-8')
        parsed = ({k: v[0] for k, v in parse_qs(raw).items()}, {})
    environ['_parsed_post'] = parsed
    return parsed


class _LocalStorageBackend:
    def save(self, file_info, folder='general'):
        original = os.path.basename(file_info['filename'])
        ext = os.path.splitext(original)[1].lower()
        safe_dir = os.path.join(UPLOAD_DIR, folder)
        os.makedirs(safe_dir, exist_ok=True)
        safe_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}{ext}"
        dest = os.path.join(safe_dir, safe_name)
        with open(dest, 'wb') as f:
            f.write(file_info['content'])
        return original, upload_relative_path(folder, safe_name)


class _S3StorageBackend:
    def __init__(self):
        if boto3 is None:
            raise RuntimeError('boto3 is required for STORAGE_BACKEND=s3')
        kwargs = {}
        if S3_REGION:
            kwargs['region_name'] = S3_REGION
        if S3_ENDPOINT_URL:
            kwargs['endpoint_url'] = S3_ENDPOINT_URL
        if S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY:
            kwargs['aws_access_key_id'] = S3_ACCESS_KEY_ID
            kwargs['aws_secret_access_key'] = S3_SECRET_ACCESS_KEY
        self.client = boto3.client('s3', **kwargs)

    def save(self, file_info, folder='general'):
        if not S3_BUCKET:
            raise RuntimeError('S3_BUCKET is required for STORAGE_BACKEND=s3')
        original = os.path.basename(file_info['filename'])
        ext = os.path.splitext(original)[1].lower()
        key = f"steeleops/{folder}/{datetime.now().strftime('%Y/%m/%d')}/{secrets.token_urlsafe(10)}{ext}"
        content_type = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.pdf': 'application/pdf'}.get(ext, 'application/octet-stream')
        self.client.put_object(Bucket=S3_BUCKET, Key=key, Body=file_info['content'], ContentType=content_type)
        if S3_PUBLIC_BASE_URL:
            return original, f"{S3_PUBLIC_BASE_URL}/{key}"
        if S3_ENDPOINT_URL:
            return original, f"{S3_ENDPOINT_URL.rstrip('/')}/{S3_BUCKET}/{key}"
        region = S3_REGION or 'us-east-1'
        return original, f"https://{S3_BUCKET}.s3.{region}.amazonaws.com/{key}"


def save_upload(file_info, folder='general'):
    if isinstance(file_info, list):
        file_info = next((item for item in file_info if item and item.get('filename')), None)
    if not file_info or not file_info.get('filename'):
        return None, None
    if len(file_info.get('content', b'')) > MAX_UPLOAD_MB * 1024 * 1024:
        return None, None
    backend = _S3StorageBackend() if STORAGE_BACKEND == 's3' else _LocalStorageBackend()
    return backend.save(file_info, folder)


def is_pdf_upload(file_info):
    if not file_info or not file_info.get('filename'):
        return False
    ext = os.path.splitext(os.path.basename(file_info['filename']))[1].lower()
    content = file_info.get('content', b'') or b''
    return ext == '.pdf' and content.startswith(b'%PDF-')


def _safe_join(base_path, relative_path):
    normalized_base = os.path.normpath(base_path)
    candidate = os.path.normpath(os.path.join(normalized_base, relative_path))
    if candidate == normalized_base or candidate.startswith(normalized_base + os.sep):
        return candidate
    return None


def local_path_from_upload(upload_path):
    normalized = (upload_path or '').strip().replace('\\', '/')
    if not normalized:
        return None
    normalized = normalized.lstrip('/')
    if normalized.startswith('uploads/'):
        normalized = normalized[len('uploads/'):]
    return _safe_join(UPLOAD_DIR, normalized)


def upload_exists(upload_path):
    local_file_path = local_path_from_upload(upload_path)
    return bool(local_file_path and os.path.isfile(local_file_path))


def upload_content_type(upload_path):
    ext = os.path.splitext((upload_path or '').lower())[1]
    return {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.pdf': 'application/pdf',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.txt': 'text/plain; charset=utf-8',
    }.get(ext, 'application/octet-stream')


def attachment_meta(path_value):
    if not path_value:
        return None
    file_name = os.path.basename(path_value.split('?', 1)[0].rstrip('/'))
    content_type = upload_content_type(path_value)
    return {
        'path': path_value,
        'name': file_name or 'attachment',
        'is_image': content_type.startswith('image/'),
        'content_type': content_type,
        'is_available': upload_exists(path_value),
        'status': 'available' if upload_exists(path_value) else 'missing',
    }


def build_absolute_url(environ, path):
    if APP_BASE_URL:
        return APP_BASE_URL + path
    scheme = environ.get('HTTP_X_FORWARDED_PROTO') or environ.get('wsgi.url_scheme', 'http')
    host = environ.get('HTTP_HOST') or f'{HOST}:{PORT}'
    return f'{scheme}://{host}{path}'


def create_password_reset(user_id, environ):
    token = secrets.token_urlsafe(32)
    conn = db()
    conn.execute('DELETE FROM password_reset_tokens WHERE user_id=?', (user_id,))
    conn.execute('INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, used_at, created_at) VALUES (?, ?, ?, NULL, ?)', (user_id, hashlib.sha256(token.encode('utf-8')).hexdigest(), expires_at(RESET_TOKEN_HOURS), now_utc().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit(); conn.close()
    return token, build_absolute_url(environ, f'/password-reset/confirm?token={token}')


def get_password_reset_row(token):
    conn = db()
    row = conn.execute('SELECT prt.*, u.username, u.company_id FROM password_reset_tokens prt JOIN users u ON prt.user_id=u.id WHERE prt.token_hash=? AND prt.used_at IS NULL AND prt.expires_at >= ? ORDER BY prt.id DESC LIMIT 1', (hashlib.sha256(token.encode('utf-8')).hexdigest(), now_utc().strftime('%Y-%m-%d %H:%M:%S'))).fetchone()
    conn.close()
    return row


def consume_password_reset(token, new_password):
    conn = db()
    row = conn.execute('SELECT * FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at >= ? ORDER BY id DESC LIMIT 1', (hashlib.sha256(token.encode('utf-8')).hexdigest(), now_utc().strftime('%Y-%m-%d %H:%M:%S'))).fetchone()
    if not row:
        conn.close(); return False
    conn.execute('UPDATE users SET password=? WHERE id=?', (hash_password(new_password), row['user_id']))
    conn.execute('UPDATE password_reset_tokens SET used_at=? WHERE id=?', (now_utc().strftime('%Y-%m-%d %H:%M:%S'), row['id']))
    conn.commit(); conn.close(); return True



def company_selector_rows():
    conn = db()
    rows = conn.execute("""
        SELECT c.id, c.name, c.tagline, COUNT(*) AS active_guard_count
        FROM companies c
        LEFT JOIN guards g ON g.company_id=c.id AND g.status='active'
        GROUP BY c.id, c.name, c.tagline
        ORDER BY c.name
    """).fetchall()
    conn.close()
    return rows



def current_guard_assignment_join(guard_alias='g'):
    return f"""
        LEFT JOIN guard_site_assignments gsa ON gsa.id=(
            SELECT gsa_current.id
            FROM guard_site_assignments gsa_current
            JOIN sites site_current ON site_current.id=gsa_current.site_id AND site_current.company_id=gsa_current.company_id
            WHERE gsa_current.guard_id={guard_alias}.id AND gsa_current.company_id={guard_alias}.company_id
            ORDER BY gsa_current.assigned_at DESC, gsa_current.id DESC
            LIMIT 1
        )
        LEFT JOIN sites s ON s.id=gsa.site_id AND s.company_id={guard_alias}.company_id
    """


def save_guard_site_assignment(conn, company_id, guard_id, site_id):
    normalized_site_id = (site_id or '').strip()
    conn.execute('DELETE FROM guard_site_assignments WHERE guard_id=? AND company_id=?', (guard_id, company_id))
    if not normalized_site_id:
        return None
    site = conn.execute('SELECT id FROM sites WHERE id=? AND company_id=? AND active=1', (normalized_site_id, company_id)).fetchone()
    if not site:
        raise ValueError('Assigned site not found')
    timestamp = utc_now_str()
    conn.execute(
        'INSERT INTO guard_site_assignments (company_id, guard_id, site_id, assigned_at) VALUES (?, ?, ?, ?)',
        (company_id, guard_id, normalized_site_id, timestamp)
    )
    return int(normalized_site_id)

def guard_login_identity_candidates(identity):
    cleaned = (identity or '').strip()
    if not cleaned:
        return []
    lowered = cleaned.lower()
    conn = db()
    user_cols = column_names(conn, 'users') if table_exists(conn, 'users') else set()
    guard_cols = column_names(conn, 'guards') if table_exists(conn, 'guards') else set()
    user_employee = "COALESCE(u.employee_id, '')" if 'employee_id' in user_cols else "''"
    user_badge = "COALESCE(u.badge_id, '')" if 'badge_id' in user_cols else "''"
    guard_employee = "COALESCE(g.employee_id, '')" if 'employee_id' in guard_cols else "''"
    guard_badge = "COALESCE(g.badge_id, '')" if 'badge_id' in guard_cols else "''"
    guard_full_name = "COALESCE(NULLIF(TRIM(g.name), ''), NULLIF(TRIM(g.first_name || ' ' || g.last_name), ''), '')" if 'name' in guard_cols else "COALESCE(NULLIF(TRIM(g.first_name || ' ' || g.last_name), ''), '')"
    rows = conn.execute(f"""
        SELECT g.*, g.id AS guard_profile_id, u.id AS user_id, u.username, u.password, u.pin_hash, u.active AS user_active,
               u.email AS login_email, u.license_number AS login_license_number, c.name AS company_name,
               c.tagline AS company_tagline, c.logo_path AS company_logo
        FROM users u
        JOIN guards g ON g.id=u.guard_id AND g.company_id=u.company_id
        JOIN companies c ON c.id=u.company_id
        WHERE u.role='guard' AND u.active=1 AND g.status='active'
          AND (
            LOWER(u.username)=? OR LOWER(COALESCE(u.full_name, ''))=? OR LOWER({guard_full_name})=?
            OR {user_employee}=? OR {guard_employee}=?
            OR {user_badge}=? OR {guard_badge}=?
            OR CAST(u.id AS TEXT)=? OR CAST(g.id AS TEXT)=? OR CAST(u.guard_id AS TEXT)=?
          )
        ORDER BY u.id
        LIMIT 20
    """, (lowered, lowered, lowered, cleaned, cleaned, cleaned, cleaned, cleaned, cleaned, cleaned)).fetchall()
    conn.close()
    return rows

def guard_login_identity_record(identity, pin):
    matches = []
    for guard in guard_login_identity_candidates(identity):
        if guard['pin_hash'] and is_valid_pin(pin) and verify_password(pin, guard['pin_hash']):
            matches.append(guard)
    return matches[0] if len(matches) == 1 else None

def guard_login_assigned_sites(company_id, guard_id):
    conn = db()
    rows = conn.execute("""
        SELECT s.id, s.name, s.address, COALESCE(NULLIF(c.name, ''), NULLIF(s.client_company_name, ''), '') AS client_name
        FROM guard_site_assignments gsa
        JOIN sites s ON s.id=gsa.site_id AND s.company_id=gsa.company_id
        LEFT JOIN clients c ON c.id=s.client_id AND c.company_id=s.company_id
        WHERE gsa.company_id=? AND gsa.guard_id=? AND COALESCE(s.active,1)=1
        ORDER BY gsa.assigned_at DESC, gsa.id DESC, s.name
    """, (company_id, guard_id)).fetchall()
    conn.close()
    return rows


def guard_login_remembered_identity(environ):
    cookies = parse_request_cookies(environ)
    return unquote_plus(cookies.get('guard_quick_identity', '')).strip()


def guard_login_identity_cookie(identity, remember):
    if not remember or not identity:
        return None
    expires = now_utc() + timedelta(days=30)
    value = quote_plus(identity.strip())
    parts = [f'guard_quick_identity={value}', 'Path=/guard-login', 'SameSite=Lax', 'Expires=' + cookie_expires_gmt(expires)]
    if SESSION_COOKIE_SECURE:
        parts.append('Secure')
    return '; '.join(parts)


def delete_guard_login_identity_cookie():
    return 'guard_quick_identity=deleted; Path=/guard-login; SameSite=Lax; Expires=' + cookie_expires_gmt(datetime(1970, 1, 1, tzinfo=timezone.utc))

def guard_site_debug_payload(company_id, guard_id, selected_site_id=None):
    conn = db()
    guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (guard_id, company_id)).fetchone()
    users = conn.execute("""
        SELECT *
        FROM users
        WHERE company_id=? AND role='guard' AND guard_id=?
        ORDER BY id
    """, (company_id, guard_id)).fetchall()
    assignments = conn.execute("""
        SELECT gsa.*, s.name AS site_name, s.active AS site_active
        FROM guard_site_assignments gsa
        LEFT JOIN sites s ON s.id=gsa.site_id AND s.company_id=gsa.company_id
        WHERE gsa.company_id=? AND gsa.guard_id=?
        ORDER BY gsa.assigned_at DESC, gsa.id DESC
    """, (company_id, guard_id)).fetchall()
    latest_assignment = conn.execute("""
        SELECT gsa.*, s.name AS site_name, s.active AS site_active
        FROM guard_site_assignments gsa
        LEFT JOIN sites s ON s.id=gsa.site_id AND s.company_id=gsa.company_id
        WHERE gsa.id=(
            SELECT gsa_current.id
            FROM guard_site_assignments gsa_current
            JOIN sites site_current ON site_current.id=gsa_current.site_id AND site_current.company_id=gsa_current.company_id
            WHERE gsa_current.guard_id=? AND gsa_current.company_id=?
            ORDER BY gsa_current.assigned_at DESC, gsa_current.id DESC
            LIMIT 1
        )
    """, (guard_id, company_id)).fetchone()
    selected_site = None
    if selected_site_id:
        selected_site = conn.execute('SELECT * FROM sites WHERE id=? AND company_id=?', (selected_site_id, company_id)).fetchone()
    linked_user = users[0] if users else None
    should_appear = bool(
        guard
        and guard['status'] == 'active'
        and linked_user
        and linked_user['active']
        and latest_assignment
        and (selected_site_id is None or latest_assignment['site_id'] == selected_site_id)
    )
    payload = {
        'selected_company_id': company_id,
        'selected_site_id': selected_site_id,
        'selected_site': dict(selected_site) if selected_site else None,
        'guard': dict(guard) if guard else None,
        'linked_user': dict(linked_user) if linked_user else None,
        'all_linked_users': [dict(row) for row in users],
        'assignment_rows': [dict(row) for row in assignments],
        'latest_assignment_row': dict(latest_assignment) if latest_assignment else None,
        'quick_login': {
            'should_appear': should_appear,
            'checks': {
                'guard_found': bool(guard),
                'guard_active': bool(guard and guard['status'] == 'active'),
                'linked_user_found': bool(linked_user),
                'linked_user_has_guard_id': bool(linked_user and linked_user['guard_id'] == guard_id),
                'linked_user_active': bool(linked_user and linked_user['active']),
                'latest_assignment_found': bool(latest_assignment),
                'latest_assignment_matches_selected_site': bool(
                    latest_assignment and (selected_site_id is None or latest_assignment['site_id'] == selected_site_id)
                ),
            },
        },
    }
    conn.close()
    return payload

def forbidden(start_response, message='Forbidden'):
    start_response('403 Forbidden', response_headers(content_type='text/plain; charset=utf-8'))
    return [message.encode('utf-8')]


def require_internal_token(environ):
    provided = (environ.get('HTTP_X_INTERNAL_TOKEN') or '').strip()
    if not MISSED_CLOCK_INTERNAL_TOKEN:
        return False, 'Internal token is not configured.'
    if not provided:
        return False, 'Missing internal token.'
    if not hmac.compare_digest(provided, MISSED_CLOCK_INTERNAL_TOKEN):
        return False, 'Invalid internal token.'
    return True, None


def requested_company_ids(environ):
    query = parse_query(environ)
    company_id = (query.get('company_id') or query.get('company') or '').strip()
    conn = db()
    try:
        if company_id:
            if not company_id.isdigit():
                raise ValueError('company_id must be a numeric value.')
            row = conn.execute('SELECT id FROM companies WHERE id=? LIMIT 1', (int(company_id),)).fetchone()
            return [row['id']] if row else []
        rows = conn.execute('SELECT id FROM companies ORDER BY id').fetchall()
        return [row['id'] for row in rows]
    finally:
        conn.close()


def run_missed_clock_check_for_companies(company_ids, environ=None):
    totals = {
        'companies_processed': 0,
        'created_count': 0,
        'sent_count': 0,
        'skipped_count': 0,
    }
    for company_id in company_ids:
        result = run_missed_clock_check(company_id, actor_user_id=None, environ=environ)
        totals['companies_processed'] += 1
        totals['created_count'] += result['created_count']
        totals['sent_count'] += result['sent_count']
        totals['skipped_count'] += result['skipped_count']
    log_audit(
        'missed_clock_check_scheduled_run',
        company_id=company_ids[0] if len(company_ids) == 1 else None,
        target_type='system',
        target_id='all_companies' if len(company_ids) != 1 else company_ids[0],
        message='scheduled missed clock check completed',
        environ=environ,
        metadata=totals,
    )
    print(
        '[missed_clock_check_scheduled_run] ' +
        f"companies_processed={totals['companies_processed']} created_count={totals['created_count']} " +
        f"sent_count={totals['sent_count']} skipped_count={totals['skipped_count']}",
        flush=True,
    )
    return totals


def utc_now_string():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def scheduler_environ():
    return {
        'REMOTE_ADDR': '127.0.0.1',
        'HTTP_USER_AGENT': f'SteeleOpsScheduler/{SCHEDULER_INSTANCE_ID}',
    }


def acquire_scheduler_leadership(lock_name=MISSED_CLOCK_SCHEDULER_LOCK_NAME):
    conn = db()
    now_str = utc_now_string()
    stale_before = (datetime.now(timezone.utc) - timedelta(seconds=MISSED_CLOCK_SCHEDULER_STALE_SECONDS)).strftime('%Y-%m-%d %H:%M:%S')
    try:
        existing = conn.execute(
            'SELECT owner_id, heartbeat_at FROM app_runtime_locks WHERE lock_name=?',
            (lock_name,),
        ).fetchone() if table_exists(conn, 'app_runtime_locks') else None
        if not existing:
            conn.execute(
                'INSERT INTO app_runtime_locks (lock_name, owner_id, acquired_at, heartbeat_at) VALUES (?, ?, ?, ?)',
                (lock_name, SCHEDULER_INSTANCE_ID, now_str, now_str),
            )
            conn.commit()
            return True
        owner_id = (existing['owner_id'] or '').strip()
        heartbeat_at = (existing['heartbeat_at'] or '').strip()
        if owner_id == SCHEDULER_INSTANCE_ID or not heartbeat_at or heartbeat_at <= stale_before:
            conn.execute(
                'UPDATE app_runtime_locks SET owner_id=?, heartbeat_at=?, acquired_at=CASE WHEN owner_id=? THEN acquired_at ELSE ? END WHERE lock_name=?',
                (SCHEDULER_INSTANCE_ID, now_str, SCHEDULER_INSTANCE_ID, now_str, lock_name),
            )
            conn.commit()
            return True
        conn.rollback()
        return False
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_missed_clock_scheduler_cycle():
    if not acquire_scheduler_leadership():
        print(
            f'[missed_clock_scheduler] skipped instance={SCHEDULER_INSTANCE_ID} reason=leader-held-by-another-process',
            flush=True,
        )
        return None
    company_ids = requested_company_ids({})
    totals = run_missed_clock_check_for_companies(company_ids, environ=scheduler_environ())
    print(
        '[missed_clock_scheduler] ' +
        f"instance={SCHEDULER_INSTANCE_ID} companies_processed={totals['companies_processed']} " +
        f"created_count={totals['created_count']} sent_count={totals['sent_count']} " +
        f"skipped_count={totals['skipped_count']}",
        flush=True,
    )
    return totals


def missed_clock_scheduler_loop():
    print(
        '[missed_clock_scheduler] ' +
        f'background thread started instance={SCHEDULER_INSTANCE_ID} interval_seconds={MISSED_CLOCK_SCHEDULER_INTERVAL_SECONDS} startup_delay_seconds={MISSED_CLOCK_SCHEDULER_STARTUP_DELAY_SECONDS}',
        flush=True,
    )
    if MISSED_CLOCK_SCHEDULER_STARTUP_DELAY_SECONDS > 0 and _missed_clock_scheduler_stop.wait(MISSED_CLOCK_SCHEDULER_STARTUP_DELAY_SECONDS):
        return
    while not _missed_clock_scheduler_stop.is_set():
        try:
            run_missed_clock_scheduler_cycle()
        except Exception as exc:
            print(f'[missed_clock_scheduler] error instance={SCHEDULER_INSTANCE_ID} detail={exc}', flush=True)
            traceback.print_exc()
        if _missed_clock_scheduler_stop.wait(MISSED_CLOCK_SCHEDULER_INTERVAL_SECONDS):
            break


def start_missed_clock_scheduler_once():
    global _missed_clock_scheduler_thread
    if not MISSED_CLOCK_SCHEDULER_ENABLED or os.getenv('RUN_MISSED_CLOCK_SCHEDULER', '1').strip() != '1':
        return False
    with _scheduler_start_lock:
        if _missed_clock_scheduler_thread and _missed_clock_scheduler_thread.is_alive():
            return False
        _missed_clock_scheduler_thread = threading.Thread(
            target=missed_clock_scheduler_loop,
            name='missed-clock-scheduler',
            daemon=True,
        )
        _missed_clock_scheduler_thread.start()
        return True


def run_missed_clock_check(company_id, actor_user_id=None, environ=None):
    conn = db()
    now_dt = datetime.now()
    created_count = 0
    sent_count = 0
    skipped_count = 0
    alerts = []
    try:
        shifts = conn.execute("""
            SELECT shifts.*, users.full_name, sites.name as site_name
            FROM shifts
            LEFT JOIN users ON COALESCE(shifts.user_id, shifts.guard_id)=users.id
            JOIN sites ON shifts.site_id=sites.id
            WHERE shifts.company_id=? AND COALESCE(shifts.user_id, shifts.guard_id) IS NOT NULL
            ORDER BY shifts.shift_date, shifts.start_time
        """, (company_id,)).fetchall()
        for shift in shifts:
            alert_kind = None
            due_at = None
            clock_in_due, clock_out_due = missed_clock_alert_thresholds(shift['shift_date'], shift['start_time'], shift['end_time'])
            if not shift['clock_in_time'] and now_dt >= clock_in_due:
                alert_kind = 'missed_clock_in'
                due_at = clock_in_due
            elif shift['clock_in_time'] and not shift['clock_out_time'] and now_dt >= clock_out_due:
                alert_kind = 'missed_clock_out'
                due_at = clock_out_due
            if not alert_kind:
                skipped_count += 1
                continue
            existing = conn.execute(
                """
                SELECT id FROM audit_logs
                WHERE company_id=? AND event_type='missed_clock_check_alert'
                  AND target_type='shift' AND target_id=? AND message=?
                LIMIT 1
                """,
                (company_id, str(shift['id']), alert_kind),
            ).fetchone() if table_exists(conn, 'audit_logs') else None
            if existing:
                skipped_count += 1
                continue
            alert_message = (
                f"{alert_kind} for shift #{shift['id']} "
                f"({shift['full_name'] or 'Assigned guard'}) at {shift['site_name']} "
                f"due {due_at.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            alerts.append(alert_message)
            created_count += 1
            sent_count += 1
            log_audit(
                'missed_clock_check_alert',
                actor_user_id=actor_user_id,
                company_id=company_id,
                target_type='shift',
                target_id=str(shift['id']),
                message=alert_kind,
                environ=environ,
                metadata={'alert': alert_message},
            )
        return {
            'created_count': created_count,
            'sent_count': sent_count,
            'skipped_count': skipped_count,
            'alerts': alerts,
        }
    finally:
        conn.close()


def render_page(environ, template_name, **context):
    company_branding = default_company_branding()
    context.setdefault('csrf_input', csrf_hidden_input(environ))
    context.setdefault('show_demo_accounts', APP_ENV != 'production')
    context.setdefault('product_short_name', PRODUCT_SHORT_NAME)
    context.setdefault('product_full_name', PRODUCT_FULL_NAME)
    context.setdefault('provider_brand_name', PROVIDER_BRAND_NAME)
    context.setdefault('brand_subtitle', BRAND_SUBTITLE)
    context.setdefault('provider_logo_url', PROVIDER_SHIELD_LOGO_URL)
    context.setdefault('default_company_name', company_branding['name'])
    context.setdefault('default_company_logo_url', company_branding['logo_url'])
    return render(template_name, **context)


_old_init_db = init_db

def init_db():
    if APP_ENV == 'production' and not USE_POSTGRES:
        raise RuntimeError('Production requires PostgreSQL via DATABASE_URL.')
    _old_init_db()
    ensure_assets()
    conn = db()
    if conn.backend == 'postgres':
        conn.cursor().executescript('''
        CREATE TABLE IF NOT EXISTS daily_activity_reports (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            company_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            officer_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            photo_path TEXT,
            status TEXT NOT NULL DEFAULT 'Open',
            supervisor_notes TEXT,
            admin_notes TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(site_id) REFERENCES sites(id),
            FOREIGN KEY(officer_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS incident_reports (
            id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            company_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            officer_id INTEGER NOT NULL,
            incident_type TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Medium',
            narrative TEXT NOT NULL,
            persons_involved TEXT,
            witnesses TEXT,
            police_notified INTEGER NOT NULL DEFAULT 0,
            client_notified INTEGER NOT NULL DEFAULT 0,
            attachment_path TEXT,
            status TEXT NOT NULL DEFAULT 'Open',
            supervisor_notes TEXT,
            admin_notes TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(site_id) REFERENCES sites(id),
            FOREIGN KEY(officer_id) REFERENCES users(id)
        );
        ''')
    else:
        conn.cursor().executescript('''
        CREATE TABLE IF NOT EXISTS daily_activity_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            officer_id INTEGER NOT NULL,
            activity_type TEXT NOT NULL,
            summary TEXT NOT NULL,
            photo_path TEXT,
            status TEXT NOT NULL DEFAULT 'Open',
            supervisor_notes TEXT,
            admin_notes TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(site_id) REFERENCES sites(id),
            FOREIGN KEY(officer_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS incident_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            site_id INTEGER NOT NULL,
            officer_id INTEGER NOT NULL,
            incident_type TEXT NOT NULL,
            priority TEXT NOT NULL DEFAULT 'Medium',
            narrative TEXT NOT NULL,
            persons_involved TEXT,
            witnesses TEXT,
            police_notified INTEGER NOT NULL DEFAULT 0,
            client_notified INTEGER NOT NULL DEFAULT 0,
            attachment_path TEXT,
            status TEXT NOT NULL DEFAULT 'Open',
            supervisor_notes TEXT,
            admin_notes TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(company_id) REFERENCES companies(id),
            FOREIGN KEY(site_id) REFERENCES sites(id),
            FOREIGN KEY(officer_id) REFERENCES users(id)
        );
        ''')
    if conn.backend == 'postgres':
        conn.cursor().executescript('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            event_type TEXT NOT NULL,
            actor_user_id INTEGER,
            company_id INTEGER,
            target_type TEXT,
            target_id TEXT,
            message TEXT,
            ip_address TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(actor_user_id) REFERENCES users(id),
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS app_runtime_locks (
            lock_name TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paystubs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            guard_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            pay_period_start TEXT,
            pay_period_end TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(guard_id) REFERENCES users(id),
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS payroll_periods (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            company_id INTEGER NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            locked_at TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            period_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'generated',
            generated_by INTEGER,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_guard_records (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            period_id INTEGER NOT NULL,
            run_id INTEGER,
            company_id INTEGER NOT NULL,
            guard_id INTEGER NOT NULL,
            regular_hours DOUBLE PRECISION DEFAULT 0,
            overtime_hours DOUBLE PRECISION DEFAULT 0,
            pay_rate DOUBLE PRECISION DEFAULT 0,
            gross_pay_estimate DOUBLE PRECISION DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending_review',
            admin_notes TEXT,
            excluded INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS payroll_exports (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            period_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guard_paystubs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            company_id INTEGER NOT NULL,
            guard_id INTEGER NOT NULL,
            period_id INTEGER,
            pay_date TEXT,
            file_path TEXT NOT NULL,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_audit_logs (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            company_id INTEGER NOT NULL,
            period_id INTEGER,
            guard_id INTEGER,
            event_type TEXT NOT NULL,
            notes TEXT,
            actor_user_id INTEGER,
            created_at TEXT NOT NULL
        );
        ''')
    else:
        conn.cursor().executescript('''
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            actor_user_id INTEGER,
            company_id INTEGER,
            target_type TEXT,
            target_id TEXT,
            message TEXT,
            ip_address TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(actor_user_id) REFERENCES users(id),
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS app_runtime_locks (
            lock_name TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paystubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guard_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            pay_period_start TEXT,
            pay_period_end TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(guard_id) REFERENCES users(id),
            FOREIGN KEY(company_id) REFERENCES companies(id)
        );
        CREATE TABLE IF NOT EXISTS payroll_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            locked_at TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'generated',
            generated_by INTEGER,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_guard_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id INTEGER NOT NULL,
            run_id INTEGER,
            company_id INTEGER NOT NULL,
            guard_id INTEGER NOT NULL,
            regular_hours REAL DEFAULT 0,
            overtime_hours REAL DEFAULT 0,
            pay_rate REAL DEFAULT 0,
            gross_pay_estimate REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending_review',
            admin_notes TEXT,
            excluded INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS payroll_exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id INTEGER NOT NULL,
            run_id INTEGER NOT NULL,
            company_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guard_paystubs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            guard_id INTEGER NOT NULL,
            period_id INTEGER,
            pay_date TEXT,
            file_path TEXT NOT NULL,
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payroll_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            period_id INTEGER,
            guard_id INTEGER,
            event_type TEXT NOT NULL,
            notes TEXT,
            actor_user_id INTEGER,
            created_at TEXT NOT NULL
        );
        ''')
    if table_exists(conn, 'reports'):
        ensure_column(conn, 'reports', 'updated_at TEXT')
    offline_cols = ['local_uuid TEXT', 'device_timestamp TEXT', 'synced_at TEXT', 'offline_submitted INTEGER DEFAULT 0']
    if table_exists(conn, 'daily_activity_reports'):
        for col in offline_cols:
            ensure_column(conn, 'daily_activity_reports', col)
    if table_exists(conn, 'incident_reports'):
        for col in offline_cols:
            ensure_column(conn, 'incident_reports', col)
    if table_exists(conn, 'patrol_tours'):
        ensure_column(conn, 'patrol_tours', 'description TEXT')
        ensure_column(conn, 'patrol_tours', 'active INTEGER DEFAULT 1')
        ensure_column(conn, 'patrol_tours', 'created_by INTEGER')
        ensure_column(conn, 'patrol_tours', 'created_at TEXT')
        ensure_column(conn, 'patrol_tours', 'updated_at TEXT')
    if table_exists(conn, 'patrol_tour_checkpoints'):
        ensure_column(conn, 'patrol_tour_checkpoints', 'company_id INTEGER')
        ensure_column(conn, 'patrol_tour_checkpoints', 'site_id INTEGER')
        ensure_column(conn, 'patrol_tour_checkpoints', 'sort_order INTEGER DEFAULT 0')
        ensure_column(conn, 'patrol_tour_checkpoints', 'qr_code TEXT')
        ensure_column(conn, 'patrol_tour_checkpoints', 'nfc_tag_id TEXT')
        ensure_column(conn, 'patrol_tour_checkpoints', 'active INTEGER DEFAULT 1')
        ensure_column(conn, 'patrol_tour_checkpoints', 'created_at TEXT')
        conn.execute('''
            UPDATE patrol_tour_checkpoints
            SET company_id=(SELECT pt.company_id FROM patrol_tours pt WHERE pt.id=patrol_tour_checkpoints.tour_id)
            WHERE company_id IS NULL
        ''')
        conn.execute('''
            UPDATE patrol_tour_checkpoints
            SET site_id=(SELECT pt.site_id FROM patrol_tours pt WHERE pt.id=patrol_tour_checkpoints.tour_id)
            WHERE site_id IS NULL
        ''')
        conn.execute("UPDATE patrol_tour_checkpoints SET active=1 WHERE active IS NULL")
        conn.execute("UPDATE patrol_tour_checkpoints SET sort_order=0 WHERE sort_order IS NULL")
        conn.execute("UPDATE patrol_tour_checkpoints SET created_at=? WHERE created_at IS NULL OR created_at=''", (utc_now_str(),))
        missing_identifiers = conn.execute("SELECT id FROM patrol_tour_checkpoints WHERE qr_code IS NULL OR qr_code='' OR nfc_tag_id IS NULL OR nfc_tag_id=''").fetchall()
        for checkpoint in missing_identifiers:
            conn.execute(
                "UPDATE patrol_tour_checkpoints SET qr_code=COALESCE(NULLIF(qr_code,''), ?), nfc_tag_id=COALESCE(NULLIF(nfc_tag_id,''), ?) WHERE id=?",
                (patrol_token('QR'), patrol_token('NFC'), checkpoint['id']),
            )
    if table_exists(conn, 'patrol_tour_runs'):
        ensure_column(conn, 'patrol_tour_runs', 'excused_reason TEXT')
        ensure_column(conn, 'patrol_tour_runs', 'excused_note TEXT')
        ensure_column(conn, 'patrol_tour_runs', 'excused_by INTEGER')
        ensure_column(conn, 'patrol_tour_runs', 'excused_at TEXT')
    if table_exists(conn, 'patrol_checkpoint_scans'):
        for col in offline_cols:
            ensure_column(conn, 'patrol_checkpoint_scans', col)
    patrol_event_pk = 'SERIAL PRIMARY KEY' if conn.backend == 'postgres' else 'INTEGER PRIMARY KEY AUTOINCREMENT'
    conn.execute(f'''CREATE TABLE IF NOT EXISTS patrol_tour_run_events (id {patrol_event_pk}, company_id INTEGER NOT NULL, tour_run_id INTEGER NOT NULL, event_type TEXT NOT NULL, event_label TEXT NOT NULL, event_note TEXT, reason TEXT, actor_user_id INTEGER, created_at TEXT NOT NULL)''')
    if table_exists(conn, 'sites'):
        ensure_column(conn, 'sites', 'client_id INTEGER')
    if table_exists(conn, 'payroll_guard_records'):
        ensure_column(conn, 'payroll_guard_records', 'manual_override_used INTEGER DEFAULT 0')
        ensure_column(conn, 'payroll_guard_records', 'manual_override_reason TEXT')
    if table_exists(conn, 'companies'):
        ensure_column(conn, 'companies', 'qb_access_token TEXT')
        ensure_column(conn, 'companies', 'qb_refresh_token TEXT')
        ensure_column(conn, 'companies', 'qb_expires_at TEXT')
        ensure_column(conn, 'companies', 'qb_connected_at TEXT')
        ensure_column(conn, 'companies', 'qb_realm_id TEXT')
    if table_exists(conn, 'sessions'):
        for col in ['company_id INTEGER', 'site_id INTEGER', 'role TEXT']:
            ensure_column(conn, 'sessions', col)
    if table_exists(conn, 'users'):
        for col in ['employee_id TEXT', 'badge_id TEXT']:
            ensure_column(conn, 'users', col)
    if table_exists(conn, 'guards'):
        for col in ['employee_id TEXT', 'badge_id TEXT']:
            ensure_column(conn, 'guards', col)
    if APP_ENV == 'production':
        # conn.execute("DELETE FROM users WHERE username IN ('superadmin','admin','guard1','guard2','demoadmin') AND email LIKE '%%.local'")
        conn.execute("DELETE FROM companies WHERE name IN ('SteeleOps Demo',?,'BlueLine Protective') AND id NOT IN (SELECT DISTINCT company_id FROM users WHERE company_id IS NOT NULL)", (PROVIDER_BRAND_NAME,))
    conn.commit(); conn.close()


def create_admin_account(company_name, username, password, full_name, email=''):
    init_db()
    conn = db()
    now = now_utc().strftime('%Y-%m-%d %H:%M:%S')
    company = conn.execute('SELECT * FROM companies WHERE name=?', (company_name,)).fetchone()
    if not company:
        conn.execute('INSERT INTO companies (name, tagline, created_at) VALUES (?, ?, ?)', (company_name, 'Security Operations Simplified', now))
        company = conn.execute('SELECT * FROM companies WHERE name=?', (company_name,)).fetchone()
    existing = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    if existing:
        conn.close(); raise ValueError(f'Username already exists: {username}')
    conn.execute('INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)', (company['id'], username, hash_password(password), full_name, 'company_admin', '', email, '', 0, now))
    conn.commit(); conn.close()


def export_reports_pdf(company_id):
    conn = db()
    company = conn.execute('SELECT * FROM companies WHERE id=?', (company_id,)).fetchone()
    rows = conn.execute('SELECT r.*, s.name as site_name, s.client_company_name FROM reports r JOIN sites s ON r.site_id=s.id WHERE r.company_id=? ORDER BY r.created_at DESC', (company_id,)).fetchall()
    conn.close()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.45 * inch, bottomMargin=0.45 * inch, leftMargin=0.55 * inch, rightMargin=0.55 * inch)
    styles = getSampleStyleSheet()
    title = ParagraphStyle(name='TitleSteele', parent=styles['Heading1'], textColor=colors.HexColor('#991b1b'), fontSize=18, spaceAfter=6)
    small = ParagraphStyle(name='SmallSteele', parent=styles['BodyText'], textColor=colors.HexColor('#4b5563'), fontSize=8, leading=10)
    body = ParagraphStyle(name='BodySteele', parent=styles['BodyText'], fontSize=10.5, leading=14)
    story = [Paragraph('SteeleOps Incident & Activity Report Export', title), Paragraph(company['name'] if company else 'Company', small), Spacer(1, 0.16 * inch)]
    for row in rows:
        meta = [
            ['Type', row['report_type'].title(), 'Status', row.get('status', 'open').title()],
            ['Priority', row.get('priority', 'medium').title(), 'Officer', row['officer_name']],
            ['Date', row['report_date'], 'Time', row['report_time']],
            ['Site', row['site_name'], 'Client', row.get('client_company_name') or '—'],
        ]
        table = Table(meta, colWidths=[0.9 * inch, 2.0 * inch, 0.9 * inch, 2.3 * inch])
        table.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f3f4f6')), ('BOX', (0,0), (-1,-1), 0.6, colors.HexColor('#c0c0c0')), ('INNERGRID', (0,0), (-1,-1), 0.4, colors.HexColor('#e5e7eb')), ('FONTNAME', (0,0), (-1,-1), 'Helvetica'), ('FONTSIZE', (0,0), (-1,-1), 9), ('VALIGN', (0,0), (-1,-1), 'TOP')]))
        story.extend([table, Spacer(1, 0.1 * inch), Paragraph(row['summary'].replace('\n', '<br/>'), body), Spacer(1, 0.18 * inch)])
    doc.build(story)
    return buffer.getvalue()


LOGIN_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>\n        {{ csrf_input|safe }}', LOGIN_HTML)
DASHBOARD_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', DASHBOARD_HTML)
PATROLS_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', PATROLS_HTML)
SCHEDULE_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', SCHEDULE_HTML)
GUARDS_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', GUARDS_HTML)
PATROL_RUN_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', PATROL_RUN_HTML)
PATROL_TOUR_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', PATROL_TOUR_HTML)
REPORTS_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', REPORTS_HTML)
PAYROLL_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>{{ csrf_input|safe }}', PAYROLL_HTML)
PROFILE_HTML = _re.sub(r'<form([^>]*method="post"[^>]*)>', r'<form\1>\n        {{ csrf_input|safe }}', PROFILE_HTML)
LOGIN_HTML = LOGIN_HTML.replace('''      <div class="demo-box">
        <strong>Demo Accounts</strong><br>
        superadmin / admin123<br>
        admin / admin123<br>
        guard1 / guard123
      </div>''', '''      <div class="helper-links"><a href="/password-reset">Forgot password?</a><a href="/guard-login">Guard quick login</a></div>
      {% if show_demo_accounts %}<div class="demo-box">
        <strong>Demo Accounts</strong><br>
        superadmin / admin123<br>
        admin / admin123<br>
        guard1 / guard123
      </div>{% endif %}''')
REPORTS_HTML = REPORTS_HTML.replace('src="/{{ report.photo_path }}"', 'src="{{ report.photo_path }}"')
REPORTS_HTML = REPORTS_HTML.replace('<p>{{ report.summary }}</p>', '<p>{{ report.summary }}</p>{% if report.attachment_path %}<div class="small-muted"><a href="{{ report.attachment_path }}" target="_blank" rel="noopener">Download attachment</a></div>{% endif %}{% if user.role in [\'company_admin\', \'superadmin\', \'supervisor\'] %}<form method="post" action="/report/update" class="inline-form">{{ csrf_input|safe }}<input type="hidden" name="report_id" value="{{ report.id }}"><select name="status"><option value="open" {% if report.status == \'open\' %}selected{% endif %}>open</option><option value="pending" {% if report.status == \'pending\' %}selected{% endif %}>pending</option><option value="closed" {% if report.status == \'closed\' %}selected{% endif %}>closed</option></select><select name="priority"><option value="low" {% if report.priority == \'low\' %}selected{% endif %}>low</option><option value="medium" {% if report.priority == \'medium\' %}selected{% endif %}>medium</option><option value="high" {% if report.priority == \'high\' %}selected{% endif %}>high</option></select><button class="btn ghost" type="submit">Update</button></form>{% endif %}')
PROFILE_HTML = PROFILE_HTML.replace('<h3>Change Password</h3>', '<h3>Change Password</h3><div class="small-muted">You can also use the password reset flow from the login screen.</div>')
PASSWORD_RESET_REQUEST_HTML = r'''{% extends "layout.html" %}
{% block body %}
<div class="simple-shell narrow-shell">
  <div class="card">
    <h1>Password Reset</h1>
    <p class="small-muted">Enter your username or email. For private beta, SteeleOps can generate a secure reset link without email setup.</p>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if reset_link %}<div class="alert success">Reset link: <a href="{{ reset_link }}">{{ reset_link }}</a></div>{% endif %}
    {% if error %}<div class="alert error">{{ error }}</div>{% endif %}
    <form method="post" action="/password-reset" class="stack">{{ csrf_input|safe }}
      <label>Username or Email<input type="text" name="identity" required></label>
      <button class="btn primary" type="submit">Generate Reset Link</button>
    </form>
    <div class="helper-links"><a href="/login">Back to login</a></div>
  </div>
</div>
{% endblock %}'''
PASSWORD_RESET_FORM_HTML = r'''{% extends "layout.html" %}
{% block body %}
<div class="simple-shell narrow-shell">
  <div class="card">
    <h1>Set New Password</h1>
    <p class="small-muted">Token-based reset for private beta access.</p>
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    {% if error %}<div class="alert error">{{ error }}</div>{% endif %}
    <form method="post" action="/password-reset/confirm" class="stack">{{ csrf_input|safe }}
      <input type="hidden" name="token" value="{{ token }}">
      <label>New Password<input type="password" name="new_password" minlength="8" required></label>
      <button class="btn primary" type="submit">Update Password</button>
    </form>
    <div class="helper-links"><a href="/login">Back to login</a></div>
  </div>
</div>
{% endblock %}'''

GUARD_LOGIN_LIST_HTML = r'''{% extends "layout.html" %}
{% block body %}
<div class="simple-shell guard-login-shell">
  <div class="simple-header">
    <div>
      <div class="eyebrow">SteeleOps</div>
      <h1>Guard Quick Login</h1>
      <p class="small-muted">Enter your username, guard name, employee ID, badge ID, or Guard ID and your 4-digit PIN.</p>
    </div>
    {% if current_user %}<a href="/dashboard" class="btn ghost">← Dashboard</a>{% endif %}
  </div>
  <div class="card narrow-shell">
    <h3>Quick PIN Sign In</h3>
    <p class="small-muted">We will identify your company, assigned site, and guard profile automatically. Companies, sites, and guard lists are not browsable from quick login.</p>
    {% if error %}<div class="alert error">{{ error }}</div>{% endif %}
    {% if message %}<div class="alert success">{{ message }}</div>{% endif %}
    <form method="post" action="/guard-login" class="stack guard-pin-form">{{ csrf_input|safe }}
      <label>Username / Name / Employee ID / Badge ID<input type="text" name="identity" value="{{ remembered_identity or '' }}" autocomplete="username" placeholder="e.g. badge123" required autofocus></label>
      <label>4-digit PIN<input class="pin-input" type="password" name="pin" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="current-password" placeholder="••••" required></label>
      <label class="check-row"><input type="checkbox" name="remember_device" value="1" {% if remembered_identity %}checked{% endif %}> Remember this device</label>
      <button class="btn primary" type="submit">Sign In</button>
    </form>
    <div class="helper-links"><a href="/login">Use standard username/password login</a></div>
    {% if show_demo_accounts %}<div class="demo-box">
      <strong>Guard Quick Login Demo</strong><br>
      Ava Carter / 2222<br>
      guard2 / 2222
    </div>{% endif %}
  </div>
</div>
{% endblock %}'''

GUARD_LOGIN_ASSIGNED_SITES_HTML = r'''{% extends "layout.html" %}
{% block body %}
<div class="simple-shell guard-login-shell">
  <div class="simple-header">
    <div>
      <div class="eyebrow">SteeleOps</div>
      <h1>Select Assigned Site</h1>
      <p class="small-muted">{{ user.full_name }} · {{ user.company_name }}</p>
    </div>
    <a href="/logout" class="btn ghost">Logout</a>
  </div>
  <div class="card">
    <div class="section-head"><h3>Your assigned sites</h3><span>{{ sites|length }} sites</span></div>
    <p class="small-muted">Only sites assigned to your guard profile are available.</p>
    {% if error %}<div class="alert error">{{ error }}</div>{% endif %}
    {% if sites %}
    <div class="guard-login-list">
      {% for site in sites %}
      <form method="post" action="/guard-login/sites/select" class="list-item detailed guard-login-item">{{ csrf_input|safe }}
        <input type="hidden" name="site_id" value="{{ site.id }}">
        <div>
          <strong>{{ site.name }}</strong>
          <div class="small-muted">{% if site.client_name %}{{ site.client_name }}{% elif site.address %}{{ site.address }}{% else %}Assigned site{% endif %}</div>
        </div>
        <button class="btn ghost" type="submit">Open dashboard</button>
      </form>
      {% endfor %}
    </div>
    {% else %}
    <div class="empty">No site assigned. Please contact your supervisor.</div>
    {% endif %}
  </div>
</div>
{% endblock %}'''

GUARD_LOGIN_SITE_LIST_HTML = GUARD_LOGIN_ASSIGNED_SITES_HTML
GUARD_LOGIN_GUARD_LIST_HTML = GUARD_LOGIN_ASSIGNED_SITES_HTML
GUARD_LOGIN_PASSWORD_HTML = GUARD_LOGIN_LIST_HTML

STYLES_CSS += r'''
.helper-links { margin-top: 14px; display: flex; gap: 12px; flex-wrap: wrap; }
.helper-links a { color: var(--accent-2); }
.inline-form { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
.inline-form select { width: auto; min-width: 120px; }
.actions.vertical { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.narrow-shell { max-width: 760px; margin: 0 auto; }
.guard-login-shell { max-width: 1200px; margin: 0 auto; }
.guard-login-list { display: grid; gap: 12px; }
.guard-login-item { padding: 16px 0; align-items: center; border-radius: 18px; }
.guard-login-item:hover { background: rgba(255,255,255,.03); padding-left: 12px; padding-right: 12px; }
.guard-login-meta { margin: -4px 0 16px; }
.inline-tools { align-items: center; }
.guard-pin-form { gap: 14px; }
.pin-input { text-align: center; letter-spacing: .65em; font-size: 1.5rem; font-weight: 700; padding-left: 1.1em; }
.fallback-card { margin-top: 14px; padding: 14px 16px; }
.fallback-card summary { cursor: pointer; font-weight: 600; }
.offline-sync-status{position:sticky;top:10px;z-index:20;margin:0 0 14px;padding:10px 14px;border-radius:14px;border:1px solid rgba(255,255,255,.14);background:rgba(10,10,10,.94);color:#e5e7eb;font-weight:700;box-shadow:0 12px 30px rgba(0,0,0,.18)}
.offline-sync-status.synced{border-color:rgba(34,197,94,.45);color:#bbf7d0}
.offline-sync-status.pending{border-color:rgba(250,204,21,.5);color:#fde68a}
.offline-sync-status.offline{border-color:rgba(248,113,113,.55);color:#fecaca}
.top-gap { margin-top: 12px; }
.summary-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 12px 0; }
.summary-list > div { border: 1px solid var(--line); border-radius: 14px; padding: 10px; background: rgba(255,255,255,.03); display: grid; gap: 4px; }
.summary-list span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
.subtle-card { background: rgba(255,255,255,.02); border: 1px solid var(--line); border-radius: 18px; }
.admin-action-card .section-head { align-items: flex-start; }
.result-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }
.result-grid > div { padding: 12px; border-radius: 14px; border: 1px solid var(--line); background: rgba(255,255,255,.03); display: grid; gap: 4px; }
.result-grid strong { font-size: 1.2rem; }
.result-list { margin: 12px 0 0; padding-left: 18px; display: grid; gap: 6px; }
.row-4 { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.btn.danger { background: rgba(239,68,68,.14); border-color: rgba(239,68,68,.35); color: #fecaca; }
.checkpoint-grid { display: grid; gap: 14px; }
.checkpoint-card { border: 1px solid var(--line); border-radius: 18px; padding: 14px; background: rgba(255,255,255,.03); display: grid; gap: 12px; }
.checkpoint-card.done { border-color: rgba(34,197,94,.4); background: rgba(34,197,94,.07); }
.checkpoint-card.missed { border-color: rgba(239,68,68,.45); background: rgba(239,68,68,.08); }
.checkpoint-card-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.checkpoint-order { display: inline-grid; place-items: center; min-width: 30px; height: 30px; border-radius: 999px; background: rgba(220,38,38,.18); color: #f5f5f5; font-weight: 800; margin-right: 8px; }
.identifier-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.identifier-list > div { border: 1px solid rgba(255,255,255,.08); border-radius: 14px; padding: 10px; background: rgba(10,10,10,.32); display: grid; gap: 8px; }
.identifier-list span { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }
.identifier-list code { white-space: normal; word-break: break-all; }
.scan-actions { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; align-items: start; }
.checkpoint-edit { border-top: 1px solid rgba(255,255,255,.08); padding-top: 12px; }
.checkpoint-edit summary { cursor: pointer; width: fit-content; margin-bottom: 10px; list-style: none; }
.checkpoint-edit summary::-webkit-details-marker { display: none; }
.qr-print-layout { display: grid; grid-template-columns: 280px 1fr; gap: 20px; align-items: center; margin: 18px 0; }
.qr-print-image { width: 260px; height: 260px; padding: 12px; border-radius: 18px; background: #fff; }
@media (max-width: 900px) { .row-4, .summary-list, .identifier-list, .scan-actions, .qr-print-layout { grid-template-columns: 1fr; } }
'''



def reset_admin_password_once(conn):
    if os.path.exists(TEMP_ADMIN_RESET_MARKER):
        return False

    targets = conn.execute(
        "SELECT id, username, role FROM users WHERE username=? OR role=?",
        ('jtadmin', 'admin')
    ).fetchall()

    if not targets:
        print('Temporary admin password reset skipped; no matching users found')
        return False

    new_hash = hash_password(TEMP_ADMIN_RESET_PASSWORD)
    for target in targets:
        conn.execute('UPDATE users SET password=? WHERE id=?', (new_hash, target['id']))

    with open(TEMP_ADMIN_RESET_MARKER, 'w', encoding='utf-8') as marker_file:
        marker_file.write(utc_now_str())

    print(f"Temporary admin password reset applied for {len(targets)} user(s)")
    return True


def ensure_assets():
    env.cache.clear()
    templates = {'layout.html': LAYOUT_HTML, 'app_shell.html': APP_SHELL_HTML, 'login.html': LOGIN_HTML, 'dashboard.html': DASHBOARD_HTML, 'admin_company_logo.html': ADMIN_COMPANY_LOGO_HTML, 'patrols.html': PATROLS_HTML, 'schedule.html': SCHEDULE_HTML, 'guards.html': GUARDS_HTML, 'patrol_run.html': PATROL_RUN_HTML, 'patrol_tour.html': PATROL_TOUR_HTML, 'reports.html': REPORTS_HTML, 'payroll.html': PAYROLL_HTML, 'profile.html': PROFILE_HTML, 'admin_paystub_upload.html': ADMIN_PAYSTUB_UPLOAD_HTML, 'guard_paystubs.html': GUARD_PAYSTUBS_HTML,
        'guard_daily_activity_reports.html': GUARD_DAILY_ACTIVITY_REPORTS_HTML,
        'guard_incident_reports.html': GUARD_INCIDENT_REPORTS_HTML,
        'guard_my_reports.html': GUARD_MY_REPORTS_HTML, 'guard_my_report_detail.html': GUARD_MY_REPORT_DETAIL_HTML, 'password_reset_request.html': PASSWORD_RESET_REQUEST_HTML, 'password_reset_form.html': PASSWORD_RESET_FORM_HTML, 'guard_login_list.html': GUARD_LOGIN_LIST_HTML, 'guard_login_assigned_sites.html': GUARD_LOGIN_ASSIGNED_SITES_HTML, 'guard_login_site_list.html': GUARD_LOGIN_SITE_LIST_HTML, 'guard_login_guard_list.html': GUARD_LOGIN_GUARD_LIST_HTML, 'guard_login_password.html': GUARD_LOGIN_PASSWORD_HTML}
    for name, content in templates.items():
        with open(os.path.join(TEMPLATE_DIR, name), 'w', encoding='utf-8') as f:
            f.write(content)
    with open(os.path.join(STATIC_DIR, 'styles.css'), 'w', encoding='utf-8') as f:
        f.write(STYLES_CSS)
    with open(os.path.join(STATIC_DIR, PROVIDER_SHIELD_LOGO_FILENAME), 'w', encoding='utf-8') as f:
        f.write(PROVIDER_SHIELD_LOGO_SVG)


def login_page(environ, start_response, error=None, message=None, reset_link=None):
    return html_response(start_response, render_page(environ, 'login.html', title=PRODUCT_FULL_NAME, error=error, message=message, reset_link=reset_link), extra_headers=csrf_headers(environ))



def guard_login_list_page(environ, start_response, current_user=None, error=None, message=None, identity=None):
    remembered_identity = identity if identity is not None else guard_login_remembered_identity(environ)
    return html_response(
        start_response,
        render_page(environ, 'guard_login_list.html', title='Guard Quick Login', current_user=current_user, remembered_identity=remembered_identity, error=error, message=message),
        extra_headers=csrf_headers(environ),
    )


def guard_login_assigned_sites_page(environ, start_response, user, sites, error=None):
    return html_response(
        start_response,
        render_page(environ, 'guard_login_assigned_sites.html', title='Guard Quick Login', user=user, sites=sites, error=error),
        extra_headers=csrf_headers(environ),
    )


def guard_login_site_list_page(environ, start_response, company, sites, error=None):
    return not_found(start_response)


def guard_login_guard_list_page(environ, start_response, company, site, guards, error=None):
    return not_found(start_response)


def guard_login_password_page(environ, start_response, company, guard, error=None, message=None):
    return guard_login_list_page(environ, start_response, error=error, message=message)

def password_reset_request_page(environ, start_response, error=None, message=None, reset_link=None):
    return html_response(start_response, render_page(environ, 'password_reset_request.html', title='Password Reset', error=error, message=message, reset_link=reset_link), extra_headers=csrf_headers(environ))


def password_reset_form_page(environ, start_response, token='', error=None, message=None):
    return html_response(start_response, render_page(environ, 'password_reset_form.html', title='Reset Password', token=token, error=error, message=message), extra_headers=csrf_headers(environ))


def _new_report_id(conn):
    if conn.backend == 'sqlite':
        return conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    return conn.execute('SELECT MAX(id) AS id FROM reports').fetchone()['id']


def insert_and_get_id(conn, insert_sql, params=()):
    sql = insert_sql.strip().rstrip(';')
    if conn.backend == 'postgres':
        row = conn.execute(f'{sql} RETURNING id', params).fetchone()
        return row['id'] if row else None
    conn.execute(sql, params)
    row = conn.execute('SELECT last_insert_rowid() AS id').fetchone()
    return row['id'] if row else None



def row_value(row, key, default=None):
    if not row:
        return default
    try:
        return row[key]
    except Exception:
        return row.get(key, default) if hasattr(row, 'get') else default


def guard_assignment_id(user):
    return row_value(user, 'guard_id')


def local_submission_uuid(value):
    cleaned = (value or '').strip()
    if cleaned:
        cleaned = re.sub(r'[^A-Za-z0-9_.:-]', '', cleaned)[:80]
    return cleaned or str(uuid.uuid4())


def first_existing_by_local_uuid(conn, table_name, company_id, officer_id, local_uuid):
    if not local_uuid or not table_exists(conn, table_name) or 'local_uuid' not in column_names(conn, table_name):
        return None
    return conn.execute(
        f'SELECT id FROM {table_name} WHERE company_id=? AND officer_id=? AND local_uuid=?',
        (company_id, officer_id, local_uuid),
    ).fetchone()


def first_patrol_scan_by_local_uuid(conn, company_id, guard_id, local_uuid):
    if not local_uuid or not table_exists(conn, 'patrol_checkpoint_scans') or 'local_uuid' not in column_names(conn, 'patrol_checkpoint_scans'):
        return None
    return conn.execute(
        'SELECT id FROM patrol_checkpoint_scans WHERE company_id=? AND guard_id=? AND local_uuid=?',
        (company_id, guard_id, local_uuid),
    ).fetchone()


def file_info_from_offline_attachment(item):
    if not isinstance(item, dict):
        return None
    data_url = item.get('data_url') or ''
    if ',' not in data_url:
        return None
    _header, encoded = data_url.split(',', 1)
    try:
        content = __import__('base64').b64decode(encoded)
    except Exception:
        return None
    return {'filename': os.path.basename(item.get('name') or 'offline-upload.bin'), 'content': content}


def make_offline_file_infos(items):
    return [f for f in (file_info_from_offline_attachment(item) for item in (items or [])) if f]


def insert_daily_activity_report(conn, user, assigned_site_id, data, uploads=None, offline_submitted=0):
    uploads = uploads or []
    local_uuid = local_submission_uuid(data.get('local_uuid'))
    existing = first_existing_by_local_uuid(conn, 'daily_activity_reports', user['company_id'], user['id'], local_uuid)
    if existing:
        return existing['id'], False
    now = utc_now_str()
    device_timestamp = (data.get('device_timestamp') or now).strip()
    photo_path = None
    if uploads:
        _, photo_path = save_upload(uploads[0], 'dar_photos')
    report_id = insert_and_get_id(conn, '''
        INSERT INTO daily_activity_reports (company_id, site_id, officer_id, activity_type, summary, photo_path, status, created_at, local_uuid, device_timestamp, synced_at, offline_submitted)
        VALUES (?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?)
    ''', (user['company_id'], assigned_site_id, user['id'], data['activity_type'], data['summary'], photo_path, device_timestamp, local_uuid, device_timestamp, now, 1 if offline_submitted else 0))
    for upload in uploads:
        create_report_attachment(conn, user['company_id'], 'daily_activity', report_id, user['id'], upload, 'dar_photos')
    return report_id, True


def insert_incident_report(conn, user, assigned_site_id, data, uploads=None, offline_submitted=0):
    uploads = uploads or []
    local_uuid = local_submission_uuid(data.get('local_uuid'))
    existing = first_existing_by_local_uuid(conn, 'incident_reports', user['company_id'], user['id'], local_uuid)
    if existing:
        return existing['id'], False
    now = utc_now_str()
    device_timestamp = (data.get('device_timestamp') or now).strip()
    first_attachment_path = None
    if uploads:
        _, first_attachment_path = save_upload(uploads[0], 'incident_attachments')
    report_id = insert_and_get_id(conn, '''
        INSERT INTO incident_reports (company_id, site_id, officer_id, incident_type, priority, narrative, persons_involved, witnesses, police_notified, client_notified, attachment_path, status, created_at, local_uuid, device_timestamp, synced_at, offline_submitted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?, ?)
    ''', (user['company_id'], assigned_site_id, user['id'], data['incident_type'], data['priority'], data['narrative'], data.get('persons_involved', ''), data.get('witnesses', ''), 1 if data.get('police_notified') else 0, 1 if data.get('client_notified') else 0, first_attachment_path, device_timestamp, local_uuid, device_timestamp, now, 1 if offline_submitted else 0))
    for upload in uploads:
        create_report_attachment(conn, user['company_id'], 'incident', report_id, user['id'], upload, 'incident_attachments')
    return report_id, True

def patrol_token(prefix):
    return f"{prefix}-{secrets.token_urlsafe(10)}"


def create_patrol_checkpoint(conn, company_id, tour_id, site_id, checkpoint_name, sort_order, qr_code=None, nfc_tag_id=None, active=1):
    return insert_and_get_id(
        conn,
        '''INSERT INTO patrol_tour_checkpoints (company_id, tour_id, site_id, checkpoint_name, sort_order, qr_code, nfc_tag_id, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            company_id,
            tour_id,
            site_id,
            (checkpoint_name or 'Checkpoint').strip(),
            sort_order,
            (qr_code or '').strip() or patrol_token('QR'),
            (nfc_tag_id or '').strip() or patrol_token('NFC'),
            1 if str(active) == '1' else 0,
            utc_now_str(),
        ),
    )


def patrol_scope_clause(conn, user, alias='ptr'):
    company_id = get_company_scope_id(user)
    allowed_site_ids = supervisor_site_ids(conn, user)
    clauses = [f'{alias}.company_id=?']
    params = [company_id]
    if row_value(user, 'role') == 'guard' and alias == 'ptr':
        clauses.append(f'{alias}.guard_id=?')
        params.append(row_value(user, 'id'))
    elif allowed_site_ids is not None:
        if not allowed_site_ids:
            clauses.append('1=0')
        else:
            placeholders = ','.join(['?'] * len(allowed_site_ids))
            clauses.append(f'{alias}.site_id IN ({placeholders})')
            params.extend(sorted(allowed_site_ids))
    return ' AND '.join(clauses), params


def parse_patrol_datetime(value):
    if not value:
        return None
    cleaned = str(value).strip().replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(cleaned[:19 if fmt.endswith('%S') else 16], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(value).strip())
    except Exception:
        return None


def patrol_duration_minutes(row):
    started = parse_patrol_datetime(row_value(row, 'started_at'))
    completed = parse_patrol_datetime(row_value(row, 'completed_at'))
    if not started or not completed or completed < started:
        return None
    return round((completed - started).total_seconds() / 60, 1)


def patrol_completion_percentage(total, completed):
    return round((completed / total) * 100, 1) if total else 0


def pluralize(count, singular, plural=None):
    return singular if count == 1 else (plural or f'{singular}s')


PATROL_EXCUSE_REASONS = (
    'Bad Weather',
    'Unsafe Conditions',
    'Emergency',
    'Client Instruction',
    'Site Access Issue',
    'Officer Safety',
    'Other',
)


def patrol_is_excused(row):
    return row_value(row, 'status') == 'excused' or bool(row_value(row, 'excused_at'))


def patrol_is_completed(row):
    return row_value(row, 'status') == 'completed'


def patrol_is_compliant(row):
    return patrol_is_completed(row) or patrol_is_excused(row)


def patrol_is_missed(row):
    return not patrol_is_compliant(row)


def patrol_counts_for_completion(row):
    return not patrol_is_excused(row)


def effective_missed_checkpoint_count(row):
    return 0 if patrol_is_excused(row) else int(row_value(row, 'missed_checkpoint_count', 0) or 0)


def patrol_event(conn, company_id, run_id, event_type, event_label, event_note='', reason='', actor_user_id=None, created_at=None):
    conn.execute(
        '''INSERT INTO patrol_tour_run_events (company_id, tour_run_id, event_type, event_label, event_note, reason, actor_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (company_id, run_id, event_type, event_label, (event_note or '').strip(), (reason or '').strip(), actor_user_id, created_at or utc_now_str()),
    )


def patrol_timeline(conn, run, checkpoints):
    run_id = row_value(run, 'id')
    company_id = row_value(run, 'company_id')
    rows = conn.execute("""
        SELECT e.*, u.full_name AS actor_name
        FROM patrol_tour_run_events e
        LEFT JOIN users u ON u.id=e.actor_user_id
        WHERE e.company_id=? AND e.tour_run_id=?
        ORDER BY e.created_at ASC, e.id ASC
    """, (company_id, run_id)).fetchall()
    timeline = [{
        'created_at': row_value(event, 'created_at'),
        'event_label': row_value(event, 'event_label'),
        'event_note': row_value(event, 'event_note'),
        'reason': row_value(event, 'reason'),
        'actor_name': row_value(event, 'actor_name'),
    } for event in rows]
    if not timeline:
        started_at = row_value(run, 'started_at')
        if started_at:
            timeline.append({'created_at': started_at, 'event_label': 'Patrol assigned', 'event_note': f"Tour assigned to {row_value(run, 'guard_name') or 'guard'}.", 'actor_name': row_value(run, 'guard_name')})
            timeline.append({'created_at': started_at, 'event_label': 'Patrol started', 'event_note': row_value(run, 'site_name') or '', 'actor_name': row_value(run, 'guard_name')})
        for checkpoint in checkpoints:
            if row_value(checkpoint, 'scanned_at'):
                label = 'Patrol incomplete' if row_value(checkpoint, 'missed_checkpoint') else 'Checkpoint scanned'
                note = row_value(checkpoint, 'checkpoint_name') or ''
                timeline.append({'created_at': row_value(checkpoint, 'scanned_at'), 'event_label': label, 'event_note': note, 'actor_name': row_value(run, 'guard_name')})
        if patrol_is_excused(run):
            timeline.append({'created_at': row_value(run, 'excused_at'), 'event_label': 'Patrol excused by admin/supervisor', 'event_note': row_value(run, 'excused_note'), 'reason': row_value(run, 'excused_reason'), 'actor_name': row_value(run, 'excused_by_name')})
        elif patrol_is_missed(run) and row_value(run, 'completed_at'):
            timeline.append({'created_at': row_value(run, 'completed_at'), 'event_label': 'Patrol incomplete', 'event_note': 'Incomplete patrol kept as missed.', 'actor_name': row_value(run, 'guard_name')})
    return sorted(timeline, key=lambda item: item.get('created_at') or '')


def admin_can_review_patrol(conn, user, run):
    return row_value(user, 'role') in {'superadmin', 'company_admin', 'admin', 'supervisor'} and supervisor_can_access_site(conn, user, row_value(run, 'site_id'))


def patrol_issue_alert_data(total_assigned, total_completed, incomplete_count, active_count, missed_count, completion_percentage):
    if not total_assigned or (incomplete_count <= 0 and missed_count <= 0 and completion_percentage >= 100):
        return None

    severity = 'warning' if active_count and not missed_count else 'danger'
    patrol_label = pluralize(incomplete_count, 'patrol tour')
    checkpoint_label = pluralize(missed_count, 'missed patrol')
    message_parts = []
    if incomplete_count:
        message_parts.append(f'{incomplete_count} {patrol_label} {"is" if incomplete_count == 1 else "are"} incomplete')
    if missed_count:
        message_parts.append(f'{missed_count} {checkpoint_label} {"was" if missed_count == 1 else "were"} missed')
    if completion_percentage < 100:
        message_parts.append(f'completion rate is {completion_percentage:.1f}%')
    message = 'Patrol Alert: ' + '; '.join(message_parts) + '. Review patrol exceptions.'

    return {
        'severity': severity,
        'message': message,
        'incomplete_count': incomplete_count,
        'active_count': active_count,
        'missed_count': missed_count,
        'completion_percentage': completion_percentage,
        'href': '/patrols#patrol-issues',
    }


def patrol_dashboard_data(conn, user):
    company_id = get_company_scope_id(user)
    allowed_site_ids = supervisor_site_ids(conn, user)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    tour_filter = ''
    params = [company_id]
    if allowed_site_ids is not None:
        if not allowed_site_ids:
            empty_analytics = {
                'patrol_completion_summary': {'total_assigned': 0, 'total_completed': 0, 'total_excused': 0, 'total_compliant': 0, 'total_missed': 0, 'completion_percentage': 0},
                'patrol_dashboard_widgets': {'patrols_today': 0, 'completed_today': 0, 'excused_today': 0, 'missed_today': 0},
                'patrol_issue_alert': None,
                'missed_checkpoints_by_guard': [],
                'missed_checkpoints_by_site': [],
                'missed_checkpoint_history': [],
                'guard_performance': [],
                'site_performance': [],
                'top_performing_guards': [],
            }
            return {'patrol_tours': [], 'active_patrol_tours': [], 'patrol_runs': [], 'completed_tours': [], 'client_patrol_history': [], **empty_analytics}
        placeholders = ','.join(['?'] * len(allowed_site_ids))
        tour_filter = f' AND pt.site_id IN ({placeholders})'
        params.extend(sorted(allowed_site_ids))
    tours = conn.execute(f'''
        SELECT pt.*, s.name AS site_name,
               (SELECT COUNT(*) FROM patrol_tour_checkpoints pc WHERE pc.company_id=pt.company_id AND pc.tour_id=pt.id AND COALESCE(pc.active,1)=1) AS checkpoint_count
        FROM patrol_tours pt
        JOIN sites s ON s.id=pt.site_id AND s.company_id=pt.company_id
        WHERE pt.company_id=?{tour_filter}
        ORDER BY pt.created_at DESC, pt.id DESC
    ''', tuple(params)).fetchall()
    run_where, run_params = patrol_scope_clause(conn, user, 'ptr')
    runs = conn.execute(f'''
        SELECT ptr.*, pt.name AS tour_name, s.name AS site_name, u.full_name AS guard_name, ex.full_name AS excused_by_name,
               (SELECT COUNT(*) FROM patrol_tour_checkpoints pc WHERE pc.company_id=ptr.company_id AND pc.tour_id=ptr.tour_id AND COALESCE(pc.active,1)=1) AS total_checkpoints,
               (SELECT COUNT(DISTINCT pcs.checkpoint_id) FROM patrol_checkpoint_scans pcs WHERE pcs.tour_run_id=ptr.id AND COALESCE(pcs.missed_checkpoint,0)=0) AS scanned_checkpoints
        FROM patrol_tour_runs ptr
        JOIN patrol_tours pt ON pt.id=ptr.tour_id AND pt.company_id=ptr.company_id
        JOIN sites s ON s.id=ptr.site_id AND s.company_id=ptr.company_id
        JOIN users u ON u.id=ptr.guard_id
        LEFT JOIN users ex ON ex.id=ptr.excused_by
        WHERE {run_where}
        ORDER BY ptr.started_at DESC
        LIMIT 20
    ''', tuple(run_params)).fetchall()
    all_runs = conn.execute(f'''
        SELECT ptr.*, pt.name AS tour_name, s.name AS site_name, u.full_name AS guard_name, ex.full_name AS excused_by_name
        FROM patrol_tour_runs ptr
        JOIN patrol_tours pt ON pt.id=ptr.tour_id AND pt.company_id=ptr.company_id
        JOIN sites s ON s.id=ptr.site_id AND s.company_id=ptr.company_id
        JOIN users u ON u.id=ptr.guard_id
        LEFT JOIN users ex ON ex.id=ptr.excused_by
        WHERE {run_where}
        ORDER BY ptr.started_at DESC
    ''', tuple(run_params)).fetchall()
    total_assigned = len(all_runs)
    total_completed = len([r for r in all_runs if patrol_is_completed(r)])
    total_excused = len([r for r in all_runs if patrol_is_excused(r)])
    total_counted = len([r for r in all_runs if patrol_counts_for_completion(r)])
    total_missed = len([r for r in all_runs if patrol_counts_for_completion(r) and patrol_is_missed(r)])
    patrol_completion_summary = {
        'total_assigned': total_assigned,
        'total_completed': total_completed,
        'total_excused': total_excused,
        'total_compliant': total_completed,
        'total_missed': total_missed,
        'completion_percentage': patrol_completion_percentage(total_counted, total_completed),
    }
    patrols_today = len([r for r in all_runs if str(row_value(r, 'started_at') or '')[:10] == today.isoformat()])
    completed_today = len([r for r in all_runs if patrol_is_completed(r) and str(row_value(r, 'completed_at') or '')[:10] == today.isoformat()])
    excused_today = len([r for r in all_runs if patrol_is_excused(r) and str(row_value(r, 'excused_at') or '')[:10] == today.isoformat()])
    missed_today = len([r for r in all_runs if patrol_is_missed(r) and str(row_value(r, 'started_at') or '')[:10] == today.isoformat()])
    incomplete_count = total_missed
    active_count = len([r for r in all_runs if row_value(r, 'status') == 'in_progress'])
    missed_total = total_missed
    patrol_issue_alert = patrol_issue_alert_data(
        total_counted,
        total_completed,
        incomplete_count,
        active_count,
        missed_total,
        patrol_completion_summary['completion_percentage'],
    )

    missed_where, missed_params = patrol_scope_clause(conn, user, 'pcs')
    missed_history = conn.execute(f'''
        SELECT pcs.*, pc.checkpoint_name, pt.name AS tour_name, s.name AS site_name, u.full_name AS guard_name
        FROM patrol_checkpoint_scans pcs
        JOIN patrol_tour_checkpoints pc ON pc.id=pcs.checkpoint_id AND pc.company_id=pcs.company_id
        JOIN patrol_tours pt ON pt.id=pcs.tour_id AND pt.company_id=pcs.company_id
        JOIN sites s ON s.id=pcs.site_id AND s.company_id=pcs.company_id
        JOIN users u ON u.id=pcs.guard_id
        JOIN patrol_tour_runs ptr ON ptr.id=pcs.tour_run_id AND ptr.company_id=pcs.company_id
        WHERE {missed_where} AND COALESCE(pcs.missed_checkpoint,0)=1 AND COALESCE(ptr.status,'')!='excused'
        ORDER BY pcs.scanned_at DESC
        LIMIT 25
    ''', tuple(missed_params)).fetchall()

    missed_by_guard = {}
    missed_by_site = {}
    for row in missed_history:
        guard_name = row_value(row, 'guard_name') or 'Unknown guard'
        site_name = row_value(row, 'site_name') or 'Unknown site'
        missed_by_guard[guard_name] = missed_by_guard.get(guard_name, 0) + 1
        missed_by_site[site_name] = missed_by_site.get(site_name, 0) + 1

    guard_stats = {}
    site_stats = {}
    for run in all_runs:
        guard_id = row_value(run, 'guard_id')
        site_id = row_value(run, 'site_id')
        guard = guard_stats.setdefault(guard_id, {'guard_id': guard_id, 'guard_name': row_value(run, 'guard_name') or 'Unknown guard', 'week_completed': 0, 'month_completed': 0, 'completed_total': 0, 'duration_total': 0, 'duration_count': 0, 'missed_count': 0, 'assigned_total': 0})
        site = site_stats.setdefault(site_id, {'site_id': site_id, 'site_name': row_value(run, 'site_name') or 'Unknown site', 'total_assigned': 0, 'total_completed': 0, 'last_completed_patrol': None, 'active_routes': 0})
        if patrol_counts_for_completion(run):
            guard['assigned_total'] += 1
            site['total_assigned'] += 1
        if patrol_is_completed(run):
            completed_at = str(row_value(run, 'completed_at') or '')
            guard['completed_total'] += 1
            site['total_completed'] += 1
            if completed_at[:10] >= week_start.isoformat():
                guard['week_completed'] += 1
            if completed_at[:10] >= month_start.isoformat():
                guard['month_completed'] += 1
            duration = patrol_duration_minutes(run)
            if duration is not None:
                guard['duration_total'] += duration
                guard['duration_count'] += 1
            if completed_at and (site['last_completed_patrol'] is None or completed_at > site['last_completed_patrol']):
                site['last_completed_patrol'] = completed_at
        guard['missed_count'] += effective_missed_checkpoint_count(run)
    for tour in tours:
        site_id = row_value(tour, 'site_id')
        site = site_stats.setdefault(site_id, {'site_id': site_id, 'site_name': row_value(tour, 'site_name') or 'Unknown site', 'total_assigned': 0, 'total_completed': 0, 'last_completed_patrol': None, 'active_routes': 0})
        if row_value(tour, 'active'):
            site['active_routes'] += 1
    guard_performance = []
    for stat in guard_stats.values():
        stat['average_completion_minutes'] = round(stat['duration_total'] / stat['duration_count'], 1) if stat['duration_count'] else 0
        stat['completion_percentage'] = patrol_completion_percentage(stat['assigned_total'], stat['completed_total'])
        guard_performance.append(stat)
    guard_performance.sort(key=lambda item: (item['month_completed'], item['week_completed'], -item['missed_count']), reverse=True)
    site_performance = []
    for stat in site_stats.values():
        stat['completion_percentage'] = patrol_completion_percentage(stat['total_assigned'], stat['total_completed'])
        site_performance.append(stat)
    site_performance.sort(key=lambda item: item['site_name'])
    top_performing_guards = sorted(guard_performance, key=lambda item: (item['completion_percentage'], item['month_completed'], -item['missed_count']), reverse=True)[:5]

    patrol_dashboard_widgets = {'patrols_today': patrols_today, 'completed_today': completed_today, 'excused_today': excused_today, 'missed_today': missed_today}
    return {
        'patrol_tours': tours,
        'active_patrol_tours': [t for t in tours if row_value(t, 'active')],
        'patrol_runs': runs,
        'completed_tours': [r for r in runs if patrol_is_completed(r)],
        'excused_tours': [r for r in runs if patrol_is_excused(r)],
        'missed_tours': [r for r in runs if patrol_is_missed(r)],
        'client_patrol_history': [r for r in runs if patrol_is_compliant(r)],
        'patrol_completion_summary': patrol_completion_summary,
        'patrol_dashboard_widgets': patrol_dashboard_widgets,
        'patrol_issue_alert': patrol_issue_alert,
        'missed_checkpoints_by_guard': [{'name': name, 'missed_count': count} for name, count in sorted(missed_by_guard.items(), key=lambda item: item[1], reverse=True)],
        'missed_checkpoints_by_site': [{'name': name, 'missed_count': count} for name, count in sorted(missed_by_site.items(), key=lambda item: item[1], reverse=True)],
        'missed_checkpoint_history': missed_history,
        'guard_performance': guard_performance,
        'site_performance': site_performance,
        'top_performing_guards': top_performing_guards,
    }


def patrol_history_csv(conn, user):
    run_where, run_params = patrol_scope_clause(conn, user, 'ptr')
    rows = conn.execute(f'''
        SELECT ptr.id, pt.name AS tour_name, s.name AS site_name, u.full_name AS guard_name, ptr.status, ptr.started_at, ptr.completed_at, CASE WHEN ptr.status='excused' THEN 0 ELSE ptr.missed_checkpoint_count END AS missed_checkpoint_count,
               (SELECT COUNT(*) FROM patrol_tour_checkpoints pc WHERE pc.company_id=ptr.company_id AND pc.tour_id=ptr.tour_id AND COALESCE(pc.active,1)=1) AS total_checkpoints,
               (SELECT COUNT(DISTINCT pcs.checkpoint_id) FROM patrol_checkpoint_scans pcs WHERE pcs.tour_run_id=ptr.id AND COALESCE(pcs.missed_checkpoint,0)=0) AS scanned_checkpoints
        FROM patrol_tour_runs ptr
        JOIN patrol_tours pt ON pt.id=ptr.tour_id AND pt.company_id=ptr.company_id
        JOIN sites s ON s.id=ptr.site_id AND s.company_id=ptr.company_id
        JOIN users u ON u.id=ptr.guard_id
        WHERE {run_where}
        ORDER BY ptr.started_at DESC
    ''', tuple(run_params)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Run ID', 'Tour', 'Site', 'Guard', 'Status', 'Started At', 'Completed At', 'Duration Minutes', 'Total Checkpoints', 'Scanned Checkpoints', 'Missed Checkpoints'])
    for row in rows:
        writer.writerow([row_value(row, 'id'), row_value(row, 'tour_name'), row_value(row, 'site_name'), row_value(row, 'guard_name'), row_value(row, 'status'), row_value(row, 'started_at'), row_value(row, 'completed_at') or '', patrol_duration_minutes(row) or '', row_value(row, 'total_checkpoints'), row_value(row, 'scanned_checkpoints'), row_value(row, 'missed_checkpoint_count') or 0])
    return output.getvalue().encode('utf-8')


def missed_checkpoints_csv(conn, user):
    missed_where, missed_params = patrol_scope_clause(conn, user, 'pcs')
    rows = conn.execute(f'''
        SELECT pcs.id, pcs.scanned_at, pc.checkpoint_name, pt.name AS tour_name, s.name AS site_name, u.full_name AS guard_name, pcs.tour_run_id, pcs.scan_method
        FROM patrol_checkpoint_scans pcs
        JOIN patrol_tour_checkpoints pc ON pc.id=pcs.checkpoint_id AND pc.company_id=pcs.company_id
        JOIN patrol_tours pt ON pt.id=pcs.tour_id AND pt.company_id=pcs.company_id
        JOIN sites s ON s.id=pcs.site_id AND s.company_id=pcs.company_id
        JOIN users u ON u.id=pcs.guard_id
        JOIN patrol_tour_runs ptr ON ptr.id=pcs.tour_run_id AND ptr.company_id=pcs.company_id
        WHERE {missed_where} AND COALESCE(pcs.missed_checkpoint,0)=1 AND COALESCE(ptr.status,'')!='excused'
        ORDER BY pcs.scanned_at DESC
    ''', tuple(missed_params)).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Missed Scan ID', 'Date/Time', 'Checkpoint', 'Tour', 'Site', 'Guard', 'Run ID', 'Scan Method'])
    for row in rows:
        writer.writerow([row_value(row, 'id'), row_value(row, 'scanned_at'), row_value(row, 'checkpoint_name'), row_value(row, 'tour_name'), row_value(row, 'site_name'), row_value(row, 'guard_name'), row_value(row, 'tour_run_id'), row_value(row, 'scan_method')])
    return output.getvalue().encode('utf-8')


def patrol_run_detail(conn, company_id, run_id):
    run = conn.execute('''SELECT ptr.*, pt.name AS tour_name, s.name AS site_name, u.full_name AS guard_name, ex.full_name AS excused_by_name FROM patrol_tour_runs ptr JOIN patrol_tours pt ON pt.id=ptr.tour_id AND pt.company_id=ptr.company_id JOIN sites s ON s.id=ptr.site_id AND s.company_id=ptr.company_id JOIN users u ON u.id=ptr.guard_id LEFT JOIN users ex ON ex.id=ptr.excused_by WHERE ptr.company_id=? AND ptr.id=?''', (company_id, run_id)).fetchone()
    if not run:
        return None, []
    checkpoints = conn.execute('''SELECT pc.*, pcs.scan_method, pcs.scanned_at, pcs.gps_latitude, pcs.gps_longitude, COALESCE(pcs.missed_checkpoint,0) AS missed_checkpoint FROM patrol_tour_checkpoints pc LEFT JOIN patrol_checkpoint_scans pcs ON pcs.checkpoint_id=pc.id AND pcs.tour_run_id=? WHERE pc.company_id=? AND pc.tour_id=? AND COALESCE(pc.active,1)=1 ORDER BY pc.sort_order, pc.id''', (run_id, company_id, run['tour_id'])).fetchall()
    return run, checkpoints


def patrol_tour_detail(conn, company_id, tour_id):
    tour = conn.execute('''
        SELECT pt.*, s.name AS site_name,
               (SELECT COUNT(*) FROM patrol_tour_checkpoints pc WHERE pc.company_id=pt.company_id AND pc.tour_id=pt.id AND COALESCE(pc.active,1)=1) AS checkpoint_count
        FROM patrol_tours pt
        JOIN sites s ON s.id=pt.site_id AND s.company_id=pt.company_id
        WHERE pt.company_id=? AND pt.id=?
    ''', (company_id, tour_id)).fetchone()
    if not tour:
        return None, []
    checkpoints = conn.execute('''
        SELECT id, company_id, tour_id, site_id, checkpoint_name,
               COALESCE(sort_order,0) AS sort_order,
               COALESCE(qr_code,'') AS qr_code,
               COALESCE(nfc_tag_id,'') AS nfc_tag_id,
               COALESCE(active,1) AS active,
               created_at
        FROM patrol_tour_checkpoints
        WHERE company_id=? AND tour_id=?
        ORDER BY COALESCE(sort_order,0), id
    ''', (company_id, tour['id'])).fetchall()
    return tour, checkpoints


def application(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    method = environ.get('REQUEST_METHOD', 'GET').upper()
    user = get_current_user(environ)
    query = parse_query(environ)
    if path in {'/health', '/healthz', '/ready', '/readyz'}:
        return json_response(start_response, {'status': 'ok', 'service': 'steeleops'})
    if path.startswith('/static/') or path.startswith('/uploads/'):
        return serve_static(environ, start_response, path.lstrip('/'))
    if path == '/api/offline-sync' and method == 'POST':
        if not user:
            return json_response(start_response, {'error': 'Login required'}, status='401 Unauthorized')
        if user['role'] != 'guard':
            return json_response(start_response, {'error': 'Only guards can sync offline mobile guard records.'}, status='403 Forbidden')
        try:
            size = int(environ.get('CONTENT_LENGTH', '0') or 0)
        except ValueError:
            size = 0
        try:
            payload = json.loads(environ['wsgi.input'].read(size).decode('utf-8') or '{}')
        except Exception:
            return json_response(start_response, {'error': 'Invalid offline sync payload.'}, status='400 Bad Request')
        records = payload.get('records') or []
        results = []
        conn = db()
        assigned_site = guard_primary_assigned_site(conn, user, preferred_site_id=row_value(user, 'session_site_id'))
        for record in records:
            kind = record.get('kind')
            data = record.get('data') or {}
            local_uuid = local_submission_uuid(record.get('local_uuid') or data.get('local_uuid'))
            data['local_uuid'] = local_uuid
            data['device_timestamp'] = data.get('device_timestamp') or record.get('device_timestamp') or utc_now_str()
            try:
                if kind == 'daily_activity':
                    if not assigned_site:
                        raise ValueError('No assigned site found for your account.')
                    if not data.get('activity_type') or not data.get('summary'):
                        raise ValueError('Activity type and summary are required.')
                    report_id, created = insert_daily_activity_report(conn, user, assigned_site['site_id'], data, make_offline_file_infos(record.get('attachments')), offline_submitted=1)
                    results.append({'local_uuid': local_uuid, 'kind': kind, 'server_id': report_id, 'status': 'synced' if created else 'duplicate'})
                elif kind == 'incident':
                    if not assigned_site:
                        raise ValueError('No assigned site found for your account.')
                    if not data.get('incident_type') or not data.get('priority') or not data.get('narrative'):
                        raise ValueError('Incident type, priority, and narrative are required.')
                    report_id, created = insert_incident_report(conn, user, assigned_site['site_id'], data, make_offline_file_infos(record.get('attachments')), offline_submitted=1)
                    results.append({'local_uuid': local_uuid, 'kind': kind, 'server_id': report_id, 'status': 'synced' if created else 'duplicate'})
                elif kind == 'patrol_scan':
                    method_value = (data.get('scan_method') or '').strip().upper()
                    if method_value not in {'QR', 'NFC', 'MANUAL'}:
                        raise ValueError('Scan method must be QR, NFC, or MANUAL')
                    if first_patrol_scan_by_local_uuid(conn, user['company_id'], user['id'], local_uuid):
                        results.append({'local_uuid': local_uuid, 'kind': kind, 'status': 'duplicate'})
                        continue
                    run = conn.execute("SELECT * FROM patrol_tour_runs WHERE id=? AND company_id=? AND guard_id=? AND status='in_progress'", (data.get('run_id'), user['company_id'], user['id'])).fetchone()
                    if not run:
                        raise ValueError('Active patrol run not found')
                    checkpoint = conn.execute('SELECT * FROM patrol_tour_checkpoints WHERE id=? AND company_id=? AND tour_id=? AND active=1', (data.get('checkpoint_id'), user['company_id'], run['tour_id'])).fetchone()
                    if not checkpoint:
                        raise ValueError('Checkpoint not found')
                    expected = checkpoint['qr_code'] if method_value == 'QR' else checkpoint['nfc_tag_id']
                    if method_value != 'MANUAL' and (data.get('scan_value') or '').strip() != expected:
                        raise ValueError('Scanned QR/NFC value does not match this checkpoint.')
                    existing = conn.execute('SELECT id FROM patrol_checkpoint_scans WHERE tour_run_id=? AND checkpoint_id=?', (run['id'], checkpoint['id'])).fetchone()
                    if existing:
                        results.append({'local_uuid': local_uuid, 'kind': kind, 'server_id': existing['id'], 'status': 'duplicate'})
                        continue
                    device_timestamp = (data.get('device_timestamp') or utc_now_str()).strip()
                    conn.execute('''INSERT INTO patrol_checkpoint_scans (company_id, site_id, tour_id, tour_run_id, checkpoint_id, guard_id, scan_method, scanned_at, gps_latitude, gps_longitude, missed_checkpoint, local_uuid, device_timestamp, synced_at, offline_submitted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 1)''', (user['company_id'], run['site_id'], run['tour_id'], run['id'], checkpoint['id'], user['id'], method_value, device_timestamp, data.get('gps_latitude', ''), data.get('gps_longitude', ''), local_uuid, device_timestamp, utc_now_str()))
                    results.append({'local_uuid': local_uuid, 'kind': kind, 'status': 'synced'})
                else:
                    raise ValueError('Unsupported offline record type.')
            except Exception as exc:
                results.append({'local_uuid': local_uuid, 'kind': kind, 'status': 'error', 'error': str(exc)})
        conn.commit()
        conn.close()
        return json_response(start_response, {'results': results, 'synced_at': utc_now_str()})
    if method == 'POST':
        if path not in {'/internal/run-missed-clock-check', '/internal/run-missed-clock-check/', '/api/offline-sync'}:
            data, _files = parse_post(environ)
            if not validate_csrf(environ, data):
                return forbidden(start_response, 'Invalid or missing CSRF token.')
    if path == '/':
        try:
            return redirect(start_response, '/dashboard' if user else '/login')
        except Exception as exc:
            log_route_exception('/', exc)
            return html_response(start_response, b'<h1>Something went wrong. Please try again.</h1>', status='500 Internal Server Error')
    if path == '/reset-admin':
        return text_response(start_response, '410 Gone', 'Route removed. Use /repair-admin for one-time admin repair.')
    if path == '/repair-admin':
        conn = db()
        try:
            result = repair_admin_account(conn)
            conn.commit()
            return text_response(start_response, '200 OK', result)
        except Exception as exc:
            conn.rollback()
            return text_response(start_response, '500 Internal Server Error', f'Admin repair failed: {exc}')
        finally:
            conn.close()
    if path == '/login' and method == 'GET':
        return login_page(environ, start_response)
    if path == '/login' and method == 'POST':
        data, _ = parse_post(environ)
        username = data.get('username', '').strip(); password = data.get('password', '')
        if not login_allowed(username):
            return login_page(environ, start_response, 'Too many failed attempts. Please wait 15 minutes and try again.')
        conn = db(); found = conn.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone(); conn.close()
        if not found or not verify_password(password, found['password']):
            record_login_attempt(username, False, environ=environ)
            return login_page(environ, start_response, 'Invalid username or password.')
        record_login_attempt(username, True, environ=environ, user_id=found['id'], company_id=found['company_id'])
        assigned_sites = guard_login_assigned_sites(found['company_id'], found['guard_id']) if found['role'] == 'guard' and found['guard_id'] else []
        selected_site_id = assigned_sites[0]['id'] if found['role'] == 'guard' and assigned_sites else None
        session_id = create_session(found['id'], company_id=found['company_id'], site_id=selected_site_id, role=found['role'])
        headers = [('Set-Cookie', cookie_header(session_id))]
        if found['role'] == 'guard' and len(assigned_sites) > 1:
            return redirect(start_response, '/guard-login/sites', headers)
        return redirect(start_response, '/dashboard', headers)

    if path == '/guard-login' and method == 'GET':
        return guard_login_list_page(environ, start_response, current_user=user)
    if path == '/guard-login' and method == 'POST':
        data, _ = parse_post(environ)
        identity = (data.get('identity') or '').strip()
        pin = normalize_pin(data.get('pin', ''))
        rate_limit_key = f'guard:{identity.lower()}' if identity else 'guard:blank'
        if not login_allowed(rate_limit_key):
            return guard_login_list_page(environ, start_response, current_user=user, identity=identity, error='Too many failed attempts. Please wait 15 minutes and try again.')
        guard = guard_login_identity_record(identity, pin)
        if not guard:
            record_login_attempt(rate_limit_key, False, environ=environ, user_id=guard['user_id'] if guard else None, company_id=guard['company_id'] if guard else None)
            return guard_login_list_page(environ, start_response, current_user=user, identity=identity, error='Invalid Guard ID or PIN.')
        assigned_sites = guard_login_assigned_sites(guard['company_id'], guard['guard_profile_id'])
        if not assigned_sites:
            record_login_attempt(rate_limit_key, False, environ=environ, user_id=guard['user_id'], company_id=guard['company_id'])
            return guard_login_list_page(environ, start_response, current_user=user, identity=identity, error='No site assigned. Please contact your supervisor.')
        record_login_attempt(rate_limit_key, True, environ=environ, user_id=guard['user_id'], company_id=guard['company_id'])
        selected_site_id = assigned_sites[0]['id']
        session_id = create_session(guard['user_id'], company_id=guard['company_id'], site_id=selected_site_id, role='guard')
        headers = [('Set-Cookie', cookie_header(session_id))]
        remember_cookie = guard_login_identity_cookie(identity, data.get('remember_device') == '1')
        headers.append(('Set-Cookie', remember_cookie or delete_guard_login_identity_cookie()))
        if len(assigned_sites) == 1:
            return redirect(start_response, f'/dashboard?site_id={assigned_sites[0]["id"]}', headers)
        return redirect(start_response, '/guard-login/sites', headers)
    if path == '/guard-login/sites' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect(start_response, '/dashboard')
        sites = guard_login_assigned_sites(user['company_id'], guard_assignment_id(user))
        if len(sites) == 1:
            return redirect(start_response, f'/dashboard?site_id={sites[0]["id"]}')
        return guard_login_assigned_sites_page(environ, start_response, user, sites)
    if path == '/guard-login/sites/select' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect(start_response, '/dashboard')
        data, _ = parse_post(environ)
        site_id = (data.get('site_id') or '').strip()
        conn = db()
        can_access_site = site_id.isdigit() and supervisor_can_access_site(conn, user, int(site_id))
        conn.close()
        if not can_access_site:
            return guard_login_assigned_sites_page(environ, start_response, user, guard_login_assigned_sites(user['company_id'], guard_assignment_id(user)), error='You can only select sites assigned to your guard profile.')
        _session, session_id = get_session(environ)
        set_session_site(session_id, int(site_id))
        return redirect(start_response, f'/dashboard?site_id={site_id}')
    site_list_match = re.match(r'^/guard-login/\d+/sites$', path)
    if site_list_match and method == 'GET':
        return not_found(start_response)
    site_guard_match = re.match(r'^/guard-login/\d+/sites/\d+$', path)
    if site_guard_match and method == 'GET':
        return not_found(start_response)
    guard_match = re.match(r'^/guard-login/\d+$', path)
    if guard_match and method in {'GET', 'POST'}:
        return not_found(start_response)
    debug_guard_match = re.match(r'^/debug/guard-site-check/(\d+)$', path)
    if debug_guard_match and method == 'GET':
        user, response = require_admin(environ, start_response)
        if response: return response
        company_id = user['company_id']
        selected_site_id = (query.get('site_id') or '').strip()
        payload = guard_site_debug_payload(
            company_id,
            int(debug_guard_match.group(1)),
            int(selected_site_id) if selected_site_id.isdigit() else None,
        )
        return json_response(start_response, payload)

    if path == '/password-reset' and method == 'GET':
        return password_reset_request_page(environ, start_response)
    if path == '/password-reset' and method == 'POST':
        data, _ = parse_post(environ)
        identity = data.get('identity', '').strip(); reset_link = None
        conn = db(); found = conn.execute('SELECT * FROM users WHERE username=? OR email=?', (identity, identity)).fetchone(); conn.close()
        if found:
            _token, url = create_password_reset(found['id'], environ)
            log_audit('password_reset_requested', actor_user_id=found['id'], company_id=found['company_id'], target_type='user', target_id=found['id'], message='password reset requested', environ=environ)
            if ALLOW_BROWSER_PASSWORD_RESET_LINKS:
                reset_link = url
        return password_reset_request_page(environ, start_response, message='If the account exists, a reset link has been generated.', reset_link=reset_link)
    if path == '/password-reset/confirm' and method == 'GET':
        token = query.get('token', '')
        if not token or not get_password_reset_row(token):
            return password_reset_form_page(environ, start_response, token=token, error='That reset link is invalid or expired.')
        return password_reset_form_page(environ, start_response, token=token)
    if path == '/password-reset/confirm' and method == 'POST':
        data, _ = parse_post(environ)
        token = data.get('token', ''); new_password = data.get('new_password', '')
        row = get_password_reset_row(token)
        if len(new_password) < 8 or not row:
            return password_reset_form_page(environ, start_response, token=token, error='Reset failed. Use at least 8 characters and a valid reset link.')
        consume_password_reset(token, new_password)
        log_audit('password_reset_completed', actor_user_id=row['user_id'], company_id=row['company_id'], target_type='user', target_id=row['user_id'], message='password reset completed', environ=environ)
        return login_page(environ, start_response, message='Password updated. You can sign in now.')
    if path == '/logout':
        _, sid = get_session(environ); destroy_session(sid)
        return redirect(start_response, '/login', [('Set-Cookie', delete_cookie_header())])
    if path == '/dashboard':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] == 'guard':
            assigned_sites = guard_login_assigned_sites(user['company_id'], guard_assignment_id(user))
            if len(assigned_sites) > 1 and not row_value(user, 'session_site_id'):
                return redirect(start_response, '/guard-login/sites')
        return dashboard_page(environ, start_response, user, active_path='/dashboard', view='week', title=PRODUCT_FULL_NAME)
    if path == '/patrols':
        user, response = require_login(environ, start_response)
        if response: return response
        return app_page(environ, start_response, user, 'patrols.html', active_path='/patrols', view='week', title='Patrols')
    if path == '/weekly-schedule':
        user, response = require_login(environ, start_response)
        if response: return response
        return app_page(environ, start_response, user, 'schedule.html', active_path='/weekly-schedule', view='week', title='Weekly Schedule')
    if path == '/monthly-schedule':
        user, response = require_login(environ, start_response)
        if response: return response
        return app_page(environ, start_response, user, 'schedule.html', active_path='/monthly-schedule', view='month', title='Monthly Schedule')
    if path == '/profile':
        user, response = require_login(environ, start_response)
        if response: return response
        return profile_page(environ, start_response, user)
    if path == '/profile/update' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); conn.execute('UPDATE users SET full_name=?, phone=?, email=?, license_number=? WHERE id=?', (data.get('full_name', user['full_name']), data.get('phone', ''), data.get('email', ''), data.get('license_number', ''), user['id'])); conn.commit(); conn.close(); log_audit('profile_updated', actor_user_id=user['id'], company_id=user['company_id'], target_type='user', target_id=user['id'], message='guard profile updated', environ=environ); return redirect(start_response, '/profile')
    if path == '/profile/password' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        if len(data.get('new_password', '')) < 8 or not verify_password(data.get('current_password', ''), user['password']):
            return profile_page(environ, start_response, get_current_user(environ), error='Password change failed. Check current password and use at least 8 characters.')
        conn = db(); conn.execute('UPDATE users SET password=? WHERE id=?', (hash_password(data['new_password']), user['id'])); conn.commit(); conn.close(); log_audit('password_changed', actor_user_id=user['id'], company_id=user['company_id'], target_type='user', target_id=user['id'], message='password changed from profile', environ=environ); return profile_page(environ, start_response, get_current_user(environ), message='Password updated successfully.')
    if path == '/availability/save' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db()
        for weekday in range(7):
            is_available = 1 if data.get(f'available_{weekday}') == 'on' else 0
            conn.execute('UPDATE availability SET available_start=?, available_end=?, is_available=? WHERE user_id=? AND weekday=?', (data.get(f'start_{weekday}', '08:00'), data.get(f'end_{weekday}', '20:00'), is_available, user['id'], weekday))
        conn.commit(); conn.close(); return redirect(start_response, '/profile')
    if path in {'/clock-in', '/clock-out'} and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        action = 'in' if path == '/clock-in' else 'out'
        ok, message = process_shift_clock_action(user, data.get('shift_id'), action)
        if not ok:
            return bad_request(start_response, message)
        log_audit('shift_time_event', actor_user_id=user['id'], company_id=user['company_id'], target_type='shift', target_id=data.get('shift_id'), message=message, environ=environ)
        return redirect(start_response, '/dashboard')
    if path == '/shift/clock' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); shift_id = data.get('shift_id'); action = data.get('action')
        ok, message = process_shift_clock_action(user, shift_id, action)
        if not ok:
            return bad_request(start_response, message)
        log_audit('shift_time_event', actor_user_id=user['id'], company_id=user['company_id'], target_type='shift', target_id=shift_id, message=message, environ=environ); return redirect(start_response, '/dashboard')
    if path == '/shift/claim' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return bad_request(start_response, 'Only guards can claim open shifts')
        data, _ = parse_post(environ); shift_id = data.get('shift_id')
        if not shift_id:
            return bad_request(start_response, 'Missing shift')
        conn = db()
        shift = conn.execute('SELECT * FROM shifts WHERE id=? AND company_id=?', (shift_id, user['company_id'])).fetchone()
        if not shift:
            conn.close(); return bad_request(start_response, 'Shift not found')
        if shift_assignment_value(shift) is not None or shift['status'] != 'open':
            conn.close(); return bad_request(start_response, 'Shift is no longer open')
        if not supervisor_can_access_site(conn, user, shift['site_id']):
            conn.close(); return bad_request(start_response, 'Open shift is not at your assigned site')
        conflict = approved_time_off_request_for_date(conn, user['company_id'], user['id'], shift['shift_date'])
        if conflict:
            conn.close()
            return redirect_with_feedback(
                start_response,
                '/dashboard',
                error=approved_time_off_conflict_error(shift['shift_date'], conflict, user['full_name']),
            )
        overlap_conflict = overlapping_shift_for_guard(
            conn,
            user['company_id'],
            user['id'],
            shift['shift_date'],
            shift['start_time'],
            shift['end_time'],
            exclude_shift_id=shift['id'],
        )
        if overlap_conflict:
            conn.close()
            return redirect_with_feedback(
                start_response,
                '/dashboard',
                error=overlapping_shift_conflict_error(overlap_conflict, user['full_name']),
            )
        assignment_clause, assignment_cols = shift_assignment_update_clause(conn)
        if assignment_clause:
            conn.execute(f"UPDATE shifts SET {assignment_clause}, status='assigned' WHERE id=?", tuple([user['id']] * len(assignment_cols) + [shift_id]))
        else:
            conn.execute("UPDATE shifts SET status='assigned' WHERE id=?", (shift_id,))
        conn.commit(); conn.close(); log_audit('shift_edit', actor_user_id=user['id'], company_id=user['company_id'], target_type='shift', target_id=shift_id, message='open shift claimed', environ=environ); return redirect(start_response, '/dashboard')
    if path == '/report/new' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, files = parse_post(environ)
        required = ['report_type', 'report_date', 'report_time', 'site_id', 'summary']
        if not all(data.get(k) for k in required): return bad_request(start_response, 'Missing fields')
        attachment_name, attachment_path = save_upload(files.get('attachment'), 'attachments') if files.get('attachment') else (None, None)
        photo_name, photo_path = save_upload(files.get('photo'), 'photos') if files.get('photo') else (None, None)
        officer_name = user['full_name'] if user['role'] == 'guard' else data.get('officer_name', user['full_name'])
        conn = db(); now_str = utc_now_str(); conn.execute('INSERT INTO reports (company_id, report_type, report_date, report_time, site_id, officer_name, summary, status, priority, attachment_name, attachment_path, photo_name, photo_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (user['company_id'], data['report_type'], data['report_date'], data['report_time'], int(data['site_id']), officer_name, data['summary'], data.get('status', 'open'), data.get('priority', 'medium'), attachment_name, attachment_path, photo_name, photo_path, now_str, now_str)); report_id = _new_report_id(conn); conn.commit(); conn.close(); log_audit('report_created', actor_user_id=user['id'], company_id=user['company_id'], target_type='report', target_id=report_id, message='report submitted', environ=environ, metadata={'type': data['report_type']}); return redirect(start_response, '/dashboard')
    if path == '/report/update' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); report = conn.execute('SELECT * FROM reports WHERE id=? AND company_id=?', (data.get('report_id'), user['company_id'])).fetchone()
        if not report: conn.close(); return bad_request(start_response, 'Report not found')
        if not supervisor_can_access_site(conn, user, report['site_id']):
            conn.close(); return redirect_with_feedback(start_response, '/dashboard', error='Supervisor can only manage reports for assigned sites.')
        conn.execute('UPDATE reports SET status=?, priority=?, updated_at=? WHERE id=?', (data.get('status', report['status']), data.get('priority', report['priority']), utc_now_str(), report['id']))
        conn.commit(); conn.close(); log_audit('incident_updated', actor_user_id=user['id'], company_id=user['company_id'], target_type='report', target_id=report['id'], message='report status or priority updated', environ=environ, metadata={'status': data.get('status'), 'priority': data.get('priority')}); return redirect(start_response, '/dashboard')
    if path == '/checkpoint/new' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); conn.execute('INSERT INTO patrol_checkpoints (company_id, user_id, site_id, checkpoint_name, check_time, notes) VALUES (?, ?, ?, ?, ?, ?)', (user['company_id'], user['id'], data.get('site_id'), data.get('checkpoint_name'), data.get('check_time') or utc_now_str(), data.get('notes', ''))); conn.commit(); conn.close(); return redirect(start_response, '/dashboard')
    if path == '/swap/request' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); shift = conn.execute('SELECT * FROM shifts WHERE id=? AND company_id=?', (data.get('shift_id'), user['company_id'])).fetchone()
        if not shift or shift_assignment_value(shift) != user['id']:
            conn.close(); return bad_request(start_response, 'You can only swap your own assigned shifts')
        requested_to = data.get('requested_to') or None
        if requested_to:
            requested_guard = conn.execute("SELECT id FROM users WHERE id=? AND company_id=? AND role='guard'", (requested_to, user['company_id'])).fetchone()
            if not requested_guard:
                conn.close(); return bad_request(start_response, 'Requested guard not found')
        conn.execute("INSERT INTO shift_swap_requests (company_id, shift_id, requested_by, requested_to, status, notes, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)", (user['company_id'], data.get('shift_id'), user['id'], requested_to, data.get('notes', ''), utc_now_str())); conn.commit(); conn.close(); return redirect(start_response, '/dashboard')
    if path == '/swap/approve' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); req = conn.execute('SELECT * FROM shift_swap_requests WHERE id=? AND company_id=?', (data.get('request_id'), user['company_id'])).fetchone()
        if req:
            decision = data.get('decision')
            if decision == 'approved' and req['requested_to']:
                target_shift = conn.execute('SELECT id, shift_date, start_time, end_time FROM shifts WHERE id=? AND company_id=?', (req['shift_id'], user['company_id'])).fetchone()
                conflict = approved_time_off_request_for_date(conn, user['company_id'], req['requested_to'], target_shift['shift_date'] if target_shift else None)
                if conflict:
                    guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (req['requested_to'], user['company_id'])).fetchone()
                    conn.close()
                    return redirect_with_feedback(
                        start_response,
                        '/dashboard',
                        error=approved_time_off_conflict_error(target_shift['shift_date'], conflict, guard['full_name'] if guard else None),
                    )
                overlap_conflict = overlapping_shift_for_guard(
                    conn,
                    user['company_id'],
                    req['requested_to'],
                    target_shift['shift_date'] if target_shift else None,
                    target_shift['start_time'] if target_shift else None,
                    target_shift['end_time'] if target_shift else None,
                    exclude_shift_id=req['shift_id'],
                )
                if overlap_conflict:
                    guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (req['requested_to'], user['company_id'])).fetchone()
                    conn.close()
                    return redirect_with_feedback(
                        start_response,
                        '/dashboard',
                        error=overlapping_shift_conflict_error(overlap_conflict, guard['full_name'] if guard else None),
                    )
                assignment_clause, assignment_cols = shift_assignment_update_clause(conn)
                if assignment_clause:
                    conn.execute(f"UPDATE shifts SET {assignment_clause}, status='assigned' WHERE id=?", tuple([req['requested_to']] * len(assignment_cols) + [req['shift_id']]))
                else:
                    conn.execute("UPDATE shifts SET status='assigned' WHERE id=?", (req['shift_id'],))
                conn.execute("UPDATE shift_swap_requests SET status='approved' WHERE id=?", (req['id'],))
            else:
                conn.execute("UPDATE shift_swap_requests SET status='declined' WHERE id=?", (req['id'],))
            conn.commit(); log_audit('shift_edit', actor_user_id=user['id'], company_id=user['company_id'], target_type='shift', target_id=req['shift_id'], message=f'shift swap {decision}', environ=environ)
        conn.close(); return redirect(start_response, '/dashboard')
    if path == '/time-correction/request' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); shift = conn.execute('SELECT * FROM shifts WHERE id=? AND company_id=?', (data.get('shift_id'), user['company_id'])).fetchone()
        if not shift or shift_assignment_value(shift) != user['id']:
            conn.close(); return bad_request(start_response, 'You can only request corrections for your own assigned shifts')
        conn.execute('INSERT INTO time_corrections (company_id, shift_id, requested_by, requested_clock_in, requested_clock_out, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (user['company_id'], data.get('shift_id'), user['id'], data.get('requested_clock_in'), data.get('requested_clock_out'), data.get('reason', ''), utc_now_str())); conn.commit(); conn.close(); return redirect(start_response, '/dashboard')
    if path == '/time-correction/approve' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); req = conn.execute('SELECT * FROM time_corrections WHERE id=? AND company_id=?', (data.get('request_id'), user['company_id'])).fetchone()
        if req:
            shift = conn.execute('SELECT site_id FROM shifts WHERE id=? AND company_id=?', (req['shift_id'], user['company_id'])).fetchone()
            if shift and not supervisor_can_access_site(conn, user, shift['site_id']):
                conn.close(); return redirect_with_feedback(start_response, '/dashboard', error='Supervisor can only review time corrections for assigned sites.')
            decision = data.get('decision')
            if decision == 'approved':
                worked = calculate_worked_hours(req['requested_clock_in'], req['requested_clock_out'])
                conn.execute('UPDATE shifts SET clock_in_time=?, clock_out_time=?, worked_hours=?, overtime_alert=? WHERE id=?', (req['requested_clock_in'], req['requested_clock_out'], worked, 1 if worked > 8 else 0, req['shift_id']))
                conn.execute("UPDATE time_corrections SET status='approved' WHERE id=?", (req['id'],))
            else:
                conn.execute("UPDATE time_corrections SET status='declined' WHERE id=?", (req['id'],))
            conn.commit(); log_audit('shift_edit', actor_user_id=user['id'], company_id=user['company_id'], target_type='shift', target_id=req['shift_id'], message=f'time correction {decision}', environ=environ)
        conn.close(); return redirect(start_response, '/dashboard')
    if path == '/time-off/request' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return bad_request(start_response, 'Only guards can submit time off requests')
        data, _ = parse_post(environ)
        start_date = (data.get('start_date') or '').strip()
        end_date = (data.get('end_date') or '').strip()
        request_type = (data.get('type') or '').strip().lower()
        if not start_date or not end_date:
            return redirect_with_feedback(start_response, '/dashboard', error='Start date and end date are required for time off requests.')
        try:
            start_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return redirect_with_feedback(start_response, '/dashboard', error='Dates must use YYYY-MM-DD format.')
        if end_obj < start_obj:
            return redirect_with_feedback(start_response, '/dashboard', error='End date cannot be before start date.')
        if request_type not in {'paid', 'unpaid'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Type must be paid or unpaid.')
        now_str = utc_now_str()
        conn = db()
        duplicate = conn.execute('''
            SELECT id
            FROM time_off_requests
            WHERE company_id=? AND guard_id=? AND start_date=? AND end_date=? AND type=? AND status='pending'
        ''', (user['company_id'], user['id'], start_date, end_date, request_type)).fetchone()
        if duplicate:
            conn.close()
            return redirect_with_feedback(start_response, '/dashboard', error='A matching pending time off request already exists.')
        conn.execute("INSERT INTO time_off_requests (company_id, guard_id, start_date, end_date, type, reason, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)", (user['company_id'], user['id'], start_date, end_date, request_type, (data.get('reason') or '').strip() or None, now_str, now_str))
        conn.commit(); conn.close()
        log_audit('time_off_request_created', actor_user_id=user['id'], company_id=user['company_id'], target_type='time_off_request', target_id='new', message='time off request submitted', environ=environ, metadata={'start_date': start_date, 'end_date': end_date, 'type': request_type})
        return redirect_with_feedback(start_response, '/dashboard', message='Time off request submitted.')
    if path == '/time-off/approve' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        decision = (data.get('decision') or '').strip().lower()
        if decision not in {'approved', 'denied'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Decision must be approved or denied.')
        conn = db()
        req = conn.execute('SELECT * FROM time_off_requests WHERE id=? AND company_id=?', (data.get('request_id'), user['company_id'])).fetchone()
        if not req:
            conn.close(); return redirect_with_feedback(start_response, '/dashboard', error='Time off request not found.')
        if req['status'] != 'pending':
            conn.close(); return redirect_with_feedback(start_response, '/dashboard', error='Only pending requests can be reviewed.')
        reviewed_at = utc_now_str()
        review_note = (data.get('review_note') or '').strip() or None
        conn.execute('UPDATE time_off_requests SET status=?, updated_at=?, reviewed_at=?, reviewed_by=? WHERE id=?', (decision, reviewed_at, reviewed_at, user['id'], req['id']))
        conn.execute(
            'INSERT INTO time_off_review_logs (company_id, request_id, guard_id, reviewed_by, decision, review_note, reviewed_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (user['company_id'], req['id'], req['guard_id'], user['id'], decision, review_note, reviewed_at),
        )
        if decision == 'approved':
            impacted_shifts = conn.execute('''
                SELECT id, shift_date, start_time, end_time, site_id
                FROM shifts
                WHERE company_id=? AND COALESCE(user_id, guard_id)=? AND shift_date BETWEEN ? AND ?
            ''', (user['company_id'], req['guard_id'], req['start_date'], req['end_date'])).fetchall()
            assignment_clause, assignment_cols = shift_assignment_update_clause(conn)
            for shift in impacted_shifts:
                if assignment_clause:
                    conn.execute(f"UPDATE shifts SET {assignment_clause}, status='open' WHERE id=?", tuple([None] * len(assignment_cols) + [shift['id']]))
                else:
                    conn.execute("UPDATE shifts SET status='open' WHERE id=?", (shift['id'],))
                conn.execute(
                    "INSERT INTO open_shift_alerts (company_id, shift_id, source, message, created_at) VALUES (?, ?, 'time_off_approved', ?, ?)",
                    (user['company_id'], shift['id'], f"Shift opened due to approved time off for guard #{req['guard_id']}.", reviewed_at),
                )
        conn.commit(); conn.close()
        log_audit('time_off_request_reviewed', actor_user_id=user['id'], company_id=user['company_id'], target_type='time_off_request', target_id=req['id'], message=f'time off request {decision}', environ=environ)
        return redirect_with_feedback(start_response, '/dashboard', message=f"Time off request {decision}.")
    if path in {'/time-off/my', '/api/time-off/my'} and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return json_response(start_response, {'error': 'Only guards can view personal time off requests.'}, status='403 Forbidden')
        conn = db()
        rows = conn.execute('SELECT id, guard_id, start_date, end_date, type, reason, status, created_at, updated_at, reviewed_at, reviewed_by FROM time_off_requests WHERE company_id=? AND guard_id=? ORDER BY created_at DESC', (user['company_id'], user['id'])).fetchall()
        conn.close()
        return json_response(start_response, {'items': [dict(row) for row in rows]})
    if path in {'/time-off/all', '/api/time-off/all'} and method == 'GET':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        rows = conn.execute('''
            SELECT tor.id, tor.guard_id, u.full_name as guard_name, tor.start_date, tor.end_date, tor.type, tor.reason, tor.status, tor.created_at, tor.updated_at, tor.reviewed_at, tor.reviewed_by, reviewer.full_name as reviewed_by_name, reviewer.role as reviewed_by_role
            FROM time_off_requests tor
            JOIN users u ON tor.guard_id=u.id
            LEFT JOIN users reviewer ON tor.reviewed_by=reviewer.id
            WHERE tor.company_id=?
            ORDER BY tor.created_at DESC
        ''', (user['company_id'],)).fetchall()
        conn.close()
        return json_response(start_response, {'items': [dict(row) for row in rows]})
    if path == '/guards':
        user, response = require_admin(environ, start_response)
        if response: return response
        return app_page(environ, start_response, user, 'guards.html', active_path='/guards', view='week', title='Guards')
    if path == '/admin/guards/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        first_name = (data.get('first_name') or '').strip()
        last_name = (data.get('last_name') or '').strip()
        if not first_name or not last_name:
            return bad_request(start_response, 'First and last name are required')
        conn = db()
        try:
            insert_guard(
                conn,
                user['company_id'],
                first_name=first_name,
                last_name=last_name,
                phone=data.get('phone', ''),
                email=data.get('email', ''),
                license_number=data.get('license_number', ''),
                status=data.get('status', 'active') if data.get('status') in ('active', 'inactive') else 'active',
                rating=float(data.get('rating') or 5),
                training_status=data.get('training_status') or '',
                created_at=utc_now_str(),
            )
            new_row = conn.execute('SELECT * FROM guards WHERE company_id=? ORDER BY id DESC LIMIT 1', (user['company_id'],)).fetchone()
            new_id = new_row['id'] if new_row else None
            if new_row:
                upsert_guard_login(conn, new_row, guard_login_payload(data, new_row, user['company_id']))
            conn.commit()
        except ValueError as exc:
            conn.rollback(); conn.close(); return bad_request(start_response, str(exc))
        conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=new_id, message='guard profile created', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/update' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (data.get('guard_id'), user['company_id'])).fetchone()
        if not guard:
            conn.close(); return bad_request(start_response, 'Guard not found')
        guard_name, guard_first_name, guard_last_name = guard_name_parts(
            first_name=data.get('first_name') or guard['first_name'],
            last_name=data.get('last_name') or guard['last_name'],
        )
        guard_params = [guard_first_name, guard_last_name, data.get('phone', guard['phone'] or '').strip(), data.get('email', guard['email'] or '').strip(), data.get('license_number', guard['license_number'] or '').strip(), data.get('status', guard['status']) if data.get('status', guard['status']) in ('active', 'inactive') else guard['status'], data.get('training_status', guard['training_status'] or '').strip(), guard['id']]
        guard_sql = 'UPDATE guards SET first_name=?, last_name=?, phone=?, email=?, license_number=?, status=?, training_status=? WHERE id=?'
        if 'name' in column_names(conn, 'guards'):
            guard_sql = 'UPDATE guards SET name=?, first_name=?, last_name=?, phone=?, email=?, license_number=?, status=?, training_status=? WHERE id=?'
            guard_params.insert(0, guard_name)
        try:
            conn.execute(guard_sql, tuple(guard_params))
            save_guard_site_assignment(conn, user['company_id'], guard['id'], data.get('site_id'))
            updated_guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (guard['id'], user['company_id'])).fetchone()
            upsert_guard_login(conn, updated_guard, guard_login_payload(data, updated_guard, user['company_id']))
            conn.commit()
        except ValueError as exc:
            conn.rollback(); conn.close(); return bad_request(start_response, str(exc))
        conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=guard['id'], message='guard profile updated', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/deactivate' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (data.get('guard_id'), user['company_id'])).fetchone()
        if not guard:
            conn.close(); return bad_request(start_response, 'Guard not found')
        conn.execute("UPDATE guards SET status='inactive' WHERE id=? AND company_id=?", (guard['id'], user['company_id']))
        conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=guard['id'], message='guard deactivated', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/assign' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        conn = db(); guard = conn.execute('SELECT * FROM guards WHERE id=? AND company_id=?', (data.get('guard_id'), user['company_id'])).fetchone()
        if not guard:
            conn.close(); return bad_request(start_response, 'Guard not found')
        try:
            save_guard_site_assignment(conn, user['company_id'], guard['id'], data.get('site_id'))
            conn.commit()
        except ValueError as exc:
            conn.rollback(); conn.close(); return bad_request(start_response, str(exc))
        conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='guard', target_id=guard['id'], message='guard assignment updated', environ=environ)
        return redirect(start_response, '/guards')
    if path == '/admin/guard/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        requested_role = (data.get('role') or 'guard').strip().lower()
        role_map = {'guard': 'guard', 'supervisor': 'supervisor', 'admin': 'company_admin'}
        db_role = role_map.get(requested_role, 'guard')
        if user['role'] == 'supervisor' and db_role in {'company_admin', 'superadmin'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Supervisors cannot create admin users.')
        conn = db(); conn.execute("INSERT INTO users (company_id, username, password, full_name, role, phone, email, license_number, hourly_rate, active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)", (user['company_id'], data.get('username'), hash_password(data.get('password', 'password123')), data.get('full_name'), db_role, data.get('phone', ''), data.get('email', ''), data.get('license_number', ''), float(data.get('hourly_rate') or 18), utc_now_str()))
        new_row = conn.execute('SELECT id FROM users WHERE username=?', (data.get('username'),)).fetchone(); new_id = new_row['id'] if new_row else None
        if db_role == 'guard':
            for weekday in range(7): conn.execute("INSERT INTO availability (company_id, user_id, weekday, available_start, available_end, is_available) VALUES (?, ?, ?, '08:00', '20:00', 1)", (user['company_id'], new_id, weekday))
        conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='user', target_id=new_id, message='guard account created', environ=environ); return redirect(start_response, '/dashboard')
    if path == '/admin/user/update' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        role_map = {'guard': 'guard', 'supervisor': 'supervisor', 'admin': 'company_admin'}
        requested_role = (data.get('role') or '').strip().lower()
        if requested_role not in role_map:
            return bad_request(start_response, 'Invalid role')
        conn = db()
        target = conn.execute('SELECT id, role FROM users WHERE id=? AND company_id=?', (data.get('user_id'), user['company_id'])).fetchone()
        if not target:
            conn.close(); return bad_request(start_response, 'User not found')
        if user['role'] == 'supervisor' and role_map[requested_role] in {'company_admin', 'superadmin'}:
            conn.close(); return redirect_with_feedback(start_response, '/guards', error='Supervisors cannot create or promote admins.')
        conn.execute('UPDATE users SET role=? WHERE id=?', (role_map[requested_role], target['id']))
        if role_map[requested_role] == 'supervisor':
            conn.execute('DELETE FROM supervisor_site_assignments WHERE company_id=? AND supervisor_user_id=?', (user['company_id'], target['id']))
            sites = conn.execute('SELECT id FROM sites WHERE company_id=?', (user['company_id'],)).fetchall()
            for site in sites:
                if (data.get(f'supervisor_site_{site["id"]}') or '').strip():
                    conn.execute(
                        'INSERT INTO supervisor_site_assignments (company_id, supervisor_user_id, site_id, assigned_at) VALUES (?, ?, ?, ?)',
                        (user['company_id'], target['id'], site['id'], utc_now_str()),
                    )
        conn.commit(); conn.close()
        return redirect(start_response, '/guards')
    if path == '/admin/client/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); conn.execute('INSERT INTO clients (company_id, name, contact_name, contact_email, contact_phone, notes, active, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?)', (user['company_id'], data.get('name'), data.get('contact_name'), data.get('contact_email'), data.get('contact_phone'), data.get('notes'), utc_now_str())); conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='client', target_id=data.get('name'), message='client created', environ=environ); return redirect(start_response, '/dashboard')
    if path == '/admin/site/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); conn = db(); client_id = data.get('client_id') or None; client_name = data.get('client_company_name') or '';
        if client_id:
            client_row = conn.execute('SELECT id, name FROM clients WHERE id=? AND company_id=?', (client_id, user['company_id'])).fetchone();
            if client_row:
                client_id = client_row['id']; client_name = client_row['name']
            else:
                client_id = None
        conn.execute('INSERT INTO sites (company_id, client_id, name, client_company_name, address, notes, active) VALUES (?, ?, ?, ?, ?, ?, 1)', (user['company_id'], client_id, data.get('name'), client_name, data.get('address'), data.get('notes'))); conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='site', target_id=data.get('name'), message='site created', environ=environ); return redirect(start_response, '/dashboard')
    if path == '/patrol/checkpoint/qr' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        conn = db(); company_id = get_company_scope_id(user)
        checkpoint = conn.execute('''SELECT pc.*, pt.name AS tour_name, s.name AS site_name FROM patrol_tour_checkpoints pc JOIN patrol_tours pt ON pt.id=pc.tour_id JOIN sites s ON s.id=pc.site_id WHERE pc.id=? AND pc.company_id=?''', (query.get('id'), company_id)).fetchone()
        if not checkpoint: conn.close(); return not_found(start_response)
        allowed = user['role'] in {'superadmin', 'company_admin', 'admin', 'client'} or supervisor_can_access_site(conn, user, checkpoint['site_id'])
        conn.close()
        if not allowed: return redirect_with_feedback(start_response, '/dashboard', error='You do not have permission to view that checkpoint QR code.')
        qr_value = urllib.parse.quote(checkpoint['qr_code'])
        checkpoint_name = html.escape(str(checkpoint['checkpoint_name']))
        site_name = html.escape(str(checkpoint['site_name']))
        tour_name = html.escape(str(checkpoint['tour_name']))
        qr_code = html.escape(str(checkpoint['qr_code']))
        nfc_tag_id = html.escape(str(checkpoint['nfc_tag_id']))
        sort_order = html.escape(str(checkpoint['sort_order']))
        status_label = 'Active' if checkpoint['active'] else 'Inactive'
        body = f'''<!doctype html><html><head><meta charset="utf-8"><title>Checkpoint QR</title><link rel="stylesheet" href="/static/styles.css"><style>@media print {{ .no-print {{ display:none }} body {{ background:#fff; color:#111 }} .print-card {{ box-shadow:none; border-color:#111 }} }}</style></head><body class="simple-shell"><main class="narrow-shell"><section class="card print-card"><div class="section-head"><div><h1>{checkpoint_name}</h1><div class="small-muted">{site_name} · {tour_name}</div></div><button class="btn primary no-print" onclick="window.print()">Print QR</button></div><div class="qr-print-layout"><img class="qr-print-image" alt="QR for {checkpoint_name}" src="https://api.qrserver.com/v1/create-qr-code/?size=260x260&data={qr_value}"><div class="stack compact"><div><strong>QR Identifier</strong><br><code>{qr_code}</code></div><div><strong>NFC Identifier</strong><br><code>{nfc_tag_id}</code></div><div><strong>Sort Order</strong><br>{sort_order}</div><div><strong>Status</strong><br>{status_label}</div></div></div><div class="small-muted">If the QR image does not load, print the QR identifier text and use manual entry during testing.</div></section></main></body></html>'''.encode('utf-8')
        return html_response(start_response, body, extra_headers=csrf_headers(environ))
    if path == '/admin/patrol/tour/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        if user['role'] not in {'superadmin', 'company_admin', 'admin', 'supervisor'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Only admins and supervisors can create patrol tours.')
        data, _ = parse_post(environ); conn = db()
        site = conn.execute('SELECT * FROM sites WHERE id=? AND company_id=?', (data.get('site_id'), user['company_id'])).fetchone()
        if not site: conn.close(); return bad_request(start_response, 'Site not found')
        if not supervisor_can_access_site(conn, user, site['id']):
            conn.close(); return redirect_with_feedback(start_response, '/patrols', error='You do not have permission to create patrol tours for that site.')
        now = utc_now_str()
        tour_id = insert_and_get_id(conn, '''INSERT INTO patrol_tours (company_id, site_id, name, description, active, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?, ?)''', (user['company_id'], site['id'], data.get('name'), data.get('description', ''), user['id'], now, now))
        for index, name in enumerate([x.strip() for x in (data.get('checkpoints') or '').splitlines() if x.strip()], start=1):
            create_patrol_checkpoint(conn, user['company_id'], tour_id, site['id'], name, index)
        conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='patrol_tour', target_id=tour_id, message='patrol tour created', environ=environ)
        return redirect_with_feedback(start_response, f'/patrol/tour?id={tour_id}', message='Patrol tour created with QR and NFC checkpoints.')
    if path == '/admin/patrol/checkpoint/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        if user['role'] not in {'superadmin', 'company_admin', 'admin', 'supervisor'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Only admins and supervisors can edit patrol tours.')
        data, _ = parse_post(environ); conn = db(); company_id = get_company_scope_id(user)
        tour = conn.execute('SELECT * FROM patrol_tours WHERE id=? AND company_id=?', (data.get('tour_id'), company_id)).fetchone()
        if not tour: conn.close(); return bad_request(start_response, 'Tour not found')
        if not supervisor_can_access_site(conn, user, tour['site_id']):
            conn.close(); return redirect_with_feedback(start_response, '/patrols', error='You do not have permission to add checkpoints for that site.')
        try:
            create_patrol_checkpoint(conn, company_id, tour['id'], tour['site_id'], data.get('checkpoint_name') or 'Checkpoint', int(data.get('sort_order') or 0), data.get('qr_code'), data.get('nfc_tag_id'), data.get('active', '1'))
            conn.execute('UPDATE patrol_tours SET updated_at=? WHERE id=?', (utc_now_str(), tour['id'])); conn.commit(); message = 'Checkpoint added.'
        except Exception as exc:
            conn.rollback(); conn.close(); return redirect_with_feedback(start_response, f'/patrol/tour?id={tour["id"]}', error=f'Checkpoint could not be saved: {exc}')
        conn.close()
        return redirect_with_feedback(start_response, f'/patrol/tour?id={tour["id"]}', message=message)
    if path == '/admin/patrol/checkpoint/update' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        if user['role'] not in {'superadmin', 'company_admin', 'admin', 'supervisor'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Only admins and supervisors can edit patrol tours.')
        data, _ = parse_post(environ); conn = db(); company_id = get_company_scope_id(user)
        checkpoint = conn.execute('SELECT * FROM patrol_tour_checkpoints WHERE id=? AND company_id=?', (data.get('checkpoint_id'), company_id)).fetchone()
        if not checkpoint: conn.close(); return bad_request(start_response, 'Checkpoint not found')
        if not supervisor_can_access_site(conn, user, checkpoint['site_id']):
            conn.close(); return redirect_with_feedback(start_response, '/patrols', error='You do not have permission to edit checkpoints for that site.')
        try:
            conn.execute('''UPDATE patrol_tour_checkpoints SET checkpoint_name=?, sort_order=?, qr_code=?, nfc_tag_id=?, active=? WHERE id=? AND company_id=?''', ((data.get('checkpoint_name') or 'Checkpoint').strip(), int(data.get('sort_order') or 0), (data.get('qr_code') or checkpoint['qr_code']).strip(), (data.get('nfc_tag_id') or checkpoint['nfc_tag_id']).strip(), 1 if data.get('active') == '1' else 0, checkpoint['id'], company_id))
            conn.execute('UPDATE patrol_tours SET updated_at=? WHERE id=?', (utc_now_str(), checkpoint['tour_id'])); conn.commit()
        except Exception as exc:
            conn.rollback(); conn.close(); return redirect_with_feedback(start_response, f'/patrol/tour?id={checkpoint["tour_id"]}', error=f'Checkpoint could not be updated: {exc}')
        conn.close(); return redirect_with_feedback(start_response, f'/patrol/tour?id={checkpoint["tour_id"]}', message='Checkpoint updated.')
    if path == '/admin/patrol/checkpoint/delete' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        if user['role'] not in {'superadmin', 'company_admin', 'admin', 'supervisor'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Only admins and supervisors can delete patrol checkpoints.')
        data, _ = parse_post(environ); conn = db(); company_id = get_company_scope_id(user)
        checkpoint = conn.execute('SELECT * FROM patrol_tour_checkpoints WHERE id=? AND company_id=?', (data.get('checkpoint_id'), company_id)).fetchone()
        if not checkpoint: conn.close(); return bad_request(start_response, 'Checkpoint not found')
        if not supervisor_can_access_site(conn, user, checkpoint['site_id']):
            conn.close(); return redirect_with_feedback(start_response, '/patrols', error='You do not have permission to delete checkpoints for that site.')
        conn.execute('UPDATE patrol_tour_checkpoints SET active=0 WHERE id=? AND company_id=?', (checkpoint['id'], company_id))
        conn.execute('UPDATE patrol_tours SET updated_at=? WHERE id=?', (utc_now_str(), checkpoint['tour_id'])); conn.commit(); conn.close()
        return redirect_with_feedback(start_response, f'/patrol/tour?id={checkpoint["tour_id"]}', message='Checkpoint marked inactive.')
    if path == '/admin/patrol/checkpoint/generate-qr' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        if user['role'] not in {'superadmin', 'company_admin', 'admin', 'supervisor'}:
            return redirect_with_feedback(start_response, '/dashboard', error='Only admins and supervisors can generate checkpoint QR identifiers.')
        data, _ = parse_post(environ); conn = db(); company_id = get_company_scope_id(user)
        checkpoint = conn.execute('SELECT * FROM patrol_tour_checkpoints WHERE id=? AND company_id=?', (data.get('checkpoint_id'), company_id)).fetchone()
        if not checkpoint: conn.close(); return bad_request(start_response, 'Checkpoint not found')
        if not supervisor_can_access_site(conn, user, checkpoint['site_id']):
            conn.close(); return redirect_with_feedback(start_response, '/patrols', error='You do not have permission to update checkpoints for that site.')
        new_qr = patrol_token('QR')
        conn.execute('UPDATE patrol_tour_checkpoints SET qr_code=? WHERE id=? AND company_id=?', (new_qr, checkpoint['id'], company_id))
        conn.execute('UPDATE patrol_tours SET updated_at=? WHERE id=?', (utc_now_str(), checkpoint['tour_id'])); conn.commit(); conn.close()
        return redirect_with_feedback(start_response, f'/patrol/tour?id={checkpoint["tour_id"]}', message='QR identifier generated.')
    if path == '/patrol/note' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); note = (data.get('note') or '').strip()
        if not note:
            return redirect_with_feedback(start_response, f'/patrol/run?id={data.get("run_id") or ""}', error='A note is required.')
        conn = db(); company_id = get_company_scope_id(user); run = conn.execute('SELECT * FROM patrol_tour_runs WHERE id=? AND company_id=?', (data.get('run_id'), company_id)).fetchone()
        if not run: conn.close(); return bad_request(start_response, 'Patrol run not found')
        allowed = (user['role'] == 'guard' and run['guard_id'] == user['id'] and supervisor_can_access_site(conn, user, run['site_id'])) or admin_can_review_patrol(conn, user, run)
        if not allowed:
            conn.close(); return redirect_with_feedback(start_response, '/dashboard', error='You do not have permission to add notes to that patrol.')
        note_type = 'Guard explanation' if user['role'] == 'guard' else 'Admin/Supervisor note'
        patrol_event(conn, company_id, run['id'], 'note_added', 'Note/reason added', f'{note_type}: {note}', actor_user_id=user['id'])
        conn.commit(); conn.close(); return redirect_with_feedback(start_response, f'/patrol/run?id={run["id"]}', message='Patrol note added.')
    if path == '/admin/patrol/review' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ); action = (data.get('action') or '').strip().lower(); reason = (data.get('reason') or '').strip(); note = (data.get('note') or '').strip()
        if action not in {'excuse', 'missed'}:
            return redirect_with_feedback(start_response, '/patrols', error='Choose Mark Excused or Keep as Missed.')
        if reason not in PATROL_EXCUSE_REASONS or not note:
            return redirect_with_feedback(start_response, '/patrols', error='A valid reason and admin/supervisor note are required.')
        conn = db(); company_id = get_company_scope_id(user); run = conn.execute('SELECT * FROM patrol_tour_runs WHERE id=? AND company_id=?', (data.get('run_id'), company_id)).fetchone()
        if not run: conn.close(); return bad_request(start_response, 'Patrol run not found')
        if not admin_can_review_patrol(conn, user, run):
            conn.close(); return redirect_with_feedback(start_response, '/patrols', error='You do not have permission to review that patrol.')
        if row_value(run, 'status') == 'completed':
            conn.close(); return redirect_with_feedback(start_response, f'/patrol/run?id={run["id"]}', error='Completed patrols cannot be excused.')
        now = utc_now_str()
        if action == 'excuse':
            conn.execute("UPDATE patrol_tour_runs SET status='excused', excused_reason=?, excused_note=?, excused_by=?, excused_at=?, completed_at=COALESCE(completed_at, ?), missed_checkpoint_count=0 WHERE id=? AND company_id=?", (reason, note, user['id'], now, now, run['id'], company_id))
            patrol_event(conn, company_id, run['id'], 'patrol_excused', 'Patrol excused by admin/supervisor', note, reason, user['id'], now)
            message = 'Patrol marked excused and removed from missed patrol calculations.'
        else:
            conn.execute("UPDATE patrol_tour_runs SET status='missed', completed_at=COALESCE(completed_at, ?) WHERE id=? AND company_id=?", (now, run['id'], company_id))
            patrol_event(conn, company_id, run['id'], 'patrol_missed', 'Patrol incomplete', 'Kept as missed: ' + note, reason, user['id'], now)
            message = 'Patrol kept as missed.'
        patrol_event(conn, company_id, run['id'], 'note_added', 'Note/reason added', note, reason, user['id'], now)
        conn.commit(); conn.close(); return redirect_with_feedback(start_response, f'/patrol/run?id={run["id"]}', message=message)
    if path == '/patrol/start' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard': return redirect_with_feedback(start_response, '/dashboard', error='Only guards can start patrol tours.')
        data, _ = parse_post(environ); conn = db()
        tour = conn.execute('SELECT * FROM patrol_tours WHERE id=? AND company_id=? AND active=1', (data.get('tour_id'), user['company_id'])).fetchone()
        if not tour: conn.close(); return bad_request(start_response, 'Tour not found')
        if not supervisor_can_access_site(conn, user, tour['site_id']): conn.close(); return bad_request(start_response, 'Tour is not at your assigned site')
        now = utc_now_str()
        run_id = insert_and_get_id(conn, '''INSERT INTO patrol_tour_runs (company_id, site_id, tour_id, guard_id, status, started_at, missed_checkpoint_count) VALUES (?, ?, ?, ?, 'in_progress', ?, 0)''', (user['company_id'], tour['site_id'], tour['id'], user['id'], now))
        patrol_event(conn, user['company_id'], run_id, 'patrol_assigned', 'Patrol assigned', 'Patrol route assigned when the guard started the tour.', actor_user_id=user['id'], created_at=now)
        patrol_event(conn, user['company_id'], run_id, 'patrol_started', 'Patrol started', '', actor_user_id=user['id'], created_at=now)
        conn.commit(); conn.close(); return redirect(start_response, f'/patrol/run?id={run_id}')
    if path == '/patrol/scan' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard': return redirect_with_feedback(start_response, '/dashboard', error='Only guards can scan patrol checkpoints.')
        data, _ = parse_post(environ); method_value = (data.get('scan_method') or '').strip().upper()
        if method_value not in {'QR', 'NFC', 'MANUAL'}: return bad_request(start_response, 'Scan method must be QR, NFC, or MANUAL')
        conn = db(); run = conn.execute("SELECT * FROM patrol_tour_runs WHERE id=? AND company_id=? AND guard_id=? AND status='in_progress'", (data.get('run_id'), user['company_id'], user['id'])).fetchone()
        if not run: conn.close(); return bad_request(start_response, 'Active patrol run not found')
        checkpoint = conn.execute('SELECT * FROM patrol_tour_checkpoints WHERE id=? AND company_id=? AND tour_id=? AND active=1', (data.get('checkpoint_id'), user['company_id'], run['tour_id'])).fetchone()
        if not checkpoint: conn.close(); return bad_request(start_response, 'Checkpoint not found')
        expected = checkpoint['qr_code'] if method_value == 'QR' else checkpoint['nfc_tag_id']
        if method_value != 'MANUAL' and (data.get('scan_value') or '').strip() != expected:
            conn.close(); return redirect_with_feedback(start_response, f'/patrol/run?id={run["id"]}', error='Scanned QR/NFC value does not match this checkpoint.')
        local_uuid = local_submission_uuid(data.get('local_uuid'))
        existing = first_patrol_scan_by_local_uuid(conn, user['company_id'], user['id'], local_uuid) or conn.execute('SELECT id FROM patrol_checkpoint_scans WHERE tour_run_id=? AND checkpoint_id=?', (run['id'], checkpoint['id'])).fetchone()
        if not existing:
            device_timestamp = (data.get('device_timestamp') or utc_now_str()).strip()
            conn.execute('''INSERT INTO patrol_checkpoint_scans (company_id, site_id, tour_id, tour_run_id, checkpoint_id, guard_id, scan_method, scanned_at, gps_latitude, gps_longitude, missed_checkpoint, local_uuid, device_timestamp, synced_at, offline_submitted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 0)''', (user['company_id'], run['site_id'], run['tour_id'], run['id'], checkpoint['id'], user['id'], method_value, device_timestamp, data.get('gps_latitude', ''), data.get('gps_longitude', ''), local_uuid, device_timestamp, utc_now_str()))
            patrol_event(conn, user['company_id'], run['id'], 'checkpoint_scanned', 'Checkpoint scanned', checkpoint['checkpoint_name'], actor_user_id=user['id'], created_at=device_timestamp)
        conn.commit(); conn.close(); return redirect_with_feedback(start_response, f'/patrol/run?id={run["id"]}', message='Checkpoint scan logged.')
    if path == '/patrol/complete' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard': return redirect_with_feedback(start_response, '/dashboard', error='Only guards can complete patrol tours.')
        data, _ = parse_post(environ); conn = db(); run = conn.execute("SELECT * FROM patrol_tour_runs WHERE id=? AND company_id=? AND guard_id=? AND status='in_progress'", (data.get('run_id'), user['company_id'], user['id'])).fetchone()
        if not run: conn.close(); return bad_request(start_response, 'Active patrol run not found')
        missed = 0
        for checkpoint in conn.execute('SELECT * FROM patrol_tour_checkpoints WHERE company_id=? AND tour_id=? AND active=1', (user['company_id'], run['tour_id'])).fetchall():
            if not conn.execute('SELECT id FROM patrol_checkpoint_scans WHERE tour_run_id=? AND checkpoint_id=?', (run['id'], checkpoint['id'])).fetchone():
                missed += 1; missed_at = utc_now_str(); conn.execute('''INSERT INTO patrol_checkpoint_scans (company_id, site_id, tour_id, tour_run_id, checkpoint_id, guard_id, scan_method, scanned_at, gps_latitude, gps_longitude, missed_checkpoint) VALUES (?, ?, ?, ?, ?, ?, 'MISSED', ?, '', '', 1)''', (user['company_id'], run['site_id'], run['tour_id'], run['id'], checkpoint['id'], user['id'], missed_at)); patrol_event(conn, user['company_id'], run['id'], 'patrol_incomplete', 'Patrol incomplete', checkpoint['checkpoint_name'], actor_user_id=user['id'], created_at=missed_at)
        conn.execute("UPDATE patrol_tour_runs SET status='completed', completed_at=?, missed_checkpoint_count=? WHERE id=?", (utc_now_str(), missed, run['id'])); conn.commit(); conn.close()
        return redirect_with_feedback(start_response, f'/patrol/run?id={run["id"]}', message='Patrol tour completed.')
    if path == '/patrol/run' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        conn = db(); company_id = get_company_scope_id(user); run, checkpoints = patrol_run_detail(conn, company_id, query.get('id'))
        if not run: conn.close(); return not_found(start_response)
        allowed = user['role'] in {'superadmin', 'company_admin', 'admin', 'client'} or (user['role'] == 'guard' and run['guard_id'] == user['id'] and supervisor_can_access_site(conn, user, run['site_id'])) or supervisor_can_access_site(conn, user, run['site_id'])
        if not allowed:
            conn.close(); return redirect_with_feedback(start_response, '/dashboard', error='You do not have permission to view that patrol run.')
        timeline = patrol_timeline(conn, run, checkpoints)
        can_review = admin_can_review_patrol(conn, user, run)
        conn.close()
        context = get_dashboard_context(user)
        context.update({'run': run, 'checkpoints': checkpoints, 'patrol_timeline': timeline, 'patrol_excuse_reasons': PATROL_EXCUSE_REASONS, 'can_review_patrol': can_review, 'nav_items': sidebar_nav_items(user, '/patrol/run'), 'active_path': '/patrol/run', 'page_title': 'Patrol Run'})
        return html_response(start_response, render_page(environ, 'patrol_run.html', title='Patrol Run', user=user, **context), extra_headers=csrf_headers(environ))
    if path == '/patrol/tour' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        conn = db(); company_id = get_company_scope_id(user)
        tour, checkpoints = patrol_tour_detail(conn, company_id, query.get('id'))
        if not tour: conn.close(); return not_found(start_response)
        allowed = user['role'] in {'superadmin', 'company_admin', 'admin'} or supervisor_can_access_site(conn, user, tour['site_id'])
        conn.close()
        if not allowed: return redirect_with_feedback(start_response, '/dashboard', error='You do not have permission to view that patrol tour.')
        context = get_dashboard_context(user)
        context.update({'tour': tour, 'checkpoints': checkpoints, 'nav_items': sidebar_nav_items(user, '/patrol/tour'), 'active_path': '/patrol/tour', 'page_title': 'Patrol Tour'})
        return html_response(start_response, render_page(environ, 'patrol_tour.html', title='Patrol Tour', user=user, **context), extra_headers=csrf_headers(environ))
    if path == '/admin/shift/new' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        redirect_location = dashboard_shift_form_location(data)
        scheduled_hours = calculate_shift_hours_from_strings(data.get('start_time'), data.get('end_time'))
        user_id = data.get('user_id') or None
        status = 'assigned' if user_id else 'open'
        conn = db()
        if user_id:
            conflict = approved_time_off_request_for_date(conn, user['company_id'], user_id, data.get('shift_date'))
            if conflict:
                guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (user_id, user['company_id'])).fetchone()
                conn.close()
                return redirect_with_feedback(
                    start_response,
                    redirect_location,
                    error=approved_time_off_conflict_error(data.get('shift_date'), conflict, guard['full_name'] if guard else None),
                )
            overlap_conflict = overlapping_shift_for_guard(
                conn,
                user['company_id'],
                user_id,
                data.get('shift_date'),
                data.get('start_time'),
                data.get('end_time'),
            )
            if overlap_conflict:
                guard = conn.execute('SELECT full_name FROM users WHERE id=? AND company_id=?', (user_id, user['company_id'])).fetchone()
                conn.close()
                return redirect_with_feedback(
                    start_response,
                    redirect_location,
                    error=overlapping_shift_conflict_error(overlap_conflict, guard['full_name'] if guard else None),
                )
        shift_sql, shift_params = shift_insert_sql_and_params(
            conn,
            ['company_id', 'site_id', 'shift_date', 'start_time', 'end_time', 'status', 'scheduled_hours', 'worked_hours', 'overtime_alert', 'notes'],
            [user['company_id'], data.get('site_id'), data.get('shift_date'), data.get('start_time'), data.get('end_time'), status, scheduled_hours, 0, 0, data.get('notes', '')],
            user_id,
        )
        conn.execute(shift_sql, shift_params)
        conn.commit()
        conn.close()
        log_audit('shift_edit', actor_user_id=user['id'], company_id=user['company_id'], target_type='shift', target_id='new', message='shift created', environ=environ, metadata={'site_id': data.get('site_id'), 'shift_date': data.get('shift_date')})
        return redirect(start_response, '/dashboard')
    if path == '/admin/company/logo' and method == 'GET':
        user, response = require_admin(environ, start_response)
        if response: return response
        return app_page(environ, start_response, user, 'admin_company_logo.html', active_path='/admin/company/logo', view='week', title='Company Logo')
    if path == '/admin/company/logo' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        _data, files = parse_post(environ)
        logo_file = first_uploaded_file(files, 'logo')
        if not logo_file:
            return redirect_with_feedback(start_response, '/admin/company/logo', error='Choose a logo image to upload.')
        if not is_allowed_company_logo(logo_file):
            return redirect_with_feedback(start_response, '/admin/company/logo', error='Logo must be a valid PNG, JPG, GIF, WebP, or SVG image.')
        _original_name, logo_path = save_upload(logo_file, 'logos')
        if logo_path:
            conn = db(); conn.execute('UPDATE companies SET logo_path=? WHERE id=?', (logo_path, user['company_id'])); conn.commit(); conn.close(); log_audit('admin_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='company', target_id=user['company_id'], message='company logo updated', environ=environ, metadata={'logo_path': logo_path})
            return redirect_with_feedback(start_response, '/admin/company/logo', message='Company logo updated successfully.')
        return redirect_with_feedback(start_response, '/admin/company/logo', error='Logo upload failed. Check the file size and try again.')
    if path == '/admin/patrol/export/history.csv':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        try:
            csv_data = patrol_history_csv(conn, user)
        finally:
            conn.close()
        start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_patrol_history.csv"')], 'text/csv; charset=utf-8'))
        return [csv_data]
    if path == '/admin/patrol/export/missed-checkpoints.csv':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        try:
            csv_data = missed_checkpoints_csv(conn, user)
        finally:
            conn.close()
        start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_missed_checkpoints.csv"')], 'text/csv; charset=utf-8'))
        return [csv_data]
    if path == '/admin/reports/export':
        user, response = require_admin(environ, start_response)
        if response: return response
        pdf = export_reports_pdf(user['company_id']); log_audit('reports_exported_pdf', actor_user_id=user['id'], company_id=user['company_id'], target_type='report', target_id='export', message='PDF report export generated', environ=environ); start_response('200 OK', response_headers([('Content-Disposition', 'attachment; filename="steeleops_reports.pdf"')], 'application/pdf')); return [pdf]
    if path == '/admin/reports/manage' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user.get('role') not in {'company_admin', 'superadmin', 'admin', 'supervisor'}:
            return redirect_with_feedback(start_response, '/reports', error='Only supervisors/admins can manage reports.')
        data, _ = parse_post(environ)
        report_kind = (data.get('report_kind') or '').strip()
        table_name = 'daily_activity_reports' if report_kind == 'daily_activity' else 'incident_reports' if report_kind == 'incident' else ''
        if not table_name:
            return bad_request(start_response, 'Invalid report type.')
        report_id = data.get('report_id')
        new_status = (data.get('status') or '').strip()
        if new_status not in report_status_options():
            return bad_request(start_response, 'Invalid status.')
        conn = db()
        row = conn.execute(f'SELECT * FROM {table_name} WHERE id=? AND company_id=?', (report_id, user['company_id'])).fetchone()
        if not row:
            conn.close(); return bad_request(start_response, 'Report not found.')
        if user.get('role') == 'supervisor' and not supervisor_can_access_site(conn, user, row['site_id']):
            conn.close(); return redirect_with_feedback(start_response, '/reports', error='Supervisors can only manage reports for assigned sites.')
        old_status = row_value(row, 'status') or ''
        now = utc_now_str()
        supervisor_note = (data.get('supervisor_note') or '').strip()
        admin_note = (data.get('admin_note') or '').strip()
        resolved_at_value = row_value(row, 'resolved_at')
        if new_status.lower() == 'closed' and str(old_status).lower() != 'closed':
            resolved_at_value = now
        elif new_status.lower() != 'closed':
            resolved_at_value = None
        conn.execute(f'UPDATE {table_name} SET status=?, resolved_at=? WHERE id=?', (new_status, resolved_at_value, report_id))
        if new_status != old_status:
            conn.execute('INSERT INTO report_status_history (company_id, report_kind, report_id, old_status, new_status, changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (user['company_id'], report_kind, report_id, old_status, new_status, user['id'], now))
        if supervisor_note:
            conn.execute(
                'INSERT INTO report_notes (company_id, report_kind, report_id, note_text, note_type, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (user['company_id'], report_kind, report_id, supervisor_note, 'supervisor', user['id'], now),
            )
            merged_supervisor_notes = ((row_value(row, 'supervisor_notes') or '').strip() + '\n' + f'[{now}] {supervisor_note}').strip()
            conn.execute(f'UPDATE {table_name} SET supervisor_notes=? WHERE id=?', (merged_supervisor_notes, report_id))
        if admin_note:
            conn.execute(
                'INSERT INTO report_notes (company_id, report_kind, report_id, note_text, note_type, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (user['company_id'], report_kind, report_id, admin_note, 'admin', user['id'], now),
            )
            merged_admin_notes = ((row_value(row, 'admin_notes') or '').strip() + '\n' + f'[{now}] {admin_note}').strip()
            conn.execute(f'UPDATE {table_name} SET admin_notes=? WHERE id=?', (merged_admin_notes, report_id))
        conn.commit(); conn.close()
        return redirect_with_feedback(start_response, '/reports', message='Report updated.')
    if path == '/reports':
        user, response = require_login(environ, start_response)
        if response: return response
        if user.get('role') == 'guard':
            return redirect_with_feedback(start_response, '/guard/my-reports', message='Guards can submit reports and view their own submissions from My Reports.')
        conn = db()
        report_context = report_management_context(conn, user, parse_query(environ))
        conn.close()
        return app_page(environ, start_response, user, 'reports.html', active_path='/reports', view='week', title='Reports', **report_context)
    report_file_match = re.match(r'^/report-files/([a-zA-Z_-]+)/(\d+)/(photo|attachment)(?:/(\d+))?$', path)
    if report_file_match and method == 'GET':
        user, response = require_login(environ, start_response)
        if response:
            return response
        report_kind_raw = report_file_match.group(1)
        report_kind = {
            'incident': 'incident',
            'incident_report': 'incident',
            'incident-report': 'incident',
            'daily_activity': 'daily_activity',
            'daily_activity_report': 'daily_activity',
            'daily-activity-report': 'daily_activity',
            'daily-activity': 'daily_activity',
            'dar': 'daily_activity',
        }.get((report_kind_raw or '').strip().lower())
        if not report_kind:
            return bad_request(start_response, 'Invalid report type.')
        report_id = int(report_file_match.group(2))
        field_name = report_file_match.group(3)
        attachment_id = int(report_file_match.group(4)) if report_file_match.group(4) else None
        if report_kind == 'daily_activity' and field_name not in {'photo', 'attachment'}:
            return bad_request(start_response, 'Invalid file request.')
        if report_kind == 'incident' and field_name != 'attachment':
            return bad_request(start_response, 'Invalid file request.')
        conn = db()
        if report_kind == 'daily_activity':
            row = conn.execute(
                'SELECT company_id, site_id, officer_id, photo_path FROM daily_activity_reports WHERE id=?',
                (report_id,),
            ).fetchone()
            # Daily activity reports store uploads in photo_path only.
            # Keep `/attachment` path compatibility by resolving to the same column.
            file_path_value = row.get('photo_path') if row else None
        else:
            row = conn.execute(
                'SELECT company_id, site_id, officer_id, attachment_path FROM incident_reports WHERE id=?',
                (report_id,),
            ).fetchone()
            file_path_value = row.get('attachment_path') if row else None
        if not row:
            conn.close()
            return bad_request(start_response, f'Report file unavailable: report not found for type "{report_kind}".')
        if report_kind_raw.strip().lower() in {'incident', 'incident_report', 'incident-report'} and report_kind != 'incident':
            conn.close()
            return bad_request(start_response, 'Report file unavailable: report type mismatch.')
        if (report_kind_raw or '').strip().lower() in {'daily_activity', 'daily_activity_report', 'daily-activity', 'daily-activity-report', 'dar'} and report_kind != 'daily_activity':
            conn.close()
            return bad_request(start_response, 'Report file unavailable: report type mismatch.')
        if attachment_id:
            attachment_row = conn.execute(
                'SELECT * FROM report_attachments WHERE id=? AND report_type=? AND report_id=?',
                (attachment_id, report_kind, report_id),
            ).fetchone()
            if attachment_row:
                file_path_value = attachment_row['stored_path']
        if not file_path_value:
            conn.close()
            return bad_request(start_response, f'Report file unavailable: attachment path missing for report #{report_id}.')
        if user['role'] == 'guard':
            if int(row['officer_id']) != int(user['id']) or int(row['company_id']) != int(user['company_id']):
                conn.close()
                return forbidden(start_response, 'Access denied.')
        elif user['role'] == 'supervisor':
            if int(row['company_id']) != int(user['company_id']) or not supervisor_can_access_site(conn, user, row['site_id']):
                conn.close()
                return forbidden(start_response, 'Access denied.')
        elif user['role'] in {'company_admin', 'admin'}:
            if int(row['company_id']) != int(user['company_id']):
                conn.close()
                return forbidden(start_response, 'Access denied.')
        elif user['role'] == 'superadmin':
            pass
        else:
            conn.close()
            return forbidden(start_response, 'Access denied.')
        conn.close()
        local_file_path = local_path_from_upload(file_path_value)
        if not local_file_path:
            return bad_request(start_response, 'Report file unavailable: invalid stored file path.')
        if not os.path.isfile(local_file_path):
            return redirect_with_feedback(
                start_response,
                '/reports' if user['role'] in {'company_admin', 'admin', 'superadmin', 'supervisor'} else '/guard/my-reports',
                error='This attachment is no longer available in storage. Existing legacy files may be unavailable after a restart/deploy.',
            )
        disposition = 'attachment' if query.get('download') == '1' else 'inline'
        filename = os.path.basename(local_file_path)
        headers = [('Content-Type', upload_content_type(file_path_value)), ('Content-Disposition', f'{disposition}; filename="{filename}"')]
        start_response('200 OK', response_headers(headers))
        with open(local_file_path, 'rb') as f:
            return [f.read()]
    if path == '/guard/daily-activity-reports' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect_with_feedback(start_response, '/dashboard', error='Only guards can access Daily Activity Reports.')
        conn = db()
        assigned_site = guard_primary_assigned_site(conn, user, preferred_site_id=row_value(user, 'session_site_id'))
        dar_reports = conn.execute('''
            SELECT d.*, s.name as site_name FROM daily_activity_reports d
            JOIN sites s ON d.site_id=s.id
            WHERE d.company_id=? AND d.officer_id=?
            ORDER BY d.created_at DESC LIMIT 20
        ''', (user['company_id'], user['id'])).fetchall()
        conn.close()
        return app_page(environ, start_response, user, 'guard_daily_activity_reports.html', active_path='/guard/daily-activity-reports', view='week', title='Daily Activity Reports', assigned_site=assigned_site, server_now=utc_now_str(), dar_reports=dar_reports)
    if path == '/guard/daily-activity-reports/new' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return bad_request(start_response, 'Only guards can submit Daily Activity Reports.')
        data, files = parse_post(environ)
        if not data.get('activity_type') or not data.get('summary'):
            return bad_request(start_response, 'Activity type and summary are required.')
        conn = db()
        assigned_site = guard_primary_assigned_site(conn, user, preferred_site_id=row_value(user, 'session_site_id'))
        if not assigned_site:
            conn.close()
            return redirect_with_feedback(start_response, '/guard/daily-activity-reports', error='No assigned site found for your account.')
        dar_files = collect_attachments(files, 'photo')
        insert_daily_activity_report(conn, user, assigned_site['site_id'], data, dar_files, offline_submitted=0)
        conn.commit()
        conn.close()
        return redirect_with_feedback(start_response, '/guard/daily-activity-reports', message='Daily activity report submitted.')
    if path == '/guard/my-reports' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect(start_response, '/dashboard')
        q = (query.get('q') or '').strip().lower()
        report_type_filter = (query.get('report_type') or '').strip().lower()
        site_id_filter = (query.get('site_id') or '').strip()
        date_from = (query.get('date_from') or '').strip()
        date_to = (query.get('date_to') or '').strip()
        conn = db()
        filter_sites = conn.execute('''
            SELECT DISTINCT s.id, s.name
            FROM sites s
            JOIN (
                SELECT site_id FROM daily_activity_reports WHERE company_id=? AND officer_id=?
                UNION
                SELECT site_id FROM incident_reports WHERE company_id=? AND officer_id=?
            ) used_sites ON used_sites.site_id=s.id
            ORDER BY s.name
        ''', (user['company_id'], user['id'], user['company_id'], user['id'])).fetchall()
        daily_reports = conn.execute('''
            SELECT d.id as report_id, d.activity_type, d.summary, d.status, d.created_at, d.site_id, d.photo_path, s.name as site_name, 'Daily Activity Report' as report_type, 'daily_activity' as report_kind, NULL as priority, NULL as incident_type, NULL as narrative, NULL as persons_involved, NULL as witnesses, 0 as police_notified, 0 as client_notified, NULL as attachment_path
            FROM daily_activity_reports d
            JOIN sites s ON d.site_id=s.id
            WHERE d.company_id=? AND d.officer_id=?
        ''', (user['company_id'], user['id'])).fetchall()
        incident_reports = conn.execute('''
            SELECT i.id as report_id, NULL as activity_type, NULL as summary, i.status, i.created_at, i.site_id, NULL as photo_path, s.name as site_name, 'Incident Report' as report_type, 'incident' as report_kind, i.priority, i.incident_type, i.narrative, i.persons_involved, i.witnesses, i.police_notified, i.client_notified, i.attachment_path
            FROM incident_reports i
            JOIN sites s ON i.site_id=s.id
            WHERE i.company_id=? AND i.officer_id=?
        ''', (user['company_id'], user['id'])).fetchall()
        my_reports = []
        for row in list(daily_reports) + list(incident_reports):
            haystack = ' '.join([
                str(row.get('report_type') or ''),
                str(row.get('site_name') or ''),
                str(row.get('status') or ''),
                str(row.get('activity_type') or ''),
                str(row.get('summary') or ''),
                str(row.get('incident_type') or ''),
                str(row.get('narrative') or ''),
            ]).lower()
            if q and q not in haystack:
                continue
            if report_type_filter and row.get('report_kind') != report_type_filter:
                continue
            if site_id_filter and str(row.get('site_id')) != site_id_filter:
                continue
            row_date = str(row.get('created_at') or '')[:10]
            if date_from and row_date < date_from:
                continue
            if date_to and row_date > date_to:
                continue
            preview = (row.get('narrative') or row.get('summary') or '').strip()
            row['preview'] = preview[:180] + ('…' if len(preview) > 180 else '')
            my_reports.append(row)
        my_reports = sorted(my_reports, key=lambda x: x['created_at'], reverse=True)
        conn.close()
        filters = {'q': q, 'report_type': report_type_filter, 'site_id': site_id_filter, 'date_from': date_from, 'date_to': date_to}
        return app_page(environ, start_response, user, 'guard_my_reports.html', active_path='/guard/my-reports', view='week', title='My Reports', my_reports=my_reports, filter_sites=filter_sites, filters=filters)
    if path.startswith('/guard/my-reports/') and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect(start_response, '/dashboard')
        parts = [p for p in path.split('/') if p]
        if len(parts) != 4:
            return bad_request(start_response, 'Invalid report path')
        report_kind, report_id = parts[2], parts[3]
        conn = db()
        report = None
        if report_kind == 'daily_activity':
            report = conn.execute('''
                SELECT d.id as report_id, d.activity_type, d.summary, d.status, d.created_at, d.photo_path, s.name as site_name, 'Daily Activity Report' as report_type, 'daily_activity' as report_kind, NULL as priority, NULL as incident_type, NULL as narrative, NULL as persons_involved, NULL as witnesses, 0 as police_notified, 0 as client_notified, NULL as attachment_path
                FROM daily_activity_reports d JOIN sites s ON s.id=d.site_id
                WHERE d.id=? AND d.company_id=? AND d.officer_id=?
            ''', (report_id, user['company_id'], user['id'])).fetchone()
        elif report_kind == 'incident':
            report = conn.execute('''
                SELECT i.id as report_id, NULL as activity_type, NULL as summary, i.status, i.created_at, NULL as photo_path, s.name as site_name, 'Incident Report' as report_type, 'incident' as report_kind, i.priority, i.incident_type, i.narrative, i.persons_involved, i.witnesses, i.police_notified, i.client_notified, i.attachment_path
                FROM incident_reports i JOIN sites s ON s.id=i.site_id
                WHERE i.id=? AND i.company_id=? AND i.officer_id=?
            ''', (report_id, user['company_id'], user['id'])).fetchone()
        if not report:
            conn.close()
            return redirect_with_feedback(start_response, '/guard/my-reports', error='Report not found or access denied.')
        report = dict(report)
        report['attachments'] = fetch_report_attachments(conn, report_kind, report['report_id'])
        conn.close()
        return app_page(environ, start_response, user, 'guard_my_report_detail.html', active_path='/guard/my-reports', view='week', title='Report Detail', report=report)
    if path == '/guard/incident-reports' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect(start_response, '/dashboard')
        conn = db()
        assigned_site = guard_primary_assigned_site(conn, user, preferred_site_id=row_value(user, 'session_site_id'))
        incident_reports = conn.execute('SELECT i.*, s.name as site_name FROM incident_reports i JOIN sites s ON i.site_id=s.id WHERE i.company_id=? AND i.officer_id=? ORDER BY i.created_at DESC LIMIT 20', (user['company_id'], user['id'])).fetchall()
        conn.close()
        return app_page(environ, start_response, user, 'guard_incident_reports.html', active_path='/guard/incident-reports', view='week', title='Incident Reports', assigned_site=assigned_site, server_now=utc_now_str(), incident_reports=incident_reports)
    if path == '/guard/incident-reports/new' and method == 'POST':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return bad_request(start_response, 'Only guards can submit Incident Reports.')
        data, files = parse_post(environ)
        if not data.get('incident_type') or not data.get('priority') or not data.get('narrative'):
            return bad_request(start_response, 'Incident type, priority, and narrative are required.')
        conn = db()
        assigned_site = guard_primary_assigned_site(conn, user, preferred_site_id=row_value(user, 'session_site_id'))
        if not assigned_site:
            conn.close()
            return redirect_with_feedback(start_response, '/guard/incident-reports', error='No assigned site found for your account.')
        incident_files = collect_attachments(files, 'attachment')
        insert_incident_report(conn, user, assigned_site['site_id'], data, incident_files, offline_submitted=0)
        conn.commit()
        conn.close()
        return redirect_with_feedback(start_response, '/guard/incident-reports', message='Incident report submitted.')
    if path == '/admin/paystubs/upload' and method == 'GET':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        guards = conn.execute("""
            SELECT id, full_name
            FROM users
            WHERE company_id=? AND role='guard' AND active=1
            ORDER BY full_name
        """, (user['company_id'],)).fetchall()
        recent_paystubs = conn.execute("""
            SELECT p.id, p.pay_period_start, p.pay_period_end, p.created_at, u.full_name AS guard_name
            FROM guard_paystubs p
            JOIN users u ON p.guard_id=u.id
            WHERE p.company_id=?
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT 20
        """, (user['company_id'],)).fetchall()
        conn.close()
        return app_page(
            environ,
            start_response,
            user,
            'admin_paystub_upload.html',
            active_path='/admin/paystubs/upload',
            view='week',
            title='Paystub Uploads',
            guards=guards,
            recent_paystubs=recent_paystubs,
        )
    if path == '/admin/paystubs/upload' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, files = parse_post(environ)
        guard_id_raw = (data.get('guard_id') or '').strip()
        if not guard_id_raw.isdigit():
            return redirect_with_feedback(start_response, '/admin/paystubs/upload', error='Please select a valid guard.')
        paystub_file = files.get('paystub_file')
        if not paystub_file:
            return redirect_with_feedback(start_response, '/admin/paystubs/upload', error='Please upload a paystub PDF.')
        if not is_pdf_upload(paystub_file):
            return redirect_with_feedback(start_response, '/admin/paystubs/upload', error='Only valid PDF files are allowed.')
        uploaded_name, uploaded_path = save_upload(paystub_file, 'paystubs')
        if not uploaded_path:
            return redirect_with_feedback(start_response, '/admin/paystubs/upload', error='Upload failed. Check file size and try again.')
        guard_id = int(guard_id_raw)
        pay_period_start = (data.get('pay_period_start') or '').strip() or None
        pay_period_end = (data.get('pay_period_end') or '').strip() or None
        conn = db()
        guard = conn.execute(
            "SELECT id FROM users WHERE id=? AND company_id=? AND role='guard' AND active=1",
            (guard_id, user['company_id']),
        ).fetchone()
        if not guard:
            conn.close()
            local_file_path = local_path_from_upload(uploaded_path)
            if local_file_path and os.path.isfile(local_file_path):
                os.remove(local_file_path)
            return redirect_with_feedback(start_response, '/admin/paystubs/upload', error='Selected guard was not found for your company.')
        created_at = utc_now_str()
        period = None
        if pay_period_start and pay_period_end:
            period = conn.execute('SELECT id FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], pay_period_start, pay_period_end)).fetchone()
        conn.execute(
            'INSERT INTO guard_paystubs (guard_id, company_id, period_id, pay_date, file_path, notes, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (guard_id, user['company_id'], period['id'] if period else None, data.get('pay_date') or None, uploaded_path, data.get('notes', ''), user['id'], created_at),
        )
        conn.commit()
        conn.close()
        log_audit(
            'paystub_uploaded',
            actor_user_id=user['id'],
            company_id=user['company_id'],
            target_type='paystub',
            target_id=uploaded_name or uploaded_path,
            message='paystub uploaded',
            environ=environ,
            metadata={'guard_id': guard_id, 'pay_period_start': pay_period_start, 'pay_period_end': pay_period_end},
        )
        return redirect_with_feedback(start_response, '/admin/paystubs/upload', message='Paystub uploaded successfully.')
    if path == '/my/paystubs' and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        if user['role'] != 'guard':
            return redirect_with_feedback(start_response, '/dashboard', error='Only guards can view personal paystubs.')
        conn = db()
        paystubs = conn.execute(
            'SELECT id, file_path, created_at, pay_date, notes FROM guard_paystubs WHERE company_id=? AND guard_id=? ORDER BY created_at DESC, id DESC',
            (user['company_id'], user['id']),
        ).fetchall()
        conn.close()
        return app_page(
            environ,
            start_response,
            user,
            'guard_paystubs.html',
            active_path='/my/paystubs',
            view='week',
            title='My Paystubs',
            paystubs=paystubs,
        )
    paystub_file_match = re.match(r'^/paystubs/(\d+)/file$', path)
    if paystub_file_match and method == 'GET':
        user, response = require_login(environ, start_response)
        if response: return response
        paystub_id = int(paystub_file_match.group(1))
        conn = db()
        paystub = conn.execute('SELECT * FROM guard_paystubs WHERE id=?', (paystub_id,)).fetchone()
        conn.close()
        if not paystub:
            return not_found(start_response)
        if user['role'] == 'guard':
            if paystub['company_id'] != user['company_id'] or paystub['guard_id'] != user['id']:
                return forbidden(start_response)
        elif user['role'] in {'company_admin', 'superadmin', 'supervisor'}:
            if paystub['company_id'] != user['company_id']:
                return forbidden(start_response)
        else:
            return forbidden(start_response)
        local_file_path = local_path_from_upload(paystub['file_path'])
        if local_file_path:
            if not os.path.isfile(local_file_path):
                return not_found(start_response)
            start_response(
                '200 OK',
                response_headers(
                    [('Content-Disposition', f'inline; filename=\"paystub_{paystub_id}.pdf\"')],
                    'application/pdf'
                ),
            )
            with open(local_file_path, 'rb') as f:
                return [f.read()]
        return redirect(start_response, paystub['file_path'])
    if path in {'/admin/run-missed-clock-check', '/admin/run-missed-clock-check/'} and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        result = run_missed_clock_check(user['company_id'], actor_user_id=user['id'], environ=environ)
        return json_response(start_response, result)
    if path in {'/internal/run-missed-clock-check', '/internal/run-missed-clock-check/'} and method == 'POST':
        allowed, error = require_internal_token(environ)
        if not allowed:
            return json_response(start_response, {'error': error}, status='403 Forbidden')
        try:
            company_ids = requested_company_ids(environ)
        except ValueError as exc:
            return json_response(start_response, {'error': str(exc)}, status='400 Bad Request')
        result = run_missed_clock_check_for_companies(company_ids, environ=environ)
        return json_response(start_response, result)
    if path in {'/payroll', '/admin/payroll'}:
        user, response = require_admin(environ, start_response)
        if response: return response
        start_date, end_date = parse_payroll_period_dates(query.get('start'), query.get('end'))
        payroll_error = ''
        rows = []
        period = None
        company = None
        review = None
        try:
            rows = payroll_rows(user['company_id'], start_date, end_date, query.get('guard_id')) or []
            conn = db()
            try:
                period = conn.execute('SELECT * FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], start_date, end_date)).fetchone()
                company = conn.execute('SELECT qb_connected_at, qb_realm_id FROM companies WHERE id=?', (user['company_id'],)).fetchone()
                record_map = payroll_guard_record_map(conn, user['company_id'], period['id'] if period else None) or {}
                review_guard_id = query.get('guard_id')
                for r in rows:
                    rec = record_map.get(r['guard_id']) or {}
                    display = get_payroll_display_values(r, rec)
                    r['regular_hours'] = display['regular_hours'] or 0
                    r['overtime_hours'] = display['overtime_hours'] or 0
                    r['total_hours'] = display['total_hours'] or 0
                    r['hourly_rate'] = float(r.get('hourly_rate') or 0)
                    r['gross_pay'] = display['estimated_gross_pay'] or 0
                    r['hours_source'] = display['source']
                    review_status_raw = rec.get('status') if rec else None
                    r['review_status_label'] = (review_status_raw.replace('_', ' ').title() if review_status_raw else ('Approved' if display['total_hours'] > 0 else 'Pending Review'))
                if review_guard_id:
                    review_row = next((r for r in rows if str(r['guard_id']) == str(review_guard_id)), None)
                    if review_row:
                        shifts = conn.execute('SELECT clock_in_time, clock_out_time, notes FROM shifts WHERE company_id=? AND COALESCE(user_id, guard_id)=? AND shift_date BETWEEN ? AND ? ORDER BY shift_date, start_time', (user['company_id'], review_row['guard_id'], start_date, end_date)).fetchall() or []
                        missing = any((not s['clock_in_time']) or (not s['clock_out_time']) for s in shifts) or len(shifts) == 0
                        rec = record_map.get(review_row['guard_id']) or {}
                        display = get_payroll_display_values(review_row, rec)
                        manual_override_used = display['source'] == 'manual_override'
                        effective_total_hours = display['total_hours'] or 0
                        effective_gross_pay = display['estimated_gross_pay'] or 0
                        show_manual_override = missing or (query.get('manual_mode') == '1') or manual_override_used
                        approval_blocked = (missing and not manual_override_used) or effective_total_hours <= 0
                        review = {
                    'guard_id': review_row['guard_id'], 'guard_name': review_row['full_name'], 'period_start': start_date, 'period_end': end_date,
                    'site_name': review_row['site_name'] or 'Multiple', 'clock_records': f'{len(shifts)} shifts captured',
                    'total_hours': effective_total_hours, 'regular_hours': display['regular_hours'],
                    'overtime_hours': display['overtime_hours'], 'pay_rate': float(review_row['hourly_rate'] or 0), 'gross_pay': effective_gross_pay,
                    'missing_warning': 'Missing clock-in or clock-out found.' if (missing and not manual_override_used) else 'No missing punches detected.',
                    'guard_notes': '; '.join([s['notes'] for s in shifts if s['notes']]) or 'No guard notes.',
                    'admin_notes': rec.get('admin_notes', '') if rec else '',
                    'locked': bool(period and period['status'] == 'sent_to_quickbooks'),
                    'error': query.get('review_error', ''),
                    'show_manual_override': show_manual_override,
                    'manual_override_used': manual_override_used,
                    'manual_regular_hours': display['regular_hours'],
                    'manual_overtime_hours': display['overtime_hours'],
                    'manual_reason': rec.get('manual_override_reason', '') if rec else '',
                    'approval_blocked': approval_blocked,
                }
                        log_audit('payroll_review_opened', actor_user_id=user['id'], company_id=user['company_id'], target_type='payroll_guard', target_id=str(review_row['guard_id']), message='review opened', environ=environ, metadata={'start': start_date, 'end': end_date})
            finally:
                conn.close()
        except Exception as exc:
            print(f"[payroll_period_load_error] {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            payroll_error = 'Payroll period failed to load. Check logs.'
        payroll_can_export = len(rows) > 0 and all(str(r.get('review_status_label', '')).lower() == 'approved' for r in rows)
        payroll_export_blocked = query.get('export_blocked') == '1'
        send_status = 'Sent to QuickBooks' if period and period.get('status') == 'sent_to_quickbooks' else 'Not Sent'
        qb_company_name = ''
        if company and company.get('qb_connected_at') and company.get('qb_realm_id'):
            try:
                qb_company_name = (quickbooks_fetch_company_info(user['company_id']).get('CompanyName') or '').strip()
            except Exception as exc:
                print(f"[quickbooks_companyinfo_error] {exc}", flush=True)
        try:
            return app_page(environ, start_response, user, 'payroll.html', active_path='/payroll', view='week', title='Payroll Processing', payroll_rows=rows, payroll_start=start_date, payroll_end=end_date, payroll_period=period, payroll_can_export=payroll_can_export, payroll_export_blocked=payroll_export_blocked, query_preview=(query.get('preview') == '1'), payroll_review=review, qb_connected=bool(company and company.get('qb_connected_at')), qb_company_name=qb_company_name, payroll_send_status=send_status, error=payroll_error)
        except Exception as exc:
            log_route_exception('/payroll', exc)
            return html_response(start_response, b'<h1>Payroll failed to load. Check server logs.</h1>', status='500 Internal Server Error')
    if path == '/admin/payroll/review':
        return redirect(start_response, f"/admin/payroll?{urlencode({'start': query.get('start',''), 'end': query.get('end',''), 'guard_id': query.get('guard_id','')})}")
    if path == '/admin/payroll/review/action' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        start_date, end_date, guard_id, action = data.get('start'), data.get('end'), data.get('guard_id'), data.get('action')
        conn = db()
        period = conn.execute('SELECT * FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], start_date, end_date)).fetchone()
        if not period:
            now = utc_now_str()
            conn.execute('INSERT INTO payroll_periods (company_id, period_start, period_end, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)', (user['company_id'], start_date, end_date, 'pending_approval', user['id'], now))
            period = conn.execute('SELECT * FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], start_date, end_date)).fetchone()
        if period['status'] == 'sent_to_quickbooks':
            conn.close()
            return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'Payroll already exported. Review is view-only.'})}")
        row = payroll_rows(user['company_id'], start_date, end_date, guard_id)[0]
        shifts = conn.execute('SELECT clock_in_time, clock_out_time FROM shifts WHERE company_id=? AND COALESCE(user_id, guard_id)=? AND shift_date BETWEEN ? AND ?', (user['company_id'], guard_id, start_date, end_date)).fetchall()
        missing = any((not s['clock_in_time']) or (not s['clock_out_time']) for s in shifts) or len(shifts) == 0
        existing_rec = conn.execute('SELECT * FROM payroll_guard_records WHERE company_id=? AND period_id=? AND guard_id=? ORDER BY id DESC LIMIT 1', (user['company_id'], period['id'], guard_id)).fetchone()
        base_display = get_payroll_display_values(row, None)
        manual_override_used = bool(existing_rec and existing_rec['manual_override_used'])
        clock_regular_hours = float(base_display['regular_hours'])
        clock_overtime_hours = float(base_display['overtime_hours'])
        clock_total_hours = float(base_display['total_hours'])
        regular_hours = float(existing_rec['regular_hours']) if manual_override_used and existing_rec else clock_regular_hours
        overtime_hours = float(existing_rec['overtime_hours']) if manual_override_used and existing_rec else clock_overtime_hours
        gross_pay_estimate = (regular_hours * float(row['hourly_rate'] or 0)) + (overtime_hours * float(row['hourly_rate'] or 0) * 1.5) if manual_override_used else float(row['gross_pay'] or 0)
        status = 'pending_review'
        if action == 'save_manual_hours':
            try:
                regular_hours = float(data.get('manual_regular_hours') or 0)
                overtime_hours = float(data.get('manual_overtime_hours') or 0)
            except (TypeError, ValueError):
                conn.close()
                return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'Manual regular and overtime hours must be valid numbers.', 'manual_mode': '1'})}")
            manual_reason = (data.get('manual_reason') or '').strip()
            if regular_hours < 0 or overtime_hours < 0:
                conn.close()
                return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'Manual hours cannot be negative.', 'manual_mode': '1'})}")
            if (regular_hours + overtime_hours) <= 0:
                conn.close()
                return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'At least one of regular or overtime hours must be greater than 0.', 'manual_mode': '1'})}")
            if not manual_reason:
                conn.close()
                return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'Admin Reason / Notes is required for manual override.', 'manual_mode': '1'})}")
            gross_pay_estimate = (regular_hours * float(row['hourly_rate'] or 0)) + (overtime_hours * float(row['hourly_rate'] or 0) * 1.5)
            status = 'pending_review'
            manual_override_used = True
            old_regular = float(existing_rec['regular_hours']) if existing_rec else float(max((row['total_hours'] or 0) - (row['overtime_hours'] or 0), 0))
            old_overtime = float(existing_rec['overtime_hours']) if existing_rec else float(row['overtime_hours'] or 0)
            audit_note = f"guard_id={guard_id}; pay_period_start={start_date}; pay_period_end={end_date}; old_regular_hours={old_regular:.2f}; old_overtime_hours={old_overtime:.2f}; new_regular_hours={regular_hours:.2f}; new_overtime_hours={overtime_hours:.2f}; admin_reason={manual_reason}; admin_user={user['username']}"
            conn.execute('INSERT INTO payroll_audit_logs (company_id, period_id, guard_id, event_type, notes, actor_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (user['company_id'], period['id'], guard_id, 'manual_hours_saved', audit_note, user['id'], utc_now_str()))
        approval_source = 'manual_override' if manual_override_used else 'clock_records'
        if action == 'approve':
            manual_total_hours = regular_hours + overtime_hours
            if manual_override_used:
                if manual_total_hours <= 0:
                    conn.close()
                    return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'Cannot approve: valid hours are required.'})}")
            elif clock_total_hours > 0 and not missing:
                regular_hours = clock_regular_hours
                overtime_hours = clock_overtime_hours
                gross_pay_estimate = float(row['gross_pay'] or 0)
                approval_source = 'clock_records'
            else:
                conn.close()
                return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'review_error': 'Cannot approve: valid hours are required.'})}")
            status = 'approved'
        elif action == 'flag':
            status = 'issue_flagged'
        elif action == 'send_back':
            status = 'pending_review'
        elif action == 'edit':
            status = 'hours_edited'
            return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'guard_id': guard_id, 'manual_mode': '1'})}")
        now = utc_now_str()
        conn.execute('DELETE FROM payroll_guard_records WHERE company_id=? AND period_id=? AND guard_id=?', (user['company_id'], period['id'], guard_id))
        conn.execute('INSERT INTO payroll_guard_records (period_id, company_id, guard_id, regular_hours, overtime_hours, pay_rate, gross_pay_estimate, status, admin_notes, manual_override_used, manual_override_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (period['id'], user['company_id'], guard_id, regular_hours, overtime_hours, row['hourly_rate'] or 0, gross_pay_estimate, status, data.get('admin_notes', ''), 1 if manual_override_used else 0, (data.get('manual_reason', '') or (existing_rec['manual_override_reason'] if existing_rec else ''))))
        audit_notes = data.get('admin_notes', '')
        if action == 'approve':
            source_note = f"approval_source={approval_source}"
            audit_notes = f"{audit_notes}; {source_note}" if audit_notes else source_note
        conn.execute('INSERT INTO payroll_audit_logs (company_id, period_id, guard_id, event_type, notes, actor_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (user['company_id'], period['id'], guard_id, f'payroll_{action}', audit_notes, user['id'], now))
        conn.commit(); conn.close()
        log_audit('payroll_review_action', actor_user_id=user['id'], company_id=user['company_id'], target_type='payroll_guard', target_id=str(guard_id), message=f'{action} action', environ=environ, metadata={'start': start_date, 'end': end_date})
        return redirect(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date})}")
    if path == '/admin/payroll/export.csv':
        user, response = require_admin(environ, start_response)
        if response: return response
        try:
            start_date, end_date = parse_payroll_period_dates(query.get('start'), query.get('end'))
            conn = db()
            try:
                rows = payroll_rows(user['company_id'], start_date, end_date) or []
                period = conn.execute('SELECT * FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], start_date, end_date)).fetchone()
                record_map = payroll_guard_record_map(conn, user['company_id'], period['id'] if period else None)

                if not rows:
                    return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date})}", error='No payroll rows found for this period.')

                for row in rows:
                    guard_record = (record_map.get(row['guard_id']) or {})
                    guard_status = str(guard_record.get('status') or '').lower()
                    display = get_payroll_display_values(row, guard_record)
                    guard_name = row.get('full_name') or 'Unknown guard'
                    if guard_status != 'approved':
                        return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date})}", error='Approve all payroll rows before export.')
                    if display['total_hours'] <= 0:
                        return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date})}", error=f'Missing hours for {guard_name}.')
                    if row.get('hourly_rate') in (None, ''):
                        return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date})}", error=f'Missing pay rate for {guard_name}.')
                    _guard_email = (row.get('email') or '').strip() or 'missing-email@unknown.local'
                    _site_name = (row.get('site_name') or '').strip() or 'Unknown Site'

                return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date, 'preview': '1'})}", success='Payroll batch preview ready.')
            finally:
                conn.close()
        except Exception as exc:
            print(f"[payroll_export_error] {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            fallback_start, fallback_end = parse_payroll_period_dates(query.get('start'), query.get('end'))
            return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': fallback_start, 'end': fallback_end})}", error='Payroll export failed. Check logs.')
    if path == '/admin/settings/quickbooks/reconnect' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        conn.execute(
            'UPDATE companies SET qb_access_token=NULL, qb_refresh_token=NULL, qb_realm_id=NULL, qb_connected_at=NULL, qb_expires_at=NULL WHERE id=?',
            (user['company_id'],),
        )
        conn.commit()
        conn.close()
        return redirect(start_response, '/admin/settings/quickbooks/connect')

    if path == '/admin/settings/quickbooks/connect' and method in ('GET', 'POST'):
        user, response = require_admin(environ, start_response)
        if response: return response
        conn = db()
        company = conn.execute('SELECT qb_connected_at, qb_realm_id, qb_access_token, qb_refresh_token FROM companies WHERE id=?', (user['company_id'],)).fetchone()
        conn.close()
        qb_already_connected = bool(company and company.get('qb_connected_at') and company.get('qb_realm_id') and company.get('qb_access_token') and company.get('qb_refresh_token'))
        if qb_already_connected:
            return redirect_with_feedback(start_response, '/payroll', success='QuickBooks is already connected.')
        try:
            required_vars = {
                'QUICKBOOKS_CLIENT_ID': os.getenv('QUICKBOOKS_CLIENT_ID', '').strip(),
                'QUICKBOOKS_CLIENT_SECRET': os.getenv('QUICKBOOKS_CLIENT_SECRET', '').strip(),
                'QUICKBOOKS_REDIRECT_URI': os.getenv('QUICKBOOKS_REDIRECT_URI', '').strip(),
                'QUICKBOOKS_ENV': os.getenv('QUICKBOOKS_ENV', '').strip(),
            }
            missing_vars = [name for name, value in required_vars.items() if not value]
            if missing_vars:
                missing_html = ''.join(f'<li><code>{escape(name)}</code></li>' for name in missing_vars)
                body = f'''<!doctype html><html><head><title>QuickBooks Setup Required</title></head><body>
                <h1>QuickBooks setup is incomplete</h1>
                <p>Please add the following environment variables and try again:</p>
                <ul>{missing_html}</ul>
                <p><a href="/admin/payroll">Back to Payroll Settings</a></p>
                </body></html>'''.encode('utf-8')
                return html_response(start_response, body, status='400 Bad Request')

            oauth_state = secrets.token_urlsafe(32)
            query_params = {
                'client_id': required_vars['QUICKBOOKS_CLIENT_ID'],
                'response_type': 'code',
                'scope': 'com.intuit.quickbooks.accounting',
                'redirect_uri': required_vars['QUICKBOOKS_REDIRECT_URI'],
                'state': oauth_state,
            }
            oauth_url = f"https://appcenter.intuit.com/connect/oauth2?{urlencode(query_params)}"
            state_cookie = qb_state_cookie_header(oauth_state, datetime.now(timezone.utc) + timedelta(minutes=15))
            return redirect(start_response, oauth_url, extra_headers=[('Set-Cookie', state_cookie)])
        except Exception as exc:
            print(f"[quickbooks_oauth_connect_error] {exc}", flush=True)
            return html_response(start_response, b"<h1>QuickBooks connection failed</h1><p>We couldn't start authorization right now. Please check server logs and configuration.</p>", status='500 Internal Server Error')

    if path == '/admin/settings/quickbooks/callback' and method == 'GET':
        user, response = require_admin(environ, start_response)
        if response: return response
        try:
            code = (query.get('code') or '').strip()
            state = (query.get('state') or '').strip()
            realm_id = (query.get('realmId') or '').strip()
            cookies = parse_request_cookies(environ)
            expected_state = (cookies.get('qb_oauth_state') or '').strip()

            if not code or not realm_id:
                return html_response(start_response, b"<h1>QuickBooks callback error</h1><p>Missing required parameters: code and realmId.</p>", status='400 Bad Request')
            if not state or not expected_state or state != expected_state:
                return html_response(start_response, b"<h1>QuickBooks callback error</h1><p>Invalid OAuth state. Please start the connection again.</p>", status='400 Bad Request')
            redirect_uri = os.getenv('QUICKBOOKS_REDIRECT_URI', '').strip()
            token_data = exchange_quickbooks_code_for_tokens(code, redirect_uri)
            access_token = (token_data.get('access_token') or '').strip()
            refresh_token = (token_data.get('refresh_token') or '').strip()
            expires_in = int(token_data.get('expires_in') or 3600)
            expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).strftime('%Y-%m-%d %H:%M:%S')
            if not access_token or not refresh_token:
                return html_response(start_response, b"<h1>QuickBooks callback error</h1><p>Token exchange failed. Missing access_token or refresh_token.</p>", status='400 Bad Request')
            conn = db()
            conn.execute(
                'UPDATE companies SET qb_realm_id=?, qb_access_token=?, qb_refresh_token=?, qb_expires_at=?, qb_connected_at=? WHERE id=?',
                (realm_id, access_token, refresh_token, expires_at, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'), user['company_id']),
            )
            conn.commit()
            conn.close()
            company_info = quickbooks_fetch_company_info(user['company_id'])
            company_name = (company_info.get('CompanyName') or 'Unknown Company').strip()
            return html_response(
                start_response,
                f"<h1>QuickBooks authorization received successfully.</h1><p>Connected to QuickBooks: {escape(company_name)}</p><p><a href=\"/admin/payroll\">Return to Payroll</a></p>".encode('utf-8'),
                extra_headers=[('Set-Cookie', qb_delete_state_cookie_header())]
            )
        except Exception as exc:
            print(f"[quickbooks_oauth_callback_error] {exc}", flush=True)
            return html_response(start_response, b"<h1>QuickBooks callback failed</h1><p>We couldn't complete authorization. Please try again.</p>", status='500 Internal Server Error')
    if path == '/admin/payroll/send-to-quickbooks' and method == 'POST':
        user, response = require_admin(environ, start_response)
        if response: return response
        data, _ = parse_post(environ)
        start_date = data.get('start', (date.today() - timedelta(days=date.today().weekday())).isoformat())
        end_date = data.get('end', (date.today() - timedelta(days=date.today().weekday()) + timedelta(days=13)).isoformat())
        conn = db()
        rows = payroll_rows(user['company_id'], start_date, end_date)
        period = conn.execute('SELECT * FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], start_date, end_date)).fetchone()
        record_map = payroll_guard_record_map(conn, user['company_id'], period['id'] if period else None)
        if any((record_map.get(r['guard_id']) or {}).get('status') != 'approved' for r in rows):
            conn.close()
            return redirect_with_feedback(start_response, f"/admin/payroll?{urlencode({'start': start_date, 'end': end_date})}", error='Cannot send payroll: all guards must be approved.')
        payload = quickbooks_payroll_payload(user['company_id'], start_date, end_date, record_map)
        print('QuickBooks payroll payload (phase 1 simulation):', json.dumps(payload, indent=2))
        now = utc_now_str()
        if not period:
            conn.execute('INSERT INTO payroll_periods (company_id, period_start, period_end, status, locked_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (user['company_id'], start_date, end_date, 'sent_to_quickbooks', now, user['id'], now))
            period = conn.execute('SELECT * FROM payroll_periods WHERE company_id=? AND period_start=? AND period_end=? ORDER BY id DESC LIMIT 1', (user['company_id'], start_date, end_date)).fetchone()
        conn.execute('UPDATE payroll_periods SET status=?, locked_at=? WHERE id=?', ('sent_to_quickbooks', now, period['id']))
        conn.commit()
        conn.close()
        return json_response(start_response, {'status': 'success', 'message': 'Payroll payload logged and QuickBooks send simulated.', 'sent_count': len(payload), 'period_start': start_date, 'period_end': end_date})
    return not_found(start_response)


app = application


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='SteeleOps server and utilities')
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('serve')
    sub.add_parser('init-db')
    create_admin_parser = sub.add_parser('create-admin')
    create_admin_parser.add_argument('--company', required=True)
    create_admin_parser.add_argument('--username', required=True)
    create_admin_parser.add_argument('--password', required=True)
    create_admin_parser.add_argument('--full-name', required=True)
    create_admin_parser.add_argument('--email', default='')
    args = parser.parse_args()
    command = args.command or 'serve'
    
    PORT = int(os.environ.get("PORT", "10000"))
    
    try:
        print(f'[startup] command={command} app_env={APP_ENV} backend={'postgres' if USE_POSTGRES else 'sqlite'} host={HOST} port={PORT}', flush=True)
        print(f'[startup] upload_dir={UPLOAD_DIR} upload_dir_env_set={bool(UPLOAD_DIR_ENV)} render_disk_path={RENDER_DISK_PATH or "(not set)"}', flush=True)
        if not RENDER_DISK_PATH and not UPLOAD_DIR_ENV and STORAGE_BACKEND != 's3':
            print('[startup][warning] Uploads are not persistent without a Render disk or cloud storage.', flush=True)
        if command == 'init-db':
            init_db(); print('Database initialized.')
        elif command == 'create-admin':
            create_admin_account(args.company, args.username, args.password, args.full_name, args.email); print(f'Created company admin {args.username} for {args.company}.')
        elif command == 'serve':
            init_db(); print(f'SteeleOps running on http://{HOST}:{PORT}')
            with make_server(HOST, PORT, application) as httpd:
                scheduler_started = start_missed_clock_scheduler_once()
                print(f'[startup] scheduler_started={scheduler_started}', flush=True)
                httpd.serve_forever()
        else:
            raise ValueError(f'Unsupported command: {command}')
    except Exception:
        print('[startup] fatal error during boot', flush=True)
        traceback.print_exc()
        raise
