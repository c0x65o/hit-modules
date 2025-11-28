"""Ping-pong test service for Hit platform.

A simple counter service to validate the full pipeline:
- Local development (hit run)
- K8s deployment
- SDK integration

Uses filesystem storage for counters (no database required).
"""

from fastapi import FastAPI

from app.db import get_counter, set_counter
from app.schemas import CounterResponse

app = FastAPI(
    title="Hit Ping-Pong Service",
    description="Test service with filesystem-based counter storage",
    version="1.0.0",
)


@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "service": "hit-ping-pong",
        "version": "1.0.0",
        "status": "ok",
        "storage": "filesystem",
    }


@app.get("/counter/{counter_id}", response_model=CounterResponse)
def get_counter_endpoint(counter_id: str):
    """Get current counter value.
    
    Args:
        counter_id: Counter identifier
    
    Returns:
        Counter value (initialized to 0 if doesn't exist)
    """
    value = get_counter(counter_id)
    return CounterResponse(id=counter_id, value=value)


@app.post("/counter/{counter_id}/increment", response_model=CounterResponse)
def increment_counter(counter_id: str):
    """Increment counter and return new value.
    
    Args:
        counter_id: Counter identifier
    
    Returns:
        Updated counter value
    """
    current_value = get_counter(counter_id)
    new_value = current_value + 1
    set_counter(counter_id, new_value)
    return CounterResponse(id=counter_id, value=new_value)


@app.post("/counter/{counter_id}/reset", response_model=CounterResponse)
def reset_counter(counter_id: str):
    """Reset counter to 0.
    
    Args:
        counter_id: Counter identifier
    
    Returns:
        Reset counter value
    """
    set_counter(counter_id, 0)
    return CounterResponse(id=counter_id, value=0)

