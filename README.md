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

## API Management

All API and route management endpoints require a JWT for an authenticated user:

```text
POST   /api/v1/apis
GET    /api/v1/apis
GET    /api/v1/apis/<api_id>
PUT    /api/v1/apis/<api_id>
DELETE /api/v1/apis/<api_id>

POST   /api/v1/apis/<api_id>/routes
GET    /api/v1/apis/<api_id>/routes
PUT    /api/v1/apis/<api_id>/routes/<route_id>
DELETE /api/v1/apis/<api_id>/routes/<route_id>
```

An API registration contains a public slug, an HTTP(S) upstream base URL, and a timeout. Routes match an uppercase HTTP method and public path to an optional upstream target path. Upstream URLs are checked against common private and loopback address ranges to reduce SSRF risk.

## Gateway

Gateway traffic uses:
```text
/gateway/<api_slug>/<path>
```

Every gateway request requires an `X-API-Key` belonging to the owner of the registered API. GateForge resolves the configured route, forwards safe headers, query parameters, and the raw request body, then returns the upstream status and safe response headers. Requests receive a generated or preserved `X-Request-ID`, which is returned to the client and included in gateway logs. Upstream timeouts return `504`; connection failures return `502`.

## Rate Limits

Rate limits are stored in PostgreSQL and use a fixed-window algorithm. Limits may apply to an entire API or to one route; an active route-specific limit takes precedence over the API-wide limit. GateForge does not use Redis for rate limiting.

```text
POST   /api/v1/apis/<api_id>/rate-limits
GET    /api/v1/apis/<api_id>/rate-limits
PUT    /api/v1/apis/<api_id>/rate-limits/<rate_limit_id>
DELETE /api/v1/apis/<api_id>/rate-limits/<rate_limit_id>
```

Counters are keyed by API key, limit, and UTC window and are incremented with a PostgreSQL atomic upsert. Rejected requests return `429 Too Many Requests`, `Retry-After`, and `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers.

## Traffic

Gateway requests are persisted as traffic records, including request IDs, status codes, durations, request/response sizes, rate-limit decisions, and controlled error types. Credentials and sensitive authentication headers are not stored.

```text
GET /api/v1/apis/<api_id>/traffic
GET /api/v1/apis/<api_id>/traffic/summary
```

Traffic history supports database-level pagination and filtering by method, status, route, API key, and time range. The summary endpoint performs PostgreSQL aggregation for totals, successful requests, client/server errors, rate-limited requests, and average duration.

## Plans and Quotas

Administrators manage plans and their policies:

```text
POST   /api/v1/plans
GET    /api/v1/plans
GET    /api/v1/plans/<plan_id>
PUT    /api/v1/plans/<plan_id>
DELETE /api/v1/plans/<plan_id>

POST   /api/v1/plans/<plan_id>/rate-limits
GET    /api/v1/plans/<plan_id>/rate-limits
PUT    /api/v1/plans/<plan_id>/rate-limits/<rate_limit_id>
DELETE /api/v1/plans/<plan_id>/rate-limits/<rate_limit_id>

