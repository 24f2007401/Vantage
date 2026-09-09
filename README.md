# Vantage — Recruitment OS

**The Single Source of Truth.**

Vantage is a full-stack placement management system built for engineering institutions. It consolidates the entire recruitment lifecycle — from T-Sheet academic data ingestion through eligibility verification, drive management, and final placement tracking — into a single auditable platform. The system eliminates spreadsheet-driven chaos, provides verifiable academic credentials to recruiters, and enforces a zero-manual-entry policy by deriving student eligibility directly from parsed grade sheets.

---
  
## Architecture Overview

Vantage runs a Flask application server backed by PostgreSQL (Supabase-hosted), with Redis providing JWT blocklisting and rate-limit storage. The frontend is server-rendered HTML using Jinja2 templates and a shared JavaScript API client. The T-Sheet pipeline runs asynchronously in background threads and streams progress to the browser via Server-Sent Events.

| Layer | Technology |
|---|---|
| Frontend | Jinja2 templates, vanilla JS (`vantage-api.js`) |
| Backend | Python 3.10+, Flask 3.1 |
| Database | PostgreSQL 15 (Supabase) via SQLAlchemy 2.0 + Alembic |
| Authentication | Flask-Login (session) + Flask-JWT-Extended (Bearer tokens) |
| T-Sheet Parsing | pandas 2.2, openpyxl 3.1, pdfplumber 0.11 (stub) |
| File Storage | Supabase Storage (local fallback) |
| Email | Resend API (password reset, welcome emails) |
| Error Monitoring | Sentry SDK (optional) |
| Deployment | Gunicorn (gthread), Railway / Render / Heroku |

---

## Project Structure

```text
Vantage/
├── app/
│   ├── __init__.py          # Application factory (create_app)
│   ├── config.py            # Dev / Prod / Test configurations
│   ├── extensions.py        # db, jwt, login_manager, redis, limiter, cors
│   ├── errors.py            # Global error handlers
│   ├── email.py             # Resend-based transactional email
│   ├── storage.py           # Supabase / local file upload
│   ├── listeners.py         # SQLAlchemy event listeners (CGPA, eligibility)
│   ├── auth/
│   │   └── routes.py        # 8 auth endpoints (signup, login, reset, refresh)
│   ├── admin/
│   │   └── routes.py        # 46 admin endpoints (students, drives, pipeline, analytics)
│   ├── student/
│   │   └── routes.py        # 16 student endpoints (profile, drives, applications)
│   ├── recruiter/
│   │   └── routes.py        # 24 recruiter endpoints (drives, candidates, pipeline)
│   ├── models/
│   │   ├── user.py          # User, RoleEnum
│   │   ├── student.py       # Student (40-column central record)
│   │   ├── placement_*.py   # PlacementDrive, PlacementApplication, EligibilityRule
│   │   ├── tsheet_*.py      # TSheetUpload, TSheetRecord (staging tables)
│   │   ├── exam_*.py        # ExamBatch, ExamResult, StudentSemesterSummary
│   │   ├── academic_batch.py
│   │   ├── branch.py
│   │   ├── course.py
│   │   ├── recruiter.py
│   │   ├── certification.py
│   │   ├── audit_log.py     # Append-only compliance log
│   │   ├── notification.py
│   │   └── revoked_token.py # JWT blocklist (DB fallback)
│   ├── pipeline/
│   │   ├── parser.py        # Stage 1: CSV/Excel → raw DataFrame
│   │   ├── normalizer.py    # Stage 2: fuzzy column mapping → canonical dicts
│   │   └── ingestion.py     # Stage 3: DB write, conflict detection, student creation
│   ├── templates/           # 25 Jinja2 HTML pages (student, admin, recruiter)
│   └── static/js/
│       └── vantage-api.js   # Shared API client (auth, fetch, SSE, localStorage)
├── migrations/
│   └── versions/            # Alembic migration scripts
├── run.py                   # Development entry point
├── gunicorn.conf.py         # Production server config (gthread, 4 threads)
├── Procfile                 # Railway/Heroku: migrate then serve
└── requirements.txt
```

---

## Modules

### Module 1 — Authentication (`app/auth/`)

Handles user registration, login, token lifecycle, and password reset for all three roles (Student, Recruiter, Admin). Issues both JWT Bearer tokens (API clients) and Flask-Login sessions (server-rendered pages). Token revocation uses Redis as primary storage with a PostgreSQL `revoked_tokens` table as fallback. Rate-limited to 5 attempts per minute on login and signup.

