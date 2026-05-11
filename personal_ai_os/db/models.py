from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Plan(str, Enum):
    free = "free"
    personal = "personal"
    pro = "pro"
    business = "business"


class UserRow(BaseModel):
    id: UUID
    telegram_id: int
    username: str | None = None
    full_name: str | None = None
    plan: Plan = Plan.free
    daily_token_limit: int = 50_000
    daily_tokens_used: int = 0
    token_balance: int = 0
    timezone: str = "UTC"
    reminder_hour: int = 9
    meeting_reminder_minutes: int = 15
    language: str = "ru"
    pending_plan: Plan | None = None
    plan_expires_at: datetime | None = None
    referral_rewarded_at: datetime | None = None
    onboarding_complete: bool = False
    referral_code: str | None = None
    referred_by: UUID | None = None
    paddle_customer_id: str | None = None

    model_config = {"frozen": False}


class AgentRow(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    agent_type: str
    system_prompt: str
    tools: list[Any] = Field(default_factory=list)
    is_active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryEntryRow(BaseModel):
    id: UUID
    user_id: UUID
    entry_type: str
    name: str | None = None
    birthday: date | None = None
    relation: str | None = None
    tg_username: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    content: str | None = None


class OAuthTokenRow(BaseModel):
    user_id: UUID
    provider: str
    access_token_enc: str
    refresh_token_enc: str
    expires_at: datetime | None
    scope: str | None = None
    jira_cloud_id: str | None = None
    jira_base_url: str | None = None


class BillingEventCreate(BaseModel):
    user_id: UUID
    event_type: str
    plan: str | None = None
    amount_usd: Decimal | None = None
    paddle_tx_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
