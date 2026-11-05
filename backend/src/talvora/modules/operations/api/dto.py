"""Data Transfer Objects for Operations Endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class OperationResponse(BaseModel):
    """Operation tracking status response."""

    operation_id: UUID = Field(description="Unique operation UUID")
    tenant_id: UUID = Field(description="Owning organization tenant ID")
    operation_type: str = Field(description="Type of background workload")
    status: str = Field(description="Current lifecycle status")
    resource_id: UUID | None = Field(default=None, description="Bound resource entity ID")
    retry_count: int = Field(description="Number of attempts performed")
    max_retries: int = Field(description="Maximum allowed retries")
    created_at: datetime = Field(description="Admission timestamp")
    updated_at: datetime = Field(description="Last status update timestamp")
    result: dict[str, Any] | None = Field(default=None, description="Output payload upon success")
    error_detail: dict[str, Any] | None = Field(default=None, description="Safe error information")
