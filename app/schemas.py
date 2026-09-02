from typing import Any

from pydantic import BaseModel, Field


class ChannelCreateRequest(BaseModel):
    channel_base_name: str = Field(min_length=1)
    base_platform: str | None = None
    batch_id: str | None = None


class AppCreateRequest(BaseModel):
    business_object: str = Field(min_length=1)
    activity_name: str = ""
    actual_channel_name: str = Field(min_length=1)
    application_type: str = "云盘"
    jump_address: str = ""
    resource_fallback_page: str = ""
    settlement_type: str = "云盘"
    group_name: str = ""
    batch_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class AppUpdateRequest(BaseModel):
    app_name: str = Field(min_length=1)
    fields_to_update: dict[str, Any]
    app_id: str | None = None
    batch_id: str | None = None


class AppLocateRequest(BaseModel):
    channel_name: str = ""
    app_name: str = ""
    batch_id: str | None = None


class EmailParseRequest(BaseModel):
    raw_text: str = Field(min_length=1)


class LedgerRowRequest(BaseModel):
    parsed: dict[str, Any]
    result: dict[str, Any] = Field(default_factory=dict)

class JobCreateResponse(BaseModel):
    job_id: str
    status: str
    message: str
