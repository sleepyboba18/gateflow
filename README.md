# GateForge

GateForge is a production-oriented Python backend foundation for an API gateway and rate limiter. Module 1 provides the Flask application shell, configurable CORS and Socket.IO initialization, a plain SQLAlchemy ORM layer for Supabase PostgreSQL, the initial `User` model, startup table initialization, logging, and health/error endpoints.

## Technology

- Python 3.11+
- Flask, Flask-CORS, Flask-SocketIO
- SQLAlchemy 2.x ORM
- PostgreSQL through Supabase
- python-dotenv, psycopg2-binary, PyJWT, and Werkzeug

## Structure

```text
main.py                 Application entry point
config.py               Environment-backed configuration
app/database/           SQLAlchemy base, engine, and sessions
app/models/              ORM models
app/routes/              HTTP route package
app/services/            Service package
app/middleware/          JWT and API-key authentication
app/sockets/             Socket.IO package
tests/                   Test package
```

## Environment Setup

Create and activate a virtual environment:

```text
python -m venv venv
```

On Windows:

```text
venv\Scripts\activate
```

On macOS or Linux:

```text
source venv/bin/activate
```

Install dependencies:

```text
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then set `DATABASE_URL` to the connection string supplied by your Supabase project. Keep credentials and secrets only in `.env`; never commit that file.

## Running

```text
python main.py
```

The default server address is `http://localhost:5000`. The health endpoint is:

```text
GET /health
```

It reports `database: connected` only when GateForge can reach PostgreSQL. If the database is not configured or unavailable, it returns HTTP 503 without exposing connection details.

## Authentication

User management uses email and password credentials, Werkzeug password hashing, and short-lived JWT access tokens:

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
GET  /api/v1/auth/me
```

Send JWTs as `Authorization: Bearer <token>`. JWT settings are configured with `JWT_SECRET_KEY`, `JWT_ALGORITHM`, and `JWT_EXPIRATION_MINUTES`.

## API Keys

Authenticated users can manage keys for future gateway clients:

```text
POST   /api/v1/api-keys
GET    /api/v1/api-keys
DELETE /api/v1/api-keys/<api_key_id>
```

API keys are generated securely, hashed before storage in Supabase PostgreSQL, and shown in plaintext only in the creation response. List responses expose metadata but never the plaintext key or its hash. The reusable API-key validator accepts the `X-API-Key` header and rejects inactive, revoked, or expired keys.

Tests require `TEST_DATABASE_URL`, which must point to an isolated PostgreSQL database. No SQLite database is used.

## License

GateForge is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