Key files: `auth/routes.py`, `models/user.py`, `models/revoked_token.py`

Routes: `POST /api/auth/signup`, `/login`, `/logout`, `/refresh`, `/change-password`, `/forgot-password`, `/reset-password` — `GET /api/auth/me`

---

### Module 2 — Admin Panel (`app/admin/`)

The placement cell's control surface. Covers T-Sheet pipeline management, student record administration, drive oversight across all recruiters, eligibility bulk re-evaluation, and placement analytics. The largest module at 2,141 lines with 46 endpoints. All routes require the `ADMIN` role and are additionally protected by the `@verified_required` decorator.

Key files: `admin/routes.py`, `models/student.py`, `models/placement_drive.py`

Routes: See API Reference — Admin group (46 endpoints).

---

### Module 3 — Student Portal (`app/student/`)

Allows students to view their computed academic profile, browse open placement drives with per-drive eligibility explanations, submit applications, and track application status through the recruiter's pipeline stages. Academic data (CGPA, backlogs, semester summaries) is read-only from the student's perspective — it is derived exclusively from ingested T-Sheets. Students may edit profile URLs (GitHub, LinkedIn, resume) and manage certifications.

Key files: `student/routes.py`, `models/student.py`, `models/placement_application.py`

Routes: `GET/PATCH /api/student/profile`, drive browsing and application endpoints, certifications, SSE event stream.

---

### Module 4 — Recruiter Dashboard (`app/recruiter/`)

Enables recruiting companies to create and manage drives, browse the verified student pool (with privacy controls — full name shown only if the student applied), move applicants through a multi-stage hiring pipeline, send bulk messages, and export shortlists as CSV. Drive status flows from DRAFT through OPEN, CLOSED, to COMPLETED.

Key files: `recruiter/routes.py`, `models/placement_drive.py`, `models/recruiter.py`

Routes: Drive CRUD, applicant status updates, candidate browsing, bulk messaging, shortlist export, analytics.

---

### Module 5 — T-Sheet Pipeline (`app/pipeline/`)

A three-stage asynchronous pipeline that ingests JNTUH T-Sheet grade exports into the database. Processing runs in a background thread; the client receives real-time progress via a Server-Sent Events stream. Conflicts (duplicate results, unknown roll numbers, unrecognized branch codes) are staged for admin review rather than discarded.

Key files: `pipeline/parser.py`, `pipeline/normalizer.py`, `pipeline/ingestion.py`

Routes (Admin-facing): `POST /api/admin/tsheets/upload`, batch listing, SSE stream, conflict listing and resolution.

---

### Module 6 — Academic Models (`app/models/exam_*.py`, `academic_batch.py`, `branch.py`, `course.py`)

Defines the academic hierarchy used by the pipeline and eligibility engine: Institution → Department → Branch → AcademicBatch → Student → ExamBatch → ExamResult. `ExamResult` is the highest-volume table — one row per student × subject × exam event. `StudentSemesterSummary` stores pre-aggregated SGPA and backlog counts per semester. Six SQLAlchemy event listeners in `listeners.py` keep `Student.current_cgpa`, `active_backlogs`, and `is_placement_eligible` in sync whenever results change.

Key files: `models/exam_result.py`, `models/student_semester_summary.py`, `listeners.py`

No direct API exposure; consumed by admin, student, and recruiter modules.

---

### Module 7 — Placement Logic (`app/models/placement_*.py`)

`EligibilityRule` stores per-drive criteria (minimum CGPA, maximum active backlogs, backlog history policy, allowed branches, minimum semesters completed) and exposes an `evaluate(student)` method returning a boolean plus a list of human-readable rejection reasons. `PlacementApplication` tracks status across nine pipeline stages from APPLIED through SELECTED, with timestamps and optional offer CTC recorded at selection.

Key files: `models/eligibility_rule.py`, `models/placement_application.py`, `models/placement_drive.py`

Consumed by admin and recruiter routes; eligibility evaluated on every drive-browse request.

---

### Module 8 — Notifications, Audit, and Security (`app/models/audit_log.py`, `notification.py`)

`AuditLog` is an append-only compliance table recording every admin action with before/after JSON snapshots and the actor's IP address. `Notification` delivers in-app alerts to all roles. `DriveMessage` tracks bulk recruiter messages with per-student delivery records. `RevokedToken` provides durable JWT blocklisting when Redis is unavailable. The application factory configures security headers (CSP, HSTS, X-Frame-Options) and restricts CORS to `FRONTEND_ORIGIN`.

