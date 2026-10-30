"""Bounded live storage checks and an account-wide SQL operation budget.

Health200 only checks configuration. This gate additionally checks durable
write/read/idempotence with one synthetic record. It never changes deployments,
credentials, plans, permissions, or official evaluation state.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class PreflightBlocked(RuntimeError):
    """No submission or route switch may follow this exception."""

    def __init__(self, report):
        self.report = report
        super().__init__(f"Storage preflight blocked: {report['failure']}")


def sql_budget(client, *, account_id, token, now=None):
    """Query UTC-day account totals, never stored user content or credentials."""
    if not re.fullmatch(r"[0-9a-f]{32}", account_id):
        raise ValueError("Invalid account ID")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    now = now.astimezone(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    query = (
        '{viewer{accounts(filter:{accountTag:"' + account_id + '"}){'
        'durableObjectsPeriodicGroups(limit:1,filter:{datetime_geq:"'
        + start.isoformat()
        + '",datetime_lt:"'
        + now.isoformat()
        + '"}){sum{rowsRead rowsWritten}}}}}'
    )
    response = client.post(
        "https://api.cloudflare.com/client/v4/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": query},
        follow_redirects=False,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("errors"):
        raise ValueError("Cloudflare metrics unavailable")
    accounts = (result.get("data") or {}).get("viewer", {}).get("accounts")
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise ValueError("Cloudflare metrics account missing")
    rows = accounts[0].get("durableObjectsPeriodicGroups")
    # No rows does NOT establish zero usage: metrics may be missing or lagged.
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("Cloudflare metrics missing")
    totals = rows[0].get("sum") or {}
    for key in ("rowsRead", "rowsWritten"):
        if type(totals.get(key)) is not int or totals[key] < 0:
            raise ValueError("Invalid Cloudflare metrics")
    return {
        "at": now.isoformat(),
        "utc_day": start.date().isoformat(),
        "rows_read": totals["rowsRead"],
        "rows_written": totals["rowsWritten"],
        "source": "Cloudflare GraphQL metrics; may lag; not a billing quota counter",
    }


def budget_gate(metrics, *, now=None, write_ceiling=80_000, read_ceiling=4_000_000):
    """Conservative local policy, not a claim about the account's paid/free plan."""
    now = now or datetime.now(UTC)
    try:
        at = datetime.fromisoformat(metrics["at"])
        age = (now - at).total_seconds()
        if not 0 <= age <= 120 or metrics["utc_day"] != now.astimezone(UTC).date().isoformat():
            raise ValueError("Stale metrics")
        for key in ("rows_written", "rows_read"):
            if type(metrics[key]) is not int or metrics[key] < 0:
                raise ValueError("Invalid metrics")
        if metrics["rows_written"] >= write_ceiling or metrics["rows_read"] >= read_ceiling:
            raise ValueError("Conservative SQL operation ceiling reached")
    except (KeyError, TypeError, ValueError) as error:
        raise PreflightBlocked(
            {
                "status": "blocked",
                "failure": "sql_budget_unavailable_or_exhausted",
                "metrics": metrics,
                "policy": {"write_ceiling": write_ceiling, "read_ceiling": read_ceiling},
            }
        ) from error
    return {
        "status": "passed",
        "metrics": metrics,
        "policy": {"write_ceiling": write_ceiling, "read_ceiling": read_ceiling},
    }


