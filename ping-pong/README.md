# Ping-Pong Service

Simple counter service for testing the Hit platform pipeline.

## Purpose

This service validates the full development and deployment workflow:
- Local development with `hit run`
- K8s deployment with `hit deploy`
- SDK integration from client projects
- Database migrations
- Service discovery

## Endpoints

### GET /counter/{id}
Get current counter value (auto-creates if doesn't exist).

**Response:**
```json
{
  "id": "test",
  "value": 0
}
```

### POST /counter/{id}/increment
Increment counter and return new value.

**Response:**
```json
{
  "id": "test",
  "value": 1
}
```

### POST /counter/{id}/reset
Reset counter to 0.

**Response:**
```json
{
  "id": "test",
  "value": 0
}
```

## Local Development

### Setup

```bash
# Copy environment file
cp .env.example .env

# Install dependencies
uv sync

# Create database
createdb ping_pong

# Run migrations (if any)
uv run alembic upgrade head
```

### Run Service

```bash
# With Hit CLI
hit run ping-pong

# Or manually
uv run uvicorn app.main:app --reload --port 8099
```

### Test

```bash
# Get counter
curl http://localhost:8099/counter/test

# Increment
curl -X POST http://localhost:8099/counter/test/increment

# Reset
curl -X POST http://localhost:8099/counter/test/reset
```

## Using the SDK

### Python

```python
from hit import ping_pong

# Get counter
count = await ping_pong.get_counter("test")
print(f"Count: {count}")

# Increment
new_count = await ping_pong.increment("test")
print(f"New count: {new_count}")

# Reset
await ping_pong.reset("test")
```

### TypeScript

```typescript
import { pingPong } from '@hit/sdk';

// Get counter
const count = await pingPong.getCounter('test');
console.log(`Count: ${count}`);

// Increment
const newCount = await pingPong.increment('test');
console.log(`New count: ${newCount}`);

// Reset
await pingPong.reset('test');
```

## Deployment

```bash
# Deploy to K8s
hit deploy services
```

The Hit CLI will:
1. Build Docker image from Dockerfile
2. Push to container registry
3. Generate K8s manifests
4. Deploy to cluster with service discovery