Key files: `models/audit_log.py`, `models/notification.py`, `models/revoked_token.py`, `app/__init__.py`

---

## Data Pipeline

The T-Sheet pipeline accepts grade exports from JNTUH (MGIT) and converts them into structured academic records in seven steps.

```text
1. Upload
   POST /api/admin/tsheets/upload
   Admin uploads a .xlsx, .csv, or .pdf file (max 10 MB).
   A TSheetUpload row is created; processing begins immediately
   in a background thread. The HTTP response returns 202 with
   the batch ID.

2. Parse  (pipeline/parser.py)
   The file is read into a pandas DataFrame. For Excel files,
   Row 5 (index 4) is assumed to contain headers per the MGIT
   T-Sheet format. If Row 5 is blank, the first row is used.
   Column names are sanitized (stripped, blanks filled).

3. Normalize  (pipeline/normalizer.py)
   Raw column headers are mapped to canonical field names using
   fuzzy matching (e.g., "H.T.No." → raw_ht_no, "Gr.Pts" →
   raw_grade_points). Rows missing a roll number or course code
   are silently dropped.

4. Conflict Detection  (pipeline/ingestion.py)
   Each normalized record is compared against the database:
   - DUPLICATE    — ExamResult already exists for this
                    (student, course, exam_batch, attempt).
   - GRADE_MISMATCH — Grade differs from an existing result.
   - NEW_STUDENT  — Roll number not found; a stub Student is
                    created and flagged for admin verification.
   - NEW_BRANCH   — Branch code unrecognized; Branch is
                    auto-created with needs_review = TRUE.
   - UNKNOWN_COURSE — Course code not in Course table.
   Raw values are always written to TSheetRecord (staging table)
   regardless of conflict status.

5. Admin Review
   GET  /api/admin/tsheets/batches/<id>/conflicts
   POST /api/admin/tsheets/conflicts/<id>/resolve
   Admins inspect staged conflicts and choose:
     ACCEPT_NEW     — overwrite with new data
     KEEP_EXISTING  — discard incoming row
     SKIP           — leave unresolved for later

6. DB Commit
   Clean records and accepted resolutions are written to
   ExamResult. TSheetRecord.resolution_status is updated to
   COMMITTED. The upload's SHA-256 hash prevents re-ingestion
   of identical files.

7. Eligibility Re-evaluation
   SQLAlchemy event listeners fire after the bulk insert,
   recomputing Student.current_cgpa, active_backlogs, and
   is_placement_eligible for every affected student. Placement
   applications on open drives are re-checked against updated
   eligibility rules.
```

**Supported formats:** `.xlsx`, `.csv` (fully implemented) — `.pdf` (pdfplumber imported, parser stub only)

---

## User Roles

| Role | Description | Key Capabilities |
|---|---|---|
| Student | Enrolled student with a T-Sheet-verified academic record | View profile and computed CGPA, browse drives, check eligibility, apply, track application status, manage certifications |
| Recruiter | Verified company representative | Create and manage drives, browse anonymous candidate pool, move applicants through pipeline stages, export shortlists |
| Admin | Placement cell staff | Full access to all data, T-Sheet ingestion, conflict resolution, student verification, drive oversight across all recruiters, analytics, audit log |
| Owner | Superadmin (system-level) | All Admin capabilities plus user management and role assignment |
| Viewer | Read-only observer | Read access with no write operations |

---

## API Reference

### Authentication

| Method | Route | Description | Auth Required |
|---|---|---|---|
| POST | `/api/auth/signup` | Register as student or recruiter | No |
| POST | `/api/auth/login` | Issue JWT access and refresh tokens | No |
| POST | `/api/auth/logout` | Revoke current tokens | Yes |
| POST | `/api/auth/refresh` | Issue new access token | Refresh token |
| GET | `/api/auth/me` | Current user profile and role | Yes |
| POST | `/api/auth/change-password` | Change authenticated user's password | Yes |
| POST | `/api/auth/forgot-password` | Send password reset email | No |
| POST | `/api/auth/reset-password` | Apply password reset token | No |

### Admin — T-Sheet Pipeline

