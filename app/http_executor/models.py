from __future__ import annotations

from typing import Any, Dict, Optional

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
    contract_version: str
    task_id: str
    operation: str
    environment: str
    idempotency_key: str
    input: Dict[str, Any] = Field(default_factory=dict)


class QueryRequest(StrictModel):
    contract_version: str
    task_id: Optional[str] = None
    operation: Optional[str] = None
    environment: Optional[str] = None
    idempotency_key: Optional[str] = None
    execution_id: Optional[str] = None
