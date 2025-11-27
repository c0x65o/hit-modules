"""Ping-pong test service for Hit platform.

A simple counter service to validate the full pipeline:
- Local development (hit run)
- K8s deployment
- SDK integration
"""

import os

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.db import Base, engine, get_db
from app.models import Counter
from app.schemas import CounterResponse

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Hit Ping-Pong Service",
    description="Test service with stateful counter",
    version="1.0.0",
)


@app.get("/")
def root():
    """Health check endpoint."""
    return {
        "service": "hit-ping-pong",
        "version": "1.0.0",
        "status": "ok",
    }


@app.get("/counter/{counter_id}", response_model=CounterResponse)
def get_counter(counter_id: str, db: Session = Depends(get_db)):
    """Get current counter value.
    
    Args:
        counter_id: Counter identifier
        db: Database session
    
    Returns:
        Counter value
    """
    counter = db.query(Counter).filter(Counter.id == counter_id).first()
    if not counter:
        # Auto-create counter if it doesn't exist
        counter = Counter(id=counter_id, value=0)
        db.add(counter)
        db.commit()
        db.refresh(counter)
    
    return CounterResponse(id=counter.id, value=counter.value)


@app.post("/counter/{counter_id}/increment", response_model=CounterResponse)
def increment_counter(counter_id: str, db: Session = Depends(get_db)):
    """Increment counter and return new value.
    
    Args:
        counter_id: Counter identifier
        db: Database session
    
    Returns:
        Updated counter value
    """
    counter = db.query(Counter).filter(Counter.id == counter_id).first()
    if not counter:
        counter = Counter(id=counter_id, value=1)
        db.add(counter)
    else:
        counter.value += 1
    
    db.commit()
    db.refresh(counter)
    return CounterResponse(id=counter.id, value=counter.value)


@app.post("/counter/{counter_id}/reset", response_model=CounterResponse)
def reset_counter(counter_id: str, db: Session = Depends(get_db)):
    """Reset counter to 0.
    
    Args:
        counter_id: Counter identifier
        db: Database session
    
    Returns:
        Reset counter value
    """
    counter = db.query(Counter).filter(Counter.id == counter_id).first()
    if not counter:
        counter = Counter(id=counter_id, value=0)
        db.add(counter)
    else:
        counter.value = 0
    
    db.commit()
    db.refresh(counter)
    return CounterResponse(id=counter.id, value=counter.value)