| Method | Route | Description | Auth Required |
|---|---|---|---|
| POST | `/api/admin/tsheets/upload` | Upload T-Sheet file (async, returns 202) | Admin |
| GET | `/api/admin/tsheets/batches` | List all upload batches with stats | Admin |
| GET | `/api/admin/tsheets/batches/<id>/stream` | SSE progress stream for active upload | Admin |
| GET | `/api/admin/tsheets/batches/<id>/conflicts` | List unresolved conflicts for a batch | Admin |
| POST | `/api/admin/tsheets/conflicts/<id>/resolve` | Resolve single conflict | Admin |
| POST | `/api/admin/tsheets/batches/<id>/resolve-all` | Bulk-resolve all conflicts in a batch | Admin |

### Admin — Students

| Method | Route | Description | Auth Required |
|---|---|---|---|
| GET | `/api/admin/students` | Paginated, filterable student list | Admin |
| GET | `/api/admin/students/<id>` | Full student profile with academic history | Admin |
| PATCH | `/api/admin/students/<id>` | Update biodata or eligibility flags | Admin |
| PATCH | `/api/admin/students/bulk-verify` | Verify multiple students in one request | Admin |
| GET | `/api/admin/students/<id>/applications` | All drive applications for a student | Admin |

### Admin — Drives and Eligibility

| Method | Route | Description | Auth Required |
|---|---|---|---|
| POST | `/api/admin/drives` | Create a drive | Admin |
| GET | `/api/admin/drives` | List all drives across all recruiters | Admin |
| GET | `/api/admin/drives/<id>` | Drive detail with eligibility rules | Admin |
| PATCH | `/api/admin/drives/<id>` | Update drive metadata | Admin |
| DELETE | `/api/admin/drives/<id>` | Soft-delete drive | Admin |
| POST | `/api/admin/drives/<id>/eligibility-rule` | Set CGPA / backlog / branch criteria | Admin |
| GET | `/api/admin/drives/<id>/eligible-students` | List students who meet criteria | Admin |
| GET | `/api/admin/drives/<id>/applications` | All applicants with current status | Admin |
| PATCH | `/api/admin/applications/<id>/status` | Update applicant pipeline stage | Admin |
| POST | `/api/admin/eligibility/reevaluate` | Batch re-evaluate all students | Admin |

### Admin — Analytics

| Method | Route | Description | Auth Required |
|---|---|---|---|
| GET | `/api/admin/analytics/overview` | Placement rate, average CTC, gender split | Admin |
| GET | `/api/admin/analytics/by-branch` | Stats grouped by branch | Admin |
| GET | `/api/admin/analytics/by-company` | Placements grouped by recruiter | Admin |
| GET | `/api/admin/analytics/cgpa-distribution` | Histogram data | Admin |
| GET | `/api/admin/analytics/placements` | Final placement list (CSV export) | Admin |

### Student

| Method | Route | Description | Auth Required |
|---|---|---|---|
| GET | `/api/student/dashboard` | CGPA, backlogs, drives applied, selections | Student |
| GET | `/api/student/profile` | Full profile (academic + biodata + links) | Student |
| PATCH | `/api/student/profile` | Update resume URL, skills, social links | Student |
| GET | `/api/student/drives` | Browse OPEN drives with eligibility per drive | Student |
| GET | `/api/student/drives/<id>` | Drive detail with eligibility reasons | Student |
| POST | `/api/student/drives/<id>/apply` | Submit application | Student |
| GET | `/api/student/applications` | All applications with stage timeline | Student |
| GET | `/api/student/applications/<id>` | Single application detail | Student |
| GET | `/api/student/certifications` | List certifications | Student |
| POST | `/api/student/certifications` | Add certification | Student |
| DELETE | `/api/student/certifications/<id>` | Remove certification | Student |
| GET | `/api/student/notifications` | In-app notifications | Student |
| GET | `/api/student/events` | SSE real-time event stream | Student |

### Recruiter

| Method | Route | Description | Auth Required |
|---|---|---|---|
| GET | `/api/recruiter/dashboard` | Active drives, applicant counts, selections | Recruiter |
| GET | `/api/recruiter/candidates` | Browse verified students (anonymized) | Recruiter |
| GET | `/api/recruiter/candidates/<id>` | Candidate detail (name visible if applied) | Recruiter |
| POST | `/api/recruiter/drives` | Create drive | Recruiter |
| GET | `/api/recruiter/drives` | List own drives | Recruiter |
| GET | `/api/recruiter/drives/<id>` | Drive detail with applicant breakdown | Recruiter |
| PATCH | `/api/recruiter/drives/<id>` | Update drive metadata | Recruiter |
| PATCH | `/api/recruiter/drives/<id>/close` | Close applications | Recruiter |
| GET | `/api/recruiter/drives/<id>/applicants` | Paginated applicant list | Recruiter |
| PATCH | `/api/recruiter/applications/<id>/status` | Advance applicant through pipeline | Recruiter |
| GET | `/api/recruiter/drives/<id>/shortlist` | Shortlisted candidates | Recruiter |
| GET | `/api/recruiter/drives/<id>/shortlist/export` | Download shortlist as CSV | Recruiter |
| POST | `/api/recruiter/drives/<id>/alert` | Bulk message to all / shortlisted / selected | Recruiter |
| GET | `/api/recruiter/analytics/by-branch` | Applicants per branch | Recruiter |
| GET | `/api/recruiter/events` | SSE real-time event stream | Recruiter |

