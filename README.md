# Hit Modules

Microservices for the Hit platform. Each module is a standalone service that can be run independently or orchestrated via the Hit CLI.

## Architecture

- **Standalone Services**: Each module is a complete FastAPI/Express application
- **Multi-tenancy**: Services support namespace-based isolation for cross-project sharing
- **SDK Integration**: Consumed via strongly-typed client SDKs (Python/TypeScript)
- **Flexible Deployment**: Run bare metal in dev (`hit run`), K8s pods in production

## Available Modules

### ping-pong (Test Module)
Simple counter service for testing the full pipeline (dev → K8s deployment).

**Endpoints:**
- `GET /counter/{id}` - Get current count
- `POST /counter/{id}/increment` - Increment counter
- `POST /counter/{id}/reset` - Reset to 0

**Status:** ✅ Implemented

### auth (Authentication)
JWT authentication with OAuth provider support (Google, Azure).

**Endpoints:**
- `POST /auth/login` - Email/password login
- `POST /auth/register` - Create account
- `POST /auth/verify` - Validate JWT
- `GET /oauth/{provider}` - Initiate OAuth flow
- `GET /oauth/{provider}/callback` - Complete OAuth flow

**Status:** 🚧 Planned (Phase 3)

### email (Email Service)
Multi-provider email sending with templates.

**Status:** 📋 Planned (Phase 4)

## Development

### Running a Service Locally

```bash
# Navigate to service directory
cd ping-pong

# Install dependencies
uv sync

# Run migrations
uv run alembic upgrade head

# Start service
uv run uvicorn app.main:app --reload --port 8099
```

### Using Hit CLI

```bash
# Start a service
hit run ping-pong

# Start all services from project hit.yaml
hit run

# Deploy to K8s
hit deploy services
```

## Module Structure

Each module follows this structure:

```
module-name/
├── Dockerfile
├── hit-module.yaml   # Service manifest
├── pyproject.toml
├── app/
│   ├── main.py       # FastAPI app
│   ├── models.py     # Database models
│   ├── routes.py     # API routes
│   └── db.py         # Database connection
├── alembic/          # Migrations
├── tests/
└── README.md
```

## Shared Utilities

Common utilities in `shared/`:
- `database.py` - Database connection helpers
- `models.py` - Base models with timestamps

## Contributing

1. Each module is self-contained
2. Follow the standard structure above
3. Include hit-module.yaml with service metadata
4. Provide comprehensive tests
5. Document all endpoints

## License

Proprietary - Hitcents, Inc.

