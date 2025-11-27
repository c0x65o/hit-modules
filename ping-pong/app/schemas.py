"""Pydantic schemas for API responses."""

from pydantic import BaseModel


class CounterResponse(BaseModel):
    """Counter response schema."""

    id: str
    value: int

    model_config = {"from_attributes": True}

