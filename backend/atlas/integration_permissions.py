"""Exact-scope, expiring permissions for connector reads and scheduled polling."""
import hashlib
import json
from datetime import datetime, timezone

from .models import ConnectorSource, ToolGrant, now


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def scope_fingerprint(source: ConnectorSource) -> str:
    payload = {"id": source.id, "origin": source.origin, "recipe": source.recipe}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def grant_valid(grant: ToolGrant | None, source: ConnectorSource | None = None, stamp: datetime | None = None) -> bool:
    stamp = utc(stamp or now())
    if grant is None or not grant.allowed or grant.expires_at is not None and utc(grant.expires_at) <= stamp:
        return False
    if source is None:
        return True
    return bool(source.enabled and grant.scope_fingerprint == scope_fingerprint(source) and
                grant.expires_at is not None)


def schedule_valid(source: ConnectorSource, stamp: datetime | None = None) -> bool:
    stamp = utc(stamp or now())
    return bool(source.enabled and source.schedule_minutes is not None and
                source.schedule_fingerprint == scope_fingerprint(source) and
                source.schedule_expires_at is not None and utc(source.schedule_expires_at) > stamp)


def revoke_connector(source: ConnectorSource, grant: ToolGrant | None) -> None:
    source.schedule_minutes = None
    source.next_run_at = None
    source.schedule_fingerprint = None
    source.schedule_expires_at = None
    source.last_run_status = "REVOKED"
    source.session_ciphertext = None
    source.session_fingerprint = None
    source.session_generation = None
    source.session_expires_at = None
    source.session_kind = None
    if grant is not None:
        grant.allowed = False
        grant.scope_fingerprint = None
        grant.expires_at = None
