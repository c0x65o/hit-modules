"""Ping-pong test service for Hit platform.

A simple counter service to validate the full pipeline:
- Local development (hit run)
- K8s deployment
- SDK integration

Uses in-memory storage for counters (no database required).
Each pod instance maintains its own counter state, useful for testing scaling.
"""

from fastapi import FastAPI
from threading import Lock

from app.schemas import CounterResponse

# In-memory counter storage (per pod instance)
# Each pod will have its own counter state when scaled
_counters: dict[str, int] = {}
_counter_lock = Lock()

app = FastAPI(
    title="Hit Ping-Pong Service",
    description="Test service with in-memory counter (no database)",
    version="1.0.0",
)


@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "service": "hit-ping-pong",
        "version": "1.0.0",
        "status": "ok",
        "storage": "in-memory",
    }


@app.get("/counter/{counter_id}", response_model=CounterResponse)
def get_counter(counter_id: str):
    """Get current counter value.
    
    Args:
        counter_id: Counter identifier
    
    Returns:
        Counter value (initialized to 0 if doesn't exist)
    """
    with _counter_lock:
        value = _counters.get(counter_id, 0)
        return CounterResponse(id=counter_id, value=value)


@app.post("/counter/{counter_id}/increment", response_model=CounterResponse)
def increment_counter(counter_id: str):
    """Increment counter and return new value.
    
    Args:
        counter_id: Counter identifier
    
    Returns:
        Updated counter value
    """
    with _counter_lock:
        current_value = _counters.get(counter_id, 0)
        new_value = current_value + 1
        _counters[counter_id] = new_value
        return CounterResponse(id=counter_id, value=new_value)


@app.post("/counter/{counter_id}/reset", response_model=CounterResponse)
def reset_counter(counter_id: str):
    """Reset counter to 0.
    
    Args:
        counter_id: Counter identifier
    
    Returns:
        Reset counter value
    """
    with _counter_lock:
        _counters[counter_id] = 0
        return CounterResponse(id=counter_id, value=0)

