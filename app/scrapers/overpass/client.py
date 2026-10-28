from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import requests

from app.config import Settings
from app.logging_config import get_logger

RETRYABLE_STATUS = {429, 502, 503, 504}
_SLOTS_NOW_RE = re.compile(r"(\d+)\s+slots?\s+available\s+now", re.IGNORECASE)
_SLOT_IN_RE = re.compile(r"in\s+(-?\d+)\s+seconds", re.IGNORECASE)


class OverpassError(RuntimeError):
    """Base class for Overpass failures."""


class OverpassQueryError(OverpassError):
    """The query itself is wrong (HTTP 400 or other non-retryable error)."""


class OverpassUnavailableError(OverpassError):
    """The endpoint kept failing after all retries."""


@dataclass
class OverpassResponse:
    payload: dict
    http_status: int
    attempts: int
    elapsed_s: float
    raw_bytes: bytes
    url: str
    remarks: list[str] = field(default_factory=list)


@dataclass
class ClientStats:
    requests: int = 0
    retries: int = 0
    status_checks: int = 0
    waited_s: float = 0.0
    failures_by_reason: dict[str, int] = field(default_factory=dict)

    def failure(self, reason: str) -> None:
        self.failures_by_reason[reason] = self.failures_by_reason.get(reason, 0) + 1


def parse_status_wait(text: str) -> float:
    """Seconds to wait before a slot is free, from /api/status output."""
    if _SLOTS_NOW_RE.search(text or ""):
        return 0.0
    waits = [max(0, int(v)) for v in _SLOT_IN_RE.findall(text or "")]
    return float(min(waits)) if waits else 0.0


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Retry-After may be delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def runtime_error_remark(payload: dict) -> str | None:
    remark = payload.get("remark") if isinstance(payload, dict) else None
    if remark and "error" in remark.lower():
        return remark
    return None


class OverpassClient:
    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        sleep=time.sleep,
        clock=time.monotonic,
        run_id: str = "-",
    ):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None
        self.stats = ClientStats()
        self.log = get_logger("overpass", run_id=run_id)

    # ------------------------------------------------------------------
    def _wait(self, seconds: float, why: str) -> None:
        if seconds <= 0:
            return
        seconds = min(seconds, self.settings.backoff_max_s)
        self.log.info(f"waiting {seconds:.1f}s ({why})")
        self.stats.waited_s += seconds
        self._sleep(seconds)

    def _respect_min_interval(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._clock() - self._last_request_at
        remaining = self.settings.min_request_interval_s - elapsed
        if remaining > 0:
            self._wait(remaining, "min request interval")

    def _wait_for_slot(self) -> None:
        if not self.settings.check_status_endpoint:
            return
        try:
            self.stats.status_checks += 1
            resp = self.session.get(self.settings.overpass_status_url, timeout=30)
            if resp.status_code == 200:
                self._wait(parse_status_wait(resp.text), "no free slot on /api/status")
        except requests.RequestException as exc:  # best effort only
            self.log.warning(f"status check failed, continuing: {exc}")

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return retry_after
        return min(self.settings.backoff_base_s * (2 ** (attempt - 1)), self.settings.backoff_max_s)

    # ------------------------------------------------------------------
    def query(self, ql: str, label: str = "") -> OverpassResponse:
        max_attempts = self.settings.max_retries + 1
        last_reason = "unknown"
        started = self._clock()

        for attempt in range(1, max_attempts + 1):
            self._respect_min_interval()
            self._wait_for_slot()
            self.stats.requests += 1
            self._last_request_at = self._clock()
            retry_after: float | None = None

            try:
                resp = self.session.post(
                    self.settings.overpass_url,
                    data={"data": ql},
                    timeout=self.settings.request_timeout_s,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_reason = f"network:{type(exc).__name__}"
            else:
                status = resp.status_code
                if status == 200:
                    try:
                        payload = json.loads(resp.content)
                    except ValueError:
                        last_reason = "invalid_json"
                    else:
                        remark = runtime_error_remark(payload)
                        if remark is None:
                            remarks = [payload["remark"]] if payload.get("remark") else []
                            self.log.info(
                                f"{label} ok: {len(payload.get('elements', []))} elements, "
                                f"attempt {attempt}, {len(resp.content)} bytes"
                            )
                            return OverpassResponse(
                                payload=payload,
                                http_status=status,
                                attempts=attempt,
                                elapsed_s=self._clock() - started,
                                raw_bytes=resp.content,
                                url=self.settings.overpass_url,
                                remarks=remarks,
                            )
                        last_reason = "runtime_error_in_response"
                        self.log.warning(f"{label} Overpass runtime error: {remark[:200]}")
                elif status in RETRYABLE_STATUS or status >= 500:
                    last_reason = f"http_{status}"
                    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
                else:
                    self.stats.failure(f"http_{status}")
                    raise OverpassQueryError(
                        f"{label} HTTP {status} (not retried): {resp.text[:300]!r}"
                    )

            self.stats.failure(last_reason)
            if attempt < max_attempts:
                self.stats.retries += 1
                delay = self._backoff(attempt, retry_after)
                self.log.warning(f"{label} attempt {attempt}/{max_attempts} failed ({last_reason})")
                self._wait(delay, f"backoff after {last_reason}")

        raise OverpassUnavailableError(f"{label} failed after {max_attempts} attempts: {last_reason}")