def account_budget(client, *, cloudflare_dir, account_id):
    """Use the existing Wrangler login in memory; never write or print its token."""
    try:
        result = subprocess.run(
            ["node", "node_modules/wrangler/bin/wrangler.js", "auth", "token", "--json"],
            cwd=cloudflare_dir,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PreflightBlocked(
            {"status": "blocked", "failure": "metrics_auth_unavailable"}
        ) from error
    if result.returncode:
        raise PreflightBlocked({"status": "blocked", "failure": "metrics_auth_unavailable"})
    try:
        token = json.loads(result.stdout[result.stdout.index("{") :])["token"]
        metrics = sql_budget(client, account_id=account_id, token=token)
    except (ValueError, KeyError, TypeError, httpx.HTTPError) as error:
        raise PreflightBlocked({"status": "blocked", "failure": "metrics_unavailable"}) from error
    return budget_gate(metrics)


def check_storage(
    client,
    *,
    base_url,
    key,
    version,
    commit,
    sleeper=time.sleep,
    rounds=2,
    max_attempts=3,
    request_budget=30,
):
    """One synthetic row, repeated exact-ID writes and source/tenant checks.

    Transient HTTP/transport failures have a small bounded retry budget. Every
    retry of Add is byte-identical, including request_id. Raw bodies, auth values,
    error strings and returned memories are deliberately excluded from reports.
    """
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError("Explicit HTTPS origin required")
    if not 1 <= rounds <= 3 or not 1 <= max_attempts <= 3 or not 1 <= request_budget <= 60:
        raise ValueError("Unbounded preflight")
    base_url = base_url.rstrip("/")
    scope = "synthetic-storage-gate-" + secrets.token_hex(12)
    message = "Synthetic storage gate violetquartz 🌸."
    report = {
        "at": datetime.now(UTC).isoformat(),
        "status": "running",
        "base_url": base_url,
        "expected_version": version,
        "expected_commit": commit,
        "synthetic_scope": scope,
        "events": [],
        "rounds_completed": 0,
        "model_calls": 0,
        "official_evaluations": 0,
        "synthetic_records": 1,
    }

    def block(reason):
        report.update(status="blocked", failure=reason)
        raise PreflightBlocked(report)

    def require(condition, reason):
        if not condition:
            block(reason)

    def call(route, body=None, *, authenticated=True):
        for attempt in range(max_attempts):
            if len(report["events"]) >= request_budget:
                block("request_budget_exhausted")
            event = {"path": route, "attempt": attempt + 1}
            report["events"].append(event)
            started = time.monotonic()
            try:
                response = client.request(
                    "GET" if body is None else "POST",
                    base_url + route,
                    headers={"Authorization": "Bearer " + key} if authenticated else {},
                    json=body,
                    follow_redirects=False,
                )
                event["status"] = response.status_code
                ray = response.headers.get("cf-ray", "")
                if re.fullmatch(r"[0-9a-f]+-[A-Z]{3}", ray):
                    event["cf_ray"] = ray
            except httpx.TransportError:
                event["error"] = "transport_error"
                if attempt + 1 == max_attempts:
                    block("transport_unavailable")
                sleeper(attempt + 1)
                continue
            finally:
                event["ms"] = round((time.monotonic() - started) * 1000, 2)
            if response.status_code in (429, 502, 503, 504):
                if attempt + 1 == max_attempts:
                    block(
                        "storage_unavailable"
                        if response.status_code == 503
                        else "transient_http_exhausted"
                    )
                sleeper(attempt + 1)
                continue
            return response
        block("retry_budget_exhausted")

    def data(response):
        require(response.status_code == 200, "unexpected_http_status")
        try:
            return response.json()
        except ValueError:
            block("invalid_json")

    payload = {
        "user_id": scope,
        "request_id": scope + "-write",
        "session_id": scope + "-session",
        "messages": [{"role": "user", "content": message, "timestamp": 1704067200000}],
    }
    query = {"user_id": scope, "query": "violetquartz", "top_k": 100}
    acknowledgement = {
        "success": True,
        **{k: payload[k] for k in ("user_id", "request_id", "session_id")},
    }
    for index in range(rounds):
        h = data(call("/health", authenticated=False))
        require(isinstance(h, dict), "invalid_health")
        require(
            h.get("version") == version and h.get("source_commit") == commit, "unexpected_backend"
        )
        require(
            h.get("mode") == "lexical"
            and h.get("facts") is False
            and h.get("authenticated_endpoints_configured") is True,
            "invalid_configuration",
        )
        # Fail before any write when the storage read path is unavailable.
        rows = data(call("/v1/memories/search", query))
        require(isinstance(rows, dict), "invalid_search_response")
        if index == 0:
            require(rows == {"data": []}, "synthetic_scope_not_empty")
        bad = call("/v1/memories/search", query, authenticated=False)
        require(bad.status_code == 401, "authentication_not_enforced")
        for _ in range(2):
            require(
                data(call("/v1/memories/add", payload)) == acknowledgement, "write_not_acknowledged"
            )
        found = data(call("/v1/memories/search", query))
        memories = found.get("data") if isinstance(found, dict) else None
        require(
            isinstance(memories, list) and len(memories) == 1, "idempotence_or_readback_failure"
        )
        row = memories[0]
        require(isinstance(row, dict) and isinstance(row.get("content"), str), "invalid_evidence")
        require(row["content"].split("\n", 1)[-1] == message, "source_readback_mismatch")
        require(
            data(call("/v1/memories/search", {**query, "user_id": scope + "-other"}))
            == {"data": []},
            "tenant_isolation_failure",
        )
        report["rounds_completed"] += 1
        if index + 1 < rounds:
            sleeper(2)
    report["status"] = "passed"
    return report


def save_report(path, report):
    """Atomic diagnostics that never serialize arbitrary server payloads."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
