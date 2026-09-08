from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field, VERSION

if VERSION.startswith("1."):
    ConfigDict = None
else:
    from pydantic import ConfigDict


if ConfigDict is None:
    class StrictModel(BaseModel):
        class Config:
            extra = "forbid"
else:
    class StrictModel(BaseModel):
        model_config = ConfigDict(extra="forbid")


class ExecutionRequest(StrictModel):
    task_id: str = Field(min_length=1)
    operation: Literal["create_channel", "create_app"]
    environment: str = "TEST"
    snapshot_version: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    input: Dict[str, Any]


class QueryRequest(StrictModel):
    task_id: Optional[str] = None
    operation: Optional[Literal["create_channel", "create_app"]] = None
    environment: Optional[str] = None
    snapshot_version: Optional[str] = None
    idempotency_key: Optional[str] = None
    execution_correlation_id: Optional[str] = None
