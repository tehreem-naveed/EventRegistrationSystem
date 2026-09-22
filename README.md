# Event Registration System

A backend REST API built with **Django** and **Django REST Framework** where users can browse events, register for them, view their own registrations and cancel them. The backend is authoritative: authentication, ownership, capacity, duplicate-registration and date rules are all enforced on the server (and, where it matters, by database constraints), so they cannot be bypassed by calling the API directly.

---

## Table of contents

1. [Features](#features)
2. [Technologies](#technologies)
3. [Architecture](#architecture)
4. [Database design](#database-design)
5. [Business rules](#business-rules)
6. [Authentication and authorization](#authentication-and-authorization)
7. [Response format and status codes](#response-format-and-status-codes)
8. [API documentation](#api-documentation)
9. [Installation (Windows PowerShell)](#installation-windows-powershell)
10. [Running the project](#running-the-project)
11. [Running the tests](#running-the-tests)
12. [Manual API testing](#manual-api-testing)
13. [Demonstration script](#demonstration-script)
14. [Project structure](#project-structure)
15. [Requirement coverage](#requirement-coverage)
16. [Design decisions](#design-decisions)
17. [Limitations and future improvements](#limitations-and-future-improvements)
18. [Publishing to GitHub](#publishing-to-github)

---

## Features

Everything listed here is implemented and covered by automated tests.

- **Event model** with validation (positive capacity, end after start, non-blank title) enforced both in Python and by database `CHECK` constraints
- **Event list and detail endpoints** (public), including `registered_count`, `available_seats` and a calculated `state` (`upcoming` / `ongoing` / `completed`)
- **Token authentication** with signup, login and logout endpoints
- **Event registration** linked to the authenticated user and to an event
- **Duplicate-registration prevention** (application check *and* a database `UNIQUE` constraint)
- **Capacity management** — cancelled registrations do not use seats
- **Cancellation** (soft-cancel: history is kept) with **re-registration** support
- **"My registrations"** endpoint that only ever returns the caller's own registrations
- **Ownership authorization** on cancellation
- **Past-event rule** — registration is closed once an event has ended
- **Transaction handling** so register/cancel are all-or-nothing, with a concurrency test for the "last seat" race
- **Django Admin** for organizers: create/edit events, inspect registrations, cancel registrations
- **Consistent JSON response and error format**
- **`seed_events` management command** that creates sample events for demos
- **100+ automated tests** (isolated test database)

## Technologies

| Purpose | Technology |
|---|---|
| Language | Python 3.10+ (developed and tested on 3.12) |
| Web framework | Django 5.2 (LTS) |
| API | Django REST Framework 3.18 |
| Database | SQLite |
| Authentication | Django's built-in `User` + DRF `TokenAuthentication` |
| Config | `python-dotenv` (`.env` file) |
| Tests | Django's built-in test runner (`APITestCase`) — no extra packages |

There is deliberately no Docker, Redis, Celery, Postgres or frontend framework: none is required for this task.

## Architecture

```
config/    Django project: settings, root URLs, response envelope + exception handler
events/    Event and Registration models, business rules, API views, admin, tests
accounts/  Signup / login / logout (issues and revokes API tokens)
```

Request flow:

```
HTTP request
   │
   ▼
urls.py ──► View (thin: auth, parse input, choose status code)
                │
                ├─► Serializer   (validates input / shapes output)
                │
                └─► services.py  (business rules inside ONE transaction)
                          │
                          ▼
                       models.py  (+ database constraints as the last line of defence)
```

**Why `Event` and `Registration` share one app:** an event's available seats are computed from its registrations, so the two are tightly coupled. Splitting them into separate apps would create a circular dependency. `accounts` is a separate app because it has a different responsibility (identity/tokens).

## Database design

```
 ┌────────────┐        ┌─────────────────────┐        ┌───────────────────┐
 │   User     │ 1    * │    Registration     │ *    1 │      Event        │
 │ (Django)   │────────│ id                  │────────│ id                │
 │            │        │ user_id   (FK)      │        │ title             │
 └────────────┘        │ event_id  (FK)      │        │ description       │
                       │ status              │        │ location          │
                       │ registered_at       │        │ start_datetime    │
                       │ cancelled_at        │        │ end_datetime      │
                       └─────────────────────┘        │ capacity          │
                                                      │ created_at        │
                                                      │ updated_at        │
                                                      └───────────────────┘
```

`Registration` is the join between users and events: one user can register for many events, and one event can have many users.

### Constraints (enforced by the database)

| Table | Constraint | Purpose |
|---|---|---|
| Event | `capacity > 0` | no zero/negative capacity |
| Event | `end_datetime > start_datetime` | no invalid time ranges |
| Event | `title != ''` | no blank title |
| Registration | `UNIQUE (user, event)` | a user can never have two rows for the same event |
| Registration | `status = REGISTERED AND cancelled_at IS NULL` **or** `status = CANCELLED AND cancelled_at IS NOT NULL` | status is always valid and consistent |

### Field notes

- `description` is **optional** (may be blank). Everything else on `Event` is required.
- `status` is a Django `TextChoices` enum: `REGISTERED` or `CANCELLED`. Arbitrary values are rejected by validation *and* by the database check above.
- `registered_at` is the time of the *current* registration; it is refreshed if the user re-registers after cancelling. `cancelled_at` is set on cancellation and cleared on re-registration.
- The event **state** (`upcoming`, `ongoing`, `completed`) is *calculated* from `start_datetime`, `end_datetime` and the current time. It is not stored, so it can never become inconsistent.

### Delete behaviour (`on_delete`)

| Relationship | Behaviour | Reason |
|---|---|---|
| `Registration.event → Event` | `PROTECT` | An event that has registrations cannot be deleted, so no registration is ever orphaned and history is preserved. In the Admin this shows a "cannot delete" page. |
| `Registration.user → User` | `CASCADE` | Deleting a user account removes that user's own registrations. |

### Registration lifecycle

```
   (no row) ──register──► REGISTERED ──cancel──► CANCELLED
                              ▲                      │
                              └──────register────────┘
                          (the SAME row is reactivated)
```

- There is **one row per (user, event)**. Re-registering after a cancellation *reactivates* that row (status → `REGISTERED`, `registered_at` refreshed, `cancelled_at` cleared). This keeps the unique constraint simple and avoids duplicate history rows.
- Cancelling is a **soft cancel**: the row stays with status `CANCELLED`, so a user's history remains visible.
- Only two transitions exist. Cancelling an already-cancelled registration and registering while already registered are both rejected with `409`.

## Business rules

| Rule | Behaviour |
|---|---|
| Active seats | `active = registrations with status REGISTERED`. Cancelled rows **never** count. |
| Available seats | `capacity − active` (never negative) |
| Capacity | Registering when `active >= capacity` → `409 event_full` |
| Duplicates | Registering while already `REGISTERED` → `409 already_registered` |
| Past events | Registration for an event whose `end_datetime` has passed → `409 event_ended`. An event that has started but not finished ("ongoing") still accepts registrations. |
| Cancelling after the event | Registrations of an ended event can no longer be cancelled → `409 event_ended` (history is frozen). |
| Shrinking capacity | An organizer cannot set an event's capacity below its current number of active registrations (validated in Admin). |
| Ownership | A user can only cancel their own registration → otherwise `403`. |

### Concurrency (the "last seat" problem)

Registering is one database transaction: *load event → check ended → check duplicate → check capacity → create/reactivate*.

- **SQLite:** `config/settings.py` sets `"transaction_mode": "IMMEDIATE"`. Each transaction takes SQLite's write lock as it *begins*, so two simultaneous requests are serialized — the second waits, then sees the updated seat count. A request that waits longer than the 20-second `timeout` receives a safe `503`.
- **Other databases:** the code also calls `select_for_update()` on the event, which provides row-level locking on PostgreSQL/MySQL (it is a no-op on SQLite).
- **Evidence:** `events/tests/test_concurrency.py` starts many threads that register at the same instant (1 seat / 8 users; 3 seats / 10 users; one user "double-clicking"; a freed seat with 5 waiting users) and asserts exact outcomes. These tests pass reliably with `IMMEDIATE` mode and fail when it is removed.
- **Limitation:** SQLite allows only one writer at a time, so under heavy write load requests queue up. That is fine for this project; a production deployment with high traffic should use PostgreSQL. This is not a distributed locking system.

## Authentication and authorization

**Authentication** ("who are you?") uses DRF **Token authentication**. Send the header:

```
Authorization: Token <your-token>
```

Get a token from `POST /api/auth/signup/` or `POST /api/auth/login/`. Tokens do not expire but are revoked by `POST /api/auth/logout/`.

**Authorization** ("are you allowed?"):

| Endpoint group | Who |
|---|---|
| Event list / detail | Anyone (public, read-only) |
| Register, list own registrations, cancel | Authenticated users only |
| Cancel a registration | Only the user who owns it (`registration.user == request.user`, checked on the server) |
| Create / edit events | Staff users through Django Admin (no public write API) |

Security properties (all covered by tests):

- **No user-ID spoofing** — the request body only accepts `event_id`. The registering user is always `request.user` (from the token). A `user_id` sent by a client is ignored.
- **No mass assignment** — `status`, `registered_at`, `id`, etc. in the request body are ignored; the server decides them.
- **No cross-user data access** — `GET /api/registrations/` filters by the authenticated user.
- **Ordinary users cannot become staff** — signup ignores `is_staff` / `is_superuser`.
- **No SQL injection surface** — only the Django ORM is used; no raw SQL.
- **No stack traces to clients** — unexpected errors return a generic JSON `500` and are logged server-side.
- **Passwords** are hashed by Django and validated with Django's password validators. Login failures give the same message for "wrong password" and "unknown user".
- **Secrets** come from `.env`, which is git-ignored.

## Response format and status codes

Every response is JSON in one of two shapes.

Success:

```json
{ "success": true, "data": { } }
```

Failure:

```json
{ "success": false, "error": "Event is full.", "code": "event_full" }
```

Validation failures additionally include `details` (per-field messages):

```json
{
  "success": false,
  "error": "Validation failed.",
  "code": "validation_error",
  "details": { "event_id": ["A valid integer is required."] }
}
```

| Status | Meaning in this API |
|---|---|
| `200 OK` | Successful read or cancellation |
| `201 Created` | Registration created (or reactivated), signup succeeded |
| `400 Bad Request` | Malformed / invalid input (`validation_error`, `parse_error`) |
| `401 Unauthorized` | Missing or invalid token (`not_authenticated`, `authentication_failed`) |
| `403 Forbidden` | Authenticated but not allowed (cancelling someone else's registration) |
| `404 Not Found` | Event or registration does not exist |
| `405 Method Not Allowed` | e.g. trying to `POST`/`PUT`/`DELETE` events through the API |
| `409 Conflict` | A valid request that conflicts with current state: `already_registered`, `event_full`, `event_ended`, `already_cancelled` |
| `503 Service Unavailable` | The database write lock could not be obtained in time (retry) |

Rule of thumb used throughout: **400 = the request itself is wrong; 409 = the request is fine but the current state of the system does not allow it.**

## API documentation

Base URL when running locally: `http://127.0.0.1:8000/api`

All request bodies are JSON (`Content-Type: application/json`).

---

### `GET /api/events/`

List events, ordered by start time.

- **Auth:** none (public)
- **Query parameters (optional):** `state=upcoming|ongoing|completed`
- **Response `200`:**

```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "title": "Python Backend Workshop",
      "description": "Hands-on session building REST APIs with Django.",
      "location": "Lahore",
      "start_datetime": "2026-10-20T18:00:00Z",
      "end_datetime": "2026-10-20T20:00:00Z",
      "capacity": 50,
      "registered_count": 13,
      "available_seats": 37,
      "state": "upcoming",
      "is_registration_open": true
    }
  ]
}
```

- **Errors:** `400 validation_error` for an unknown `state` value.
- Only counts are exposed — never the list of registered users.

---

### `GET /api/events/<id>/`

One event with the same fields as above. `available_seats = capacity − ACTIVE registrations`.

- **Auth:** none (public)
- **Response `200`:** `{ "success": true, "data": { ...event... } }`
- **Errors:** `404 not_found` — `{"success": false, "error": "Event not found.", "code": "not_found"}`

---

### `POST /api/registrations/`

Register the authenticated user for an event.

- **Auth:** required (`Authorization: Token ...`)
- **Request body:**

```json
{ "event_id": 1 }
```

Only `event_id` is used. Any other field (`user_id`, `status`, …) is ignored.

- **Response `201 Created`:**

```json
{
  "success": true,
  "data": {
    "id": 12,
    "event_id": 1,
    "user_id": 5,
    "status": "REGISTERED",
    "registered_at": "2026-09-21T12:00:00Z",
    "cancelled_at": null,
    "event": {
      "id": 1,
      "title": "Python Backend Workshop",
      "location": "Lahore",
      "start_datetime": "2026-10-20T18:00:00Z",
      "end_datetime": "2026-10-20T20:00:00Z"
    }
  }
}
```

- **Errors:**

| Status | `code` | When |
|---|---|---|
| 400 | `validation_error` | `event_id` missing, not an integer, ≤ 0, or absurdly large |
| 400 | `parse_error` | body is not valid JSON |
| 401 | `not_authenticated` / `authentication_failed` | no token / bad token |
| 404 | `not_found` | no event with that id |
| 409 | `already_registered` | you already have an active registration |
| 409 | `event_full` | active registrations have reached `capacity` |
| 409 | `event_ended` | the event has already finished |

If the user previously cancelled, the same registration row is reactivated and this endpoint also returns `201`.

---

### `GET /api/registrations/`

The authenticated user's own registrations (newest first), including cancelled ones.

- **Auth:** required
- **Query parameters (optional):** `status=REGISTERED|CANCELLED`
- **Response `200`:** `{ "success": true, "data": [ ...registration objects (same shape as above)... ] }`
- **Errors:** `401`, and `400 validation_error` for an unknown `status`.

---

### `DELETE /api/registrations/<id>/`

Cancel one of your registrations. The row is kept with `status: "CANCELLED"` and a `cancelled_at` time, and the seat becomes available again.

- **Auth:** required, and the registration must belong to the caller
- **Response `200`:** the updated registration (`status: "CANCELLED"`)
- **Errors:**

| Status | `code` | When |
|---|---|---|
| 401 | `not_authenticated` / `authentication_failed` | no / bad token |
| 403 | `permission_denied` | the registration belongs to another user |
| 404 | `not_found` | no registration with that id |
| 409 | `already_cancelled` | it is already cancelled |
| 409 | `event_ended` | the event has finished, so the registration is frozen |

---

### Authentication endpoints

| Endpoint | Auth | Body | Success |
|---|---|---|---|
| `POST /api/auth/signup/` | none | `{"username", "password", "email"?}` | `201` `{"token", "user": {"id", "username"}}` |
| `POST /api/auth/login/` | none | `{"username", "password"}` | `200` `{"token", "user": {...}}` |
| `POST /api/auth/logout/` | token | — | `200` — the token is deleted |

Errors: `400 validation_error` (duplicate username, weak password, invalid email, missing fields), `401 authentication_failed` (wrong credentials).

---

## Installation (Windows PowerShell)

Requires **Python 3.10 or newer** and (optionally) Git.

```powershell
# 1. Open PowerShell in the project folder
cd EventRegistrationSystem

# 2. Create a virtual environment
python -m venv .venv

# 3. Activate it
.venv\Scripts\Activate.ps1

# 4. Install dependencies
pip install -r requirements.txt
```

**If step 3 fails with "running scripts is disabled on this system"**, you do not need to change any system-wide setting. Either allow scripts for *this PowerShell window only*:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

or skip activation entirely and call the virtual environment's Python directly:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe manage.py runserver
```

(In that case, replace `python` with `.venv\Scripts\python.exe` in every command below.)

macOS / Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

### Environment configuration

Settings that differ per machine live in a `.env` file, which is **not** committed to Git.

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Open `.env` and paste the generated value after `SECRET_KEY=`.

| Variable | Meaning | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key. **Required** unless `DEBUG=True`. | — |
| `DEBUG` | `True` for local development (also lets `runserver` serve the Admin's CSS). Use `False` for anything public. | `False` |
| `ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` |
| `TIME_ZONE` | Time zone used to *display* datetimes in Admin and the API (e.g. `Asia/Karachi`) | `UTC` |

**Time zones:** all datetimes are timezone-aware (`USE_TZ = True`) and are *stored* in UTC. The API returns ISO-8601 with an offset (`Z` when `TIME_ZONE=UTC`). The Admin displays and accepts times in `TIME_ZONE`; set it to your local zone if you want to type local times when creating events.

## Running the project

```powershell
# Create the database tables from the committed migrations
python manage.py migrate

# Create an admin (organizer) account - you will be prompted for a password.
# No credentials are stored anywhere in the project.
python manage.py createsuperuser

# (Optional) create 4 sample events for a demo
python manage.py seed_events

# Start the server
python manage.py runserver
```

- API: <http://127.0.0.1:8000/api/events/>
- Admin: <http://127.0.0.1:8000/admin/>

The migration files are already included in the repository. If you change a model, generate a new one with `python manage.py makemigrations` and apply it with `python manage.py migrate`.

`seed_events` creates: a workshop (50 seats), a **2-seat** roundtable (ideal for demonstrating capacity), a session already **in progress** (registration still open), and a **finished** event (registration closed). It is safe to run repeatedly.

## Running the tests

```powershell
python manage.py test
```

- Uses an isolated temporary test database (`test_db.sqlite3`, created and deleted automatically). Your real `db.sqlite3` is never touched, and no manually created users or events are needed.
- If a previous run was killed and left the test database behind, run `python manage.py test --noinput`.
- Runs in a few seconds. For details of each test: `python manage.py test -v 2`.

What is covered:

| Area | Examples |
|---|---|
| Events API | list, detail, 404, ordering, `state` filter, available seats ignore cancelled rows, no N+1 queries, read-only (POST/PUT/PATCH/DELETE → 405), no user data leaked |
| Registration | success (DB state, correct user/event/status), 401 without/with bad token, duplicate → 409, capacity limit, unknown event → 404, invalid bodies → 400, `user_id`/`status` spoofing ignored, past event rejected, ongoing event allowed |
| Cancellation | own registration cancelled and persisted, seat freed, other user's registration → 403 (and unchanged), 401, 404, double-cancel → 409, ended event → 409 |
| Re-registration | same row reactivated, duplicates blocked again afterwards, seat taken by someone else → 409 |
| My registrations | only own rows, cancelled history, `status` filter, no N+1 |
| Models / DB | invalid dates, zero/negative capacity, blank title, missing fields, invalid datetime strings, DB constraints reject bad rows even when validation is skipped, unique `(user, event)`, invalid status, `PROTECT` / `CASCADE` |
| Admin | create event, invalid data rejected, capacity cannot drop below active registrations, non-staff blocked, registrations can't be created by hand, cancel action |
| Auth | signup/login/logout, weak password, duplicate username, cannot self-promote to staff, no username enumeration |
| Concurrency | last-seat race with threads, double-click, freed seat with many waiters |
| Safe failure | unexpected error → generic JSON `500` (no traceback), lock timeout → `503`, database error rolls back everything, unique constraint as last line of defence |
| End-to-end | full documented flow using real tokens |

## Manual API testing

### PowerShell

Start the server in one window (`python manage.py runserver`) and run this in another.
First define a small helper that prints error bodies as well as successes:

```powershell
$Base = "http://127.0.0.1:8000/api"

function Invoke-Api {
    param([string]$Method, [string]$Path, $Body = $null, [string]$Token = $null)
    $params = @{
        Method      = $Method
        Uri         = "$Base$Path"
        ContentType = "application/json"
        Headers     = @{}
    }
    if ($Token) { $params.Headers["Authorization"] = "Token $Token" }
    if ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json) }
    try {
        Invoke-RestMethod @params
    } catch {
        Write-Host "HTTP $([int]$_.Exception.Response.StatusCode)" -ForegroundColor Yellow
        $_.ErrorDetails.Message
    }
}
```

Then walk through the flow (run `python manage.py seed_events` first, or create events in the Admin):

```powershell
# Public: list events and view one
(Invoke-Api GET "/events/").data | Format-Table id, title, state, capacity, available_seats
(Invoke-Api GET "/events/2/").data

# Create two users (signup returns a token)
$alice = Invoke-Api POST "/auth/signup/" @{ username = "alice"; email = "alice@example.com"; password = "S3cure-Pass-2026" }
$bob   = Invoke-Api POST "/auth/signup/" @{ username = "bob"; password = "S3cure-Pass-2026" }
$aliceToken = $alice.data.token
$bobToken   = $bob.data.token
# (Later: log in again with  Invoke-Api POST "/auth/login/" @{ username = "alice"; password = "S3cure-Pass-2026" })

# Not logged in -> 401
Invoke-Api POST "/registrations/" @{ event_id = 2 }

# Alice registers (event 2 has only 2 seats). The bogus user_id is ignored.
$reg = Invoke-Api POST "/registrations/" @{ event_id = 2; user_id = 999 } -Token $aliceToken
$reg.data
$regId = $reg.data.id

# Duplicate -> 409 already_registered
Invoke-Api POST "/registrations/" @{ event_id = 2 } -Token $aliceToken

# Bob takes the second seat; a third user would now get 409 event_full
Invoke-Api POST "/registrations/" @{ event_id = 2 } -Token $bobToken
(Invoke-Api GET "/events/2/").data | Select-Object title, capacity, registered_count, available_seats

# Finished event (id 4) -> 409 event_ended; unknown event -> 404; bad body -> 400
Invoke-Api POST "/registrations/" @{ event_id = 4 } -Token $aliceToken
Invoke-Api POST "/registrations/" @{ event_id = 9999 } -Token $aliceToken
Invoke-Api POST "/registrations/" @{ event_id = "abc" } -Token $aliceToken

# My registrations (each user sees only their own)
(Invoke-Api GET "/registrations/" -Token $aliceToken).data | Format-Table id, event_id, status
(Invoke-Api GET "/registrations/" -Token $bobToken).data   | Format-Table id, event_id, status

# Bob tries to cancel Alice's registration -> 403
Invoke-Api DELETE "/registrations/$regId/" -Token $bobToken

# Alice cancels her own registration -> status CANCELLED, seat freed
(Invoke-Api DELETE "/registrations/$regId/" -Token $aliceToken).data
(Invoke-Api GET "/events/2/").data | Select-Object title, capacity, registered_count, available_seats

# Cancelling again -> 409 already_cancelled
Invoke-Api DELETE "/registrations/$regId/" -Token $aliceToken

# Alice can register again (the same registration row is reactivated)
Invoke-Api POST "/registrations/" @{ event_id = 2 } -Token $aliceToken

# Log out (revokes the token)
Invoke-Api POST "/auth/logout/" -Token $bobToken
```

> If the error body prints as empty in Windows PowerShell 5.1, use PowerShell 7 or Postman.

### Postman / any HTTP client

1. `POST http://127.0.0.1:8000/api/auth/signup/` with a JSON body → copy `data.token`.
2. On protected requests add the header `Authorization: Token <token>` (or use Postman's *Headers* tab).
3. Set `Content-Type: application/json` for `POST` bodies.

### curl (macOS / Linux / Git Bash)

```bash
B=http://127.0.0.1:8000/api
TOKEN=$(curl -s -X POST $B/auth/signup/ -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"S3cure-Pass-2026"}' | python -c "import sys,json; print(json.load(sys.stdin)['data']['token'])")

curl -s $B/events/
curl -s -X POST $B/registrations/ -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" -d '{"event_id":2}'
curl -s $B/registrations/ -H "Authorization: Token $TOKEN"
curl -s -X DELETE $B/registrations/1/ -H "Authorization: Token $TOKEN"
```

## Demonstration script

1. `python manage.py runserver`
2. Open `/admin/`, log in with your superuser, and **create an event** (try an end time before the start time and capacity `0` to show they are rejected). Or run `seed_events`.
3. Show `GET /api/events/` (public).
4. Show `GET /api/events/2/` — note `available_seats`.
5. **Sign up / log in** as a user and copy the token.
6. **Register** with `POST /api/registrations/`.
7. Show `GET /api/registrations/`.
8. **Register again** → `409 already_registered`.
9. Register a second user, then a third → `409 event_full` (event 2 has 2 seats). Show `available_seats` is `0`.
10. **Cancel** one registration with `DELETE /api/registrations/<id>/`. Try cancelling someone else's → `403`.
11. Show `available_seats` increased and the third user can now register.
12. In `/admin/` show the registrations list, filters and the cancel action.
13. Run `python manage.py test`.

## Project structure

```
EventRegistrationSystem/
├── manage.py
├── requirements.txt
├── .env.example            # template for local configuration (copy to .env)
├── .gitignore
├── README.md
├── config/
│   ├── settings.py         # env-driven settings, SQLite + IMMEDIATE transactions, DRF config
│   ├── urls.py             # /admin/, /api/auth/, /api/
│   └── api.py              # response envelope + global exception handler
├── events/
│   ├── models.py           # Event, Registration (+ constraints, calculated state/seats)
│   ├── services.py         # register_user_for_event / cancel_registration (transactions)
│   ├── exceptions.py       # 409 business-rule errors
│   ├── serializers.py
│   ├── views.py            # thin API views
│   ├── urls.py
│   ├── admin.py
│   ├── migrations/0001_initial.py
│   ├── management/commands/seed_events.py
│   └── tests/              # models, events API, registrations API, admin, workflow, concurrency
└── accounts/
    ├── serializers.py      # signup / login
    ├── views.py            # signup, login, logout (tokens)
    ├── urls.py
    └── tests.py
```

## Requirement coverage

A quick checklist of what a typical event-registration backend needs to provide, and where each piece lives in this project:

| Requirement | Where it is implemented |
|---|---|
| Backend web framework with routing and business logic | Django + DRF (`config/`) |
| Event model | `events.models.Event` |
| Registration model, linking users to events | `events.models.Registration` |
| Registrations linked to users and events | `Registration.user` (FK to `auth.User`) and `Registration.event` (FK to `Event`) |
| View event list | `GET /api/events/` |
| View event details | `GET /api/events/<id>/` |
| Submit a registration | `POST /api/registrations/` |
| View your own registrations | `GET /api/registrations/` (own only) |
| Cancel your own registration | `DELETE /api/registrations/<id>/` (owner only, soft-cancel) |
| Admin panel | Django Admin for `Event` and `Registration` (`events/admin.py`) |
| Authentication for organizers/users | Django staff accounts log in to the Admin; users authenticate to the API with tokens (`accounts/`) |

## Design decisions

- **Soft-cancel instead of deleting** the registration row: keeps history and makes re-registration a simple "reactivate".
- **One row per (user, event)** with a database `UNIQUE` constraint: duplicates are impossible even if application code is bypassed.
- **Business rules live in `events/services.py`**, not in views or serializers, so they apply however the code is invoked.
- **`403` (not `404`) for another user's registration.** It makes the ownership check explicit. The trade-off is that it reveals that the id exists; returning `404` would hide that.
- **Events are read-only in the API.** Organizers manage them in the Admin, which keeps the public surface small and avoids needing event-level permissions.
- **Calculated event state** rather than a stored status field.
- **Explicit ordering on annotated queries.** Django ignores `Meta.ordering` on `GROUP BY` queries, so the list view orders explicitly (a test caught this).

## Limitations and future improvements

These are **not** implemented:

- No email notifications, reminders, payments, QR codes/tickets, waiting lists or analytics
- No organizer dashboard beyond the Django Admin
- No API for creating/editing events (Admin only) and no event soft-delete/archive (events with registrations simply cannot be deleted)
- No pagination on list endpoints and no rate limiting/throttling (e.g. on login)
- Tokens do not expire (they can be revoked with logout)
- No password reset or email verification
- SQLite serializes writes (see [Concurrency](#concurrency-the-last-seat-problem)); use PostgreSQL for real production traffic
- Only `DEBUG=True` is intended for the local demo; a real deployment would also need HTTPS, `DEBUG=False`, proper static-file serving and a production web server

## Publishing to GitHub

```powershell
git init
git add .
git status          # check: no .env, no db.sqlite3, no .venv, no __pycache__
git commit -m "Initial commit: Event Registration System"
git branch -M main
git remote add origin https://github.com/<your-username>/EventRegistrationSystem.git
git push -u origin main
```

Create the repository named `EventRegistrationSystem` on GitHub first. `.gitignore` already excludes `.env`, `.venv/`, `db.sqlite3`, `test_db.sqlite3` and cache files.
