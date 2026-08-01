"""Pydantic request body models - single source of truth for API body schemas.

FastAPI auto-generates OpenAPI request body schemas from these models,
so handlers receive validated instances instead of raw dicts.
"""
from pydantic import BaseModel, Field


class PushKeys(BaseModel):
    p256dh: str = Field(description="P-256 public key (base64url, 65 bytes)")
    auth: str = Field(description="Auth secret (base64url, 16 bytes)")


class PushSubscribeBody(BaseModel):
    endpoint: str = Field(description="Push endpoint URL from PushSubscription")
    keys: PushKeys = Field(description="Subscription encryption keys")
    client_id: str = Field(description="Client identifier (from localStorage)")
    prefs: dict | None = Field(default=None, description="Notification preferences dict (validated server-side via sanitize_prefs)")


class PushUnsubscribeBody(BaseModel):
    endpoint: str | None = Field(default=None, description="Push endpoint URL to remove")
    client_id: str | None = Field(default=None, description="Client identifier - if present, removes ALL subscriptions for this client")


class PushUpdatePrefsBody(BaseModel):
    prefs: dict = Field(description="Notification preferences (validated server-side via sanitize_prefs)")
    client_id: str | None = Field(default=None, description="Client identifier - if present, updates ALL subscriptions for this client")
    endpoint: str | None = Field(default=None, description="Push endpoint URL (used if client_id is absent)")


class PushTestBody(BaseModel):
    endpoint: str = Field(description="Push endpoint URL to send the test to")


class ClientErrorBody(BaseModel):
    message: str = Field(description="Error message")
    source: str = Field(default="", description="Source script URL")
    line: int | None = Field(default=None, description="Line number")
    col: int | None = Field(default=None, description="Column number")
    stack: str = Field(default="", description="Stack trace")
    type: str = Field(default="", description="Error type: 'error' or 'rejection'")
    url: str = Field(default="", description="Page URL")
    ua: str = Field(default="", description="User agent string")
