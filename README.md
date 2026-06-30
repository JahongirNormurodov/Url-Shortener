# URL Shortener

> A production-ready URL shortening service built with **FastAPI**, **SQLAlchemy 2.0 (async)**, and **JWT authentication**.

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.136+-009688?logo=fastapi&logoColor=white)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0%20async-D71F00)
![pytest](https://img.shields.io/badge/Tested%20with-pytest-0A9EDC?logo=pytest&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)

Turns long URLs into short, shareable 7-character codes. Redirects visitors to the original destination and scopes every link strictly to its owner. Built as a learning-oriented implementation following a technical specification — code comments are in Uzbek; this README is in English.

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Project Structure](#project-structure)
- [How Short Codes Work](#how-short-codes-work)
- [Security](#security)
- [Running Tests](#running-tests)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Features

| Area | Details |
|---|---|
| **Auth** | Register, login, refresh tokens (with rotation), logout, full `/me` CRUD |
| **Links** | Shorten URLs, custom aliases, expiration dates, idempotent shortening, cursor-paginated list with search, get, update, soft/hard delete |
| **Redirect** | Public `GET /{code}` → 302, with 404 (not found) and 410 (deleted) handling |
| **Security** | Argon2 password hashing, SHA-256 hashed refresh tokens, owner-scoped queries (no IDOR), URL validation + SSRF guard |
| **Health check** | `GET /health` endpoint for uptime monitoring |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Web framework | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| Database (prod) | PostgreSQL via `asyncpg` |
| Database (dev/test) | SQLite via `aiosqlite` |
| Auth | PyJWT (HS256) + `pwdlib[argon2]` |
| Validation | Pydantic v2 + `pydantic-settings` |
| Testing | pytest + httpx (ASGI transport) |
| Package manager | [uv](https://github.com/astral-sh/uv) |

---

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed

### 1. Clone the repository

```bash
git clone https://github.com/your-username/url-shortener.git
cd url-shortener
```

### 2. Install dependencies

```bash
uv sync
```

This installs all production and dev dependencies into an isolated virtual environment.

### 3. Configure environment

```bash
cp .env.example .env
```

The defaults work with SQLite out of the box — no Postgres needed for local development. See [Environment Variables](#environment-variables) for all available options.

### 4. Run the server

```bash
uv run uvicorn app.main:app --reload
```

The app will be available at [http://localhost:8000](http://localhost:8000).

Auto-generated API docs:
- **Swagger UI** → [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc** → [http://localhost:8000/redoc](http://localhost:8000/redoc)

> **Note:** Database tables are auto-created on startup for development convenience. Production deployments should use Alembic migrations.

---

## Environment Variables

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `url-shortener` | Application name (used in logs) |
| `ENVIRONMENT` | `development` | `development` or `production` |
| `BASE_URL` | `http://localhost:8000` | Base URL used to build `short_url` in responses |
| `DATABASE_URL` | `sqlite+aiosqlite:///./dev.db` | Database connection string. Use `postgresql+asyncpg://...` for production |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string (reserved for future caching / rate limiting) |
| `JWT_SECRET` | `change-me-...` | **Required in production** — must be at least 32 random bytes |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_TTL_SECONDS` | `900` | Access token lifetime (15 minutes) |
| `REFRESH_TOKEN_TTL_SECONDS` | `2592000` | Refresh token lifetime (30 days) |
| `URL_RESOLVE_DNS` | `true` | Resolve URLs via DNS for SSRF protection. Set to `false` in offline/test environments |

> ⚠️ **Never commit your `.env` file.** It is already in `.gitignore`.

---

## API Reference

All API endpoints are prefixed with `/api/v1`. Authenticated endpoints require the header:

```
Authorization: Bearer <access_token>
```

Redirect (`GET /{code}`) is public — no authentication needed.

### Auth & User

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/auth/register` | No | Create a new account |
| `POST` | `/api/v1/auth/login` | No | Get an access + refresh token pair |
| `POST` | `/api/v1/auth/refresh` | No | Rotate refresh token, get a new access token |
| `POST` | `/api/v1/auth/logout` | Yes | Revoke all active refresh tokens |
| `GET` | `/api/v1/me` | Yes | Get current user profile |
| `PATCH` | `/api/v1/me` | Yes | Update display name or email |
| `POST` | `/api/v1/me/change-password` | Yes | Change password (revokes all refresh tokens) |
| `DELETE` | `/api/v1/me` | Yes | Delete account (password-confirmed) |

### Links

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/v1/shorten` | Yes | Create a short link |
| `GET` | `/api/v1/links` | Yes | List your links (`?limit=&cursor=&q=`) |
| `GET` | `/api/v1/links/{code}` | Yes | Get link metadata by code |
| `PATCH` | `/api/v1/links/{code}` | Yes | Update `long_url`, alias, expiry, or active status |
| `DELETE` | `/api/v1/links/{code}` | Yes | Soft delete (add `?hard=true` for permanent deletion) |
| `GET` | `/{code}` | No | Public redirect → 302 |

### Quick Example

```bash
# 1. Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"ali@example.com","password":"password123","display_name":"Ali"}'

# 2. Login → save access_token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"ali@example.com","password":"password123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 3. Shorten a URL
curl -X POST http://localhost:8000/api/v1/shorten \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/a/very/long/path/that/is/hard/to/share"}'

# 4. Follow the short link (replace <code> with the code from the response)
curl -i http://localhost:8000/<code>
```

---

## Project Structure

```
url-shortener/
├── app/
│   ├── main.py              # FastAPI app, lifespan hooks, router wiring
│   ├── core/
│   │   ├── config.py        # pydantic-settings — all config from env vars
│   │   ├── base62.py        # id ⇄ short code (obfuscated, collision-free)
│   │   ├── security.py      # Argon2 password hashing + SHA-256 token hashing
│   │   ├── tokens.py        # JWT access/refresh token create & decode
│   │   └── urls.py          # URL validation + SSRF guard (DNS resolution)
│   ├── db/
│   │   ├── models.py        # SQLAlchemy models: User, RefreshToken, Link
│   │   └── session.py       # Async engine + get_db dependency
│   ├── api/
│   │   ├── deps.py          # Shared dependencies: get_db, get_current_user
│   │   └── routes/
│   │       ├── auth.py      # /auth/* + /me endpoints
│   │       ├── links.py     # /shorten, /links/* endpoints
│   │       └── redirect.py  # Public GET /{code} → 302 redirect
│   └── schemas/             # Pydantic request/response models
├── tests/
│   ├── conftest.py          # Fixtures: in-memory SQLite, test client, auth_client
│   ├── test_auth.py         # Auth flow tests (register/login/refresh/logout/me)
│   ├── test_links.py        # Link CRUD, redirect, SSRF, ownership isolation
│   └── test_base62.py       # Base62 encode/decode unit tests
├── .env.example             # Environment variable template
├── pyproject.toml           # Project metadata + dependencies (uv)
└── uv.lock                  # Locked dependency tree
```

---

## How Short Codes Work

Each new link receives a monotonic auto-increment `id` from the database. To avoid guessable sequential codes, the id is passed through a **reversible modular-multiplication permutation** before encoding:

```
db_id  →  permute(id)  →  base62_encode(permuted_id)  →  left-pad to 7 chars
```

- **Collision-free** — derived from a unique DB id
- **Unguessable** — sequential ids map to non-sequential codes
- **Fully reversible** — `code_to_id()` decodes back to the original id
- Custom aliases bypass the permutation and are stored directly

See [`app/core/base62.py`](app/core/base62.py) for the full implementation.

---

## Security

This project follows several security best practices:

| Concern | Implementation |
|---|---|
| Password storage | Hashed with **Argon2** (via `pwdlib`). Raw password never stored or returned in responses |
| Refresh tokens | Stored **SHA-256 hashed** in the database. Logout and password changes revoke all tokens |
| Ownership isolation | All link/stats queries filter by the JWT's `user_id` — requesting another user's link returns **404**, not 403, to avoid leaking existence |
| URL validation | Rejects non-`http(s)` schemes (e.g., `javascript:`, `file:`) and blocks **SSRF targets** — loopback, private ranges (RFC 1918), and link-local IPs |
| SSRF DNS check | When `URL_RESOLVE_DNS=true`, the destination hostname is resolved and the resulting IPs are validated against the blocklist |
| Token rotation | Each refresh rotates the token. Reusing an old refresh token immediately returns **401** |

---

## Running Tests

Tests use an isolated in-memory SQLite database and disable outbound DNS — no external services required.

```bash
uv run pytest
```

Run with verbose output:

```bash
uv run pytest -v
```

Run a specific test file:

```bash
uv run pytest tests/test_auth.py -v
```

### Test Coverage

| File | What's tested |
|---|---|
| `test_auth.py` | Registration (success, duplicate, weak password), login, `/me` access, token refresh & rotation, logout + token revocation, profile update & password change |
| `test_links.py` | Shorten + redirect, custom aliases, idempotency, unsafe URL rejection, SSRF rejection, 404 on unknown code, 410 on soft-deleted links, list/get, update + re-redirect, ownership isolation (user B cannot see user A's links) |
| `test_base62.py` | Base62 encode/decode round-trip |

---

## Roadmap

The current build covers **MVP + core auth** (spec phases 1–2 plus URL validation from phase 7).

Planned for future phases:

- [ ] **Redis caching** — cache redirects to skip DB on hot codes
- [ ] **Rate limiting** — per-IP and per-user request throttling via Redis
- [ ] **Click analytics** — track redirect events, expose stats endpoints
- [ ] **API keys** — machine-to-machine authentication
- [ ] **Bulk shortening** — shorten multiple URLs in a single request
- [ ] **QR codes** — generate QR images for short links
- [ ] **UTM parameter injection** — append UTM tags automatically
- [ ] **Password-protected links** — require a PIN to access a redirect
- [ ] **Safe Browsing checks** — integrate Google Safe Browsing API
- [ ] **Smart routing** — geo/device/A·B split routing per link
- [ ] **Webhooks** — notify external services on redirect events
- [ ] **Alembic migrations** — schema version control for production
- [ ] **Docker Compose** — one-command local stack (app + Postgres + Redis)

---

## Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Install dependencies: `uv sync`
4. Make your changes and add tests
5. Run the test suite: `uv run pytest`
6. Commit your changes: `git commit -m "feat: add my feature"`
7. Push and open a Pull Request

Please keep code style consistent with the existing codebase. Docstrings may be in Uzbek (as per the project convention) or English.

---

## License

This project is licensed under the [MIT License](LICENSE).