---

## Environment Setup

### Prerequisites

- Python 3.10 or later
- PostgreSQL 15 (Supabase project or local instance)
- Redis (Upstash or local — optional but recommended for JWT blocklisting)
- A Resend account and API key for transactional email

### Local Setup

```bash
# 1. Clone the repository
git clone https://github.com/<org>/vantage.git
cd vantage

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# Edit .env — see the table below for required values

# 5. Apply database migrations
flask db upgrade

# 6. Seed the branch table (first run only)
# This creates the standard JNTUH branch list used by the pipeline
curl -X POST http://localhost:5000/api/admin/branches/seed \
  -H "Authorization: Bearer <admin_jwt>"

# 7. Start the development server
flask run
```

The application will be available at `http://localhost:5000`.

### Required Environment Variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Flask session signing key — use a long random string |
| `JWT_SECRET_KEY` | JWT signing key — must differ from SECRET_KEY |
| `FLASK_ENV` | `development` or `production` |
| `DATABASE_URL` | PostgreSQL connection string (e.g., `postgresql://user:pass@host/db`) |
| `REDIS_URL` | Redis connection URL (e.g., `redis://localhost:6379`) — set to empty string to disable |
| `ADMIN_EMAIL` | Email address of the initial admin account created on first run |
| `ADMIN_PASSWORD` | Password for the initial admin account |
| `SUPABASE_URL` | Supabase project URL (for file storage) |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key |
| `RESEND_API_KEY` | Resend API key for transactional email |
| `EMAIL_FROM` | Sender address for outgoing emails (e.g., `noreply@yourdomain.com`) |
| `FRONTEND_ORIGIN` | Allowed CORS origin (e.g., `http://localhost:5000`) |
| `JWT_ACCESS_TOKEN_EXPIRES` | Access token lifetime in seconds (default: `900`) |
| `JWT_REFRESH_TOKEN_EXPIRES` | Refresh token lifetime in seconds (default: `604800`) |
| `SENTRY_DSN` | Sentry DSN for error monitoring (optional) |

---

## Project Status

| Feature | Status | Notes |
|---|---|---|
| Auth — signup, login, logout, refresh | Complete | JWT + session dual auth |
| Auth — password reset via email | Complete | Resend API integration |
| T-Sheet parser (CSV, Excel) | Complete | MGIT Row-4 header detection |
| T-Sheet parser (PDF) | Planned | pdfplumber imported; parser is a stub |
| T-Sheet normalizer (fuzzy mapping) | Complete | |
| T-Sheet ingestion and staging | Complete | TSheetRecord staging table |
| Conflict detection (duplicate, mismatch, new student) | Complete | |
| Admin conflict resolution UI | Complete | Single and bulk resolution |
| Async pipeline with SSE progress | Complete | Background threading |
| Student model (40-column record) | Complete | |
| SQLAlchemy listeners (CGPA, eligibility sync) | Complete | Six listeners in `listeners.py` |
| Soft deletes and audit log | Complete | Append-only, immutable rows |
| Student portal (profile, drives, applications) | Complete | |
| Student certifications | Complete | |
| Real-time SSE event streams (student, recruiter) | Complete | |
| Admin student management (search, verify, edit) | Complete | |
| Admin drive oversight (all recruiters) | Complete | |
| Admin analytics (overview, by-branch, by-company) | Complete | |
| Admin analytics CSV export | Complete | |
| Eligibility rules engine (`evaluate()` method) | Complete | |
| Eligibility bulk re-evaluation endpoint | Complete | |
| Recruiter drive lifecycle (DRAFT → COMPLETED) | Complete | |
| Recruiter applicant pipeline (9 stages) | Complete | |
| Recruiter bulk messaging (all / shortlisted / selected) | Complete | |
| Recruiter shortlist CSV export | Complete | |
| Candidate anonymization (name gated by application) | Complete | |
| Branch seeding (JNTUH standard branches) | Complete | |
| Rate limiting (Redis + in-memory fallback) | Complete | |
| Security headers (CSP, HSTS, X-Frame-Options) | Complete | |
| Sentry error monitoring | Complete | Optional via `SENTRY_DSN` |
| Structured JSON logging | Complete | |
| Gunicorn production config | Complete | gthread, 4 threads, 120 s timeout |
| Alembic migrations — main schema | In Progress | Two versions exist; full schema relies on `db.create_all()` in development |
| `.env.example` | Planned | No template file committed |
| OpenAPI / Postman collection | Planned | No API specification file |
| Test suite | Planned | No test files found in the repository |
| Architecture diagram | Planned | |
| T-Sheet format documentation | Planned | Column expectations not documented |