POST   /api/v1/plans/<plan_id>/quotas
GET    /api/v1/plans/<plan_id>/quotas
PUT    /api/v1/plans/<plan_id>/quotas/<quota_id>
DELETE /api/v1/plans/<plan_id>/quotas/<quota_id>
```

New API keys use the active assigned plan, or the active default Free plan when no assignment exists. Expired or inactive assignments fall back safely. Effective limits follow route override, API override, plan limit, then no limit; all applicable short windows and daily/monthly quotas must pass. The key usage endpoint is `GET /api/v1/api-keys/<api_key_id>/usage`.

Plan and quota counters are maintained synchronously with PostgreSQL atomic upserts. PostgreSQL/Supabase is the authoritative state store; Redis is not required or used.

## Analytics

Analytics queries aggregate the persistent `TrafficLog` table in PostgreSQL and default to the previous 24 hours. All endpoints require JWT authentication and API ownership or admin access:

```text
GET /api/v1/apis/<api_id>/analytics
GET /api/v1/apis/<api_id>/analytics/timeseries?granularity=hour
GET /api/v1/apis/<api_id>/analytics/routes
GET /api/v1/apis/<api_id>/analytics/api-keys
GET /api/v1/apis/<api_id>/analytics/status-codes
GET /api/v1/apis/<api_id>/analytics/latency
GET /api/v1/apis/<api_id>/analytics/errors
```

Use `from` and `to` ISO 8601 UTC query parameters for a custom time range. Results include request volume, status/error groups, latency, rate-limited traffic, and time-series data without loading the full traffic table into Python.

## Socket.IO Monitoring

Monitoring connections provide a JWT in the connection payload as `{ "token": "<JWT>" }`. Active users automatically join `user:<user_id>`. Authorized clients may join `api:<api_id>` and `api_key:<api_key_id>` rooms; owners and admins are checked against PostgreSQL.

Supported events are `connect`, `disconnect`, `join_api`, `leave_api`, `join_api_key`, `leave_api`, `join_user`, and `leave_user`. Gateway notifications use `gateway:traffic`, `gateway:rate_limit`, and `gateway:error`; API-room joins receive `monitoring:snapshot`. Socket.IO is transient notification transport only, while PostgreSQL remains the persistent source of truth. Event delivery failures do not fail HTTP gateway responses, and no Redis message queue is used.

## Scopes

Scopes provide fine-grained API-key authorization. Route scopes take precedence over API scopes; when a route has no scopes, its API scopes are required. Global scope creation is admin-only, while API owners and admins manage associations.

```text
POST   /api/v1/scopes
GET    /api/v1/scopes
PUT    /api/v1/scopes/<scope_id>
DELETE /api/v1/scopes/<scope_id>
POST   /api/v1/api-keys/<api_key_id>/scopes
GET    /api/v1/api-keys/<api_key_id>/scopes
DELETE /api/v1/api-keys/<api_key_id>/scopes/<scope_id>
POST   /api/v1/apis/<api_id>/scopes
GET    /api/v1/apis/<api_id>/scopes
DELETE /api/v1/apis/<api_id>/scopes/<scope_id>
POST   /api/v1/apis/<api_id>/routes/<route_id>/scopes
GET    /api/v1/apis/<api_id>/routes/<route_id>/scopes
DELETE /api/v1/apis/<api_id>/routes/<route_id>/scopes/<scope_id>
```

## Gateway Policies

Route policies override API policies and can restrict query parameters, request bodies, file uploads, and body size. Safe request/response header policies and upstream authentication are supported without exposing credentials. Protected headers, CRLF injection, arbitrary code, and unsafe upstream destinations remain blocked.

```text
GET /api/v1/apis/<api_id>/policy
PUT /api/v1/apis/<api_id>/policy
GET /api/v1/apis/<api_id>/routes/<route_id>/policy
PUT /api/v1/apis/<api_id>/routes/<route_id>/policy
POST   /api/v1/apis/<api_id>/headers
GET    /api/v1/apis/<api_id>/headers
DELETE /api/v1/apis/<api_id>/headers/<header_policy_id>
```

## Reliability

Circuit breakers use PostgreSQL-backed `closed`, `open`, and `half_open` states. An open circuit stops upstream calls with `503` after the configured failure threshold; after the recovery timeout, safe probe requests can test recovery. Only `GET`, `HEAD`, and `OPTIONS` are retried by default, with a bounded timeout budget. Health states are `healthy`, `degraded`, `unhealthy`, and `unknown`.

```text
POST   /api/v1/apis/<api_id>/circuit-breakers
GET    /api/v1/apis/<api_id>/circuit-breakers
PUT    /api/v1/apis/<api_id>/circuit-breakers/<breaker_id>
DELETE /api/v1/apis/<api_id>/circuit-breakers/<breaker_id>
POST   /api/v1/apis/<api_id>/circuit-breakers/<breaker_id>/reset
GET    /api/v1/apis/<api_id>/health
GET    /api/v1/apis/<api_id>/routes/<route_id>/health
GET    /api/v1/health/upstreams
POST   /api/v1/apis/<api_id>/health/check
GET    /api/v1/apis/<api_id>/analytics/reliability
GET    /api/v1/apis/<api_id>/analytics/reliability/timeseries
```

Reliability availability excludes `401`, `403`, and `429` policy/client rejections and measures eligible upstream successes against `502`, `503`, and `504` failures. `gateway:health` and `gateway:circuit` provide transient monitoring notifications. PostgreSQL remains authoritative; Redis and background workers are not used.

## API Versions and Schemas

Versioned gateway traffic uses the canonical URL `/gateway/<api>/<version>/<route>`, such as `/gateway/weather/v2/current`. Versions use the lifecycle states `development`, `active`, `deprecated`, `sunset`, and `disabled`. Deprecated responses include `Deprecation: true` and an optional `Sunset` header; sunset or disabled versions do not reach upstream services.

```text
POST   /api/v1/apis/<api_id>/versions
GET    /api/v1/apis/<api_id>/versions
GET    /api/v1/apis/<api_id>/versions/<version_id>
PUT    /api/v1/apis/<api_id>/versions/<version_id>
DELETE /api/v1/apis/<api_id>/versions/<version_id>
POST   /api/v1/apis/<api_id>/versions/<version_id>/routes
GET    /api/v1/apis/<api_id>/versions/<version_id>/routes
```

JSON Schemas are self-contained Draft 2020-12 definitions and are resolved from route to version scope. Request schemas require JSON content and reject malformed or invalid bodies before proxying; response schema violations return a controlled `502`. Schema management uses:

```text
POST   /api/v1/apis/<api_id>/schemas
GET    /api/v1/apis/<api_id>/schemas
GET    /api/v1/apis/<api_id>/schemas/<schema_id>
PUT    /api/v1/apis/<api_id>/schemas/<schema_id>
DELETE /api/v1/apis/<api_id>/schemas/<schema_id>
```

Version analytics are available at `GET /api/v1/apis/<api_id>/analytics/versions`. The `jsonschema` dependency is declared in `requirements.txt`; install dependencies before using schema validation.

## API-Key Security

API keys are generated with cryptographically secure randomness and only their one-way hashes are stored. The plaintext key is returned once at creation or rotation and is never returned by listing, detail, analytics, audit, or Socket.IO responses.

```text
POST /api/v1/api-keys
GET  /api/v1/api-keys
GET  /api/v1/api-keys/<api_key_id>
POST /api/v1/api-keys/<api_key_id>/rotate
POST /api/v1/api-keys/<api_key_id>/revoke
POST /api/v1/api-keys/<api_key_id>/suspend
POST /api/v1/api-keys/<api_key_id>/unsuspend
DELETE /api/v1/api-keys/<api_key_id>
```

Keys evaluate expiration, suspension, revocation, IP rules, and exact origin rules synchronously. IP deny rules take precedence over allow rules; forwarding headers are trusted only when the direct peer is in `TRUSTED_PROXY_CIDRS`. Security events are persisted in PostgreSQL and sent transiently as `gateway:security` without credentials.

```text
POST   /api/v1/api-keys/<api_key_id>/ip-rules
GET    /api/v1/api-keys/<api_key_id>/ip-rules
PUT    /api/v1/api-keys/<api_key_id>/ip-rules/<rule_id>
DELETE /api/v1/api-keys/<api_key_id>/ip-rules/<rule_id>
POST   /api/v1/api-keys/<api_key_id>/origins
GET    /api/v1/api-keys/<api_key_id>/origins
DELETE /api/v1/api-keys/<api_key_id>/origins/<origin_id>
GET    /api/v1/security/events
GET    /api/v1/api-keys/<api_key_id>/security-events
GET    /api/v1/api-keys/<api_key_id>/security-summary
```

## License

## Health Endpoints

```text
GET /health
GET /health/live
GET /health/ready
```

`/health/live` checks only process liveness. `/health/ready` checks PostgreSQL connectivity, while `/health` reports the summarized service and database state without exposing connection details.

## Observability and Analytics

GateForge emits structured JSON logs to stdout by default. Request lifecycle events are correlated with a validated `X-GateForge-Request-ID`; sensitive headers and query parameters are redacted. Traffic and security audit records remain separate PostgreSQL-backed sources for analytics, and no application log lines are stored as database records.

```text
GET /api/v1/analytics/overview
GET /api/v1/apis/<api_id>/analytics/overview
GET /api/v1/apis/<api_id>/analytics/routes
GET /api/v1/api-keys/<api_key_id>/analytics
GET /api/v1/security/analytics
GET /api/v1/security/events
```

Analytics supports UTC `from` and `to` filters and enforces `ANALYTICS_MAX_DAYS`. Metrics are aggregated from PostgreSQL traffic and security audit tables, including latency, status, upstream, API-key, route, version, and security measurements. Authorized Socket.IO monitoring clients continue to receive transient traffic, error, security, health, and circuit events; PostgreSQL remains authoritative.

GateForge is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
