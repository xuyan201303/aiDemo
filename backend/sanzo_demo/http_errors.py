"""Allowlisted, bounded diagnostics only. Never retain message/body/request headers."""
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import json
import math
import re
import httpx

_CODES = frozenset({"rate_limit_exceeded", "insufficient_quota", "billing_hard_limit_reached",
    "billing_not_active", "usage_limit_reached", "invalid_api_key", "model_not_found",
    "context_length_exceeded", "invalid_request_error"})
_TYPES = frozenset({"rate_limit_error", "insufficient_quota", "invalid_request_error",
    "server_error", "authentication_error", "permission_error", "tokens", "requests"})
_QUOTA = frozenset({"insufficient_quota", "billing_hard_limit_reached", "billing_not_active", "usage_limit_reached"})
_MAX_WAIT = 2147483647

def _pairs(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError("duplicate")
        result[k] = v
    return result

def _duration(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    if not re.fullmatch(r"(?:[0-9]+(?:\.[0-9]+)?(?:ms|s|m|h|d))+", value):
        return None
    units = {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}
    seconds = sum(float(n) * units[u] for n, u in re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h|d)", value))
    return math.ceil(seconds) if math.isfinite(seconds) and 0 <= seconds <= _MAX_WAIT else None

def _retry_after(value, now):
    if not isinstance(value, str) or len(value) > 128:
        return None
    if re.fullmatch(r"[0-9]{1,10}", value):
        seconds = int(value)
        return seconds if seconds <= _MAX_WAIT else None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        seconds = (date - now).total_seconds()
        return math.ceil(seconds) if 0 <= seconds <= _MAX_WAIT else None
    except (ValueError, TypeError, OverflowError):
        return None

def read_http_error(response, limit, *, now=None):
    now = now or datetime.now(timezone.utc)
    result = {"failure_category": "UNKNOWN_429" if response.status_code == 429 else "HTTP_ERROR",
              "provider_error_code": None, "provider_error_type": None,
              "error_body_status": "INVALID_JSON"}
    chunks, size = [], 0
    try:
        for chunk in response.iter_bytes(chunk_size=min(65536, limit + 1)):
            size += len(chunk)
            if size > limit:
                result["error_body_status"] = "TOO_LARGE"
                break
            chunks.append(chunk)
        else:
            try:
                body = json.loads(b"".join(chunks), object_pairs_hook=_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
                error = body.get("error") if isinstance(body, dict) else None
                if isinstance(error, dict):
                    code, kind = error.get("code"), error.get("type")
                    result["provider_error_code"] = code if isinstance(code, str) and code in _CODES else None
                    result["provider_error_type"] = kind if isinstance(kind, str) and kind in _TYPES else None
                    result["error_body_status"] = "PARSED_ALLOWLIST_ONLY"
            except (ValueError, TypeError, RecursionError, UnicodeError):
                pass
    except httpx.HTTPError:
        result["error_body_status"] = "READ_FAILED"
    code, kind = result["provider_error_code"], result["provider_error_type"]
    if response.status_code == 429:
        quota = code in _QUOTA or kind == "insufficient_quota"
        temporary = code == "rate_limit_exceeded"
        if quota and not temporary:
            result["failure_category"] = "ACCOUNT_QUOTA"
        elif temporary and not quota:
            result["failure_category"] = "TEMPORARY_RATE_LIMIT"
    limits = {}
    for dimension in ("requests", "tokens"):
        for key in ("limit", "remaining"):
            value = response.headers.get(f"x-ratelimit-{key}-{dimension}")
            if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
                limits[f"{key}_{dimension}"] = int(value)
        seconds = _duration(response.headers.get(f"x-ratelimit-reset-{dimension}"))
        if seconds is not None:
            limits[f"reset_{dimension}_seconds"] = seconds
    if limits:
        result["rate_limits"] = limits
    wait = _retry_after(response.headers.get("retry-after"), now)
    if wait is None and result["failure_category"] == "TEMPORARY_RATE_LIMIT":
        waits = [limits[f"reset_{d}_seconds"] for d in ("requests", "tokens")
                 if limits.get(f"remaining_{d}") == 0 and f"reset_{d}_seconds" in limits]
        if waits:
            wait = max(waits)
    if wait is not None:
        result.update(retry_after_seconds=wait,
                      retry_not_before=(now + timedelta(seconds=wait)).isoformat())
    return result