---

## What's Next

The following items are ordered by impact on production readiness.

**1. Migrations — consolidate the schema into Alembic**
The two existing migrations (`f9753e60cb1a`, `0b62094fa2b3`) cover only `audit_logs`, `revoked_tokens`, and `password_reset_token`. The remaining 20+ tables are created via `db.create_all()` at startup. A full Alembic migration covering all models in `app/models/` is required before the database can be reliably version-controlled and rolled back in production.

**2. Add a `.env.example` file**
There is no template for the required environment variables. New contributors and deployment pipelines have no reference for what variables are needed or what format they take. Add a committed `.env.example` with placeholder values and descriptions matching the table in this README.

**3. Write a test suite**
No test files exist in the repository. At minimum, add:
- Unit tests for `pipeline/parser.py` and `pipeline/normalizer.py` (edge cases: blank rows, missing headers, alternate column names)
- Unit tests for `EligibilityRule.evaluate()` (boundary conditions on CGPA, backlogs, branch filters)
- Integration tests for the auth endpoints using `TestingConfig` (SQLite in-memory)

**4. Implement PDF parsing**
`pdfplumber` is listed in `requirements.txt` and imported, but `pipeline/parser.py` contains only a stub for `.pdf` files. JNTUH distributes T-Sheets in PDF format; this is a significant intake gap.

**5. Produce an OpenAPI specification**
With 94 endpoints across four modules, undocumented APIs slow recruiter integrations and admin tooling. Generate an OpenAPI 3.0 spec using `flask-openapi3` or `apispec`, and serve it at `/api/docs`.

**6. Write a T-Sheet format guide**
The normalizer's fuzzy matcher assumes specific column name patterns derived from MGIT T-Sheets. Document the expected file format (Excel structure, Row-4 header conventions, supported column synonyms) so placement cell staff can diagnose failed uploads without reading source code.

**7. Add a health-check endpoint**
`GET /health` returning database reachability and Redis status is required by Railway, Render, and most load balancers. Currently absent.

**8. Harden the async pipeline**
The background thread started by `/api/admin/tsheets/upload` is not supervised. If the process restarts mid-ingestion, the `TSheetUpload` row remains stuck in `PROCESSING` status with no recovery path. Replace the bare thread with a task queue (Celery + Redis) or add a startup cleanup that resets stale `PROCESSING` uploads to `FAILED`.

**9. Message delivery tracking**
`DriveMessage` and `StudentMessageDelivery` models are defined, but routes do not fully track per-student delivery and read status. Complete the delivery pipeline to enable accurate message open-rate reporting.

**10. Deployment documentation**
Document the Railway / Render deployment workflow (environment variable configuration, the `flask db upgrade` migration step, Supabase storage bucket setup, Resend domain verification) so the deployment is repeatable without tribal knowledge.

---

## Contributing

**Branch naming:**
```text
feat/<short-description>
fix/<short-description>
chore/<short-description>
docs/<short-description>
```

**Commit style:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`). Keep the subject line under 72 characters. Reference issue numbers in the body where applicable.

**Pull request process:**
1. Branch from `main`.
2. Keep PRs focused — one logical change per PR.
3. Ensure `flask run` starts without errors and the affected user flow works end-to-end in the browser.
4. Update this README or the relevant module description if the change adds, removes, or substantially modifies an API endpoint.

---

## License and Credits

**Institution:** Mahatma Gandhi Institute of Technology, Hyderabad

**Authors:**
- Akhilesh Kancharla (24261A0502)
- Likhitha Yepalagunta (24261A0564)

**Year:** 2026
