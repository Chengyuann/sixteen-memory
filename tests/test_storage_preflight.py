import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

_spec = importlib.util.spec_from_file_location(
    "storage_preflight", Path(__file__).resolve().parents[1] / "scripts/storage_preflight.py"
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
PreflightBlocked = _module.PreflightBlocked
budget_gate = _module.budget_gate
check_storage = _module.check_storage
sql_budget = _module.sql_budget

KEY = "synthetic-secret-do-not-include-in-reports"
COMMIT = "a" * 40
BASE = "https://synthetic.invalid"


def healthy_handler(request, store):
    if request.url.path == "/health":
        return httpx.Response(
            200,
            json={
                "version": "0.2.5",
                "source_commit": COMMIT,
                "mode": "lexical",
                "facts": False,
                "authenticated_endpoints_configured": True,
            },
        )
    if request.headers.get("Authorization") != "Bearer " + KEY:
        return httpx.Response(401, json={"detail": "Invalid credentials"})
    body = json.loads(request.content)
    if request.url.path.endswith("/add"):
        store.setdefault(body["user_id"], {})[body["request_id"]] = body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "success": True,
                **{k: body[k] for k in ("user_id", "request_id", "session_id")},
            },
        )
    return httpx.Response(
        200,
        json={
            "data": [
                {"content": "{}\n" + content} for content in store.get(body["user_id"], {}).values()
            ],
        },
    )


def run(handler, **kwargs):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return check_storage(
            client,
            base_url=BASE,
            key=KEY,
            version="0.2.5",
            commit=COMMIT,
            sleeper=lambda _: None,
            **kwargs,
        )


def test_real_read_write_gate_uses_one_record_across_two_rounds():
    store, calls = {}, []

    def handler(request):
        calls.append(request)
        return healthy_handler(request, store)

    report = run(handler)
    assert report["status"] == "passed"
    assert report["rounds_completed"] == 2
    assert len(calls) == 14
    assert sum(len(x) for x in store.values()) == 1
    assert KEY not in json.dumps(report)
    assert "violetquartz" not in json.dumps(report)


def test_health200_cannot_hide_storage503_and_stops_before_add():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/health":
            return healthy_handler(request, {})
        return httpx.Response(503, json={"detail": "Memory storage unavailable " + KEY})

    with pytest.raises(PreflightBlocked) as failure:
        run(handler)
    assert failure.value.report["failure"] == "storage_unavailable"
    assert len(calls) == 4
    assert all(not r.url.path.endswith("/add") for r in calls)
    assert KEY not in str(failure.value)
    assert KEY not in json.dumps(failure.value.report)


def test_add503_does_not_count_as_ready():
    def handler(request):
        if request.url.path.endswith("/add"):
            return httpx.Response(503, json={"detail": "unavailable"})
        return healthy_handler(request, {})

    with pytest.raises(PreflightBlocked) as failure:
        run(handler)
    assert failure.value.report["rounds_completed"] == 0
    assert failure.value.report["failure"] == "storage_unavailable"


def test_transient_add_retry_preserves_exact_request_bytes():
    store, adds = {}, []

    def handler(request):
        if request.url.path.endswith("/add"):
            adds.append(request.content)
            response = healthy_handler(request, store)  # Commit before transport drops.
            if len(adds) == 1:
                raise httpx.ReadTimeout(KEY, request=request)
            return response
        return healthy_handler(request, store)

    report = run(handler, rounds=1)
    assert report["status"] == "passed"
    assert len(adds) == 3 and adds[0] == adds[1] == adds[2]
    assert sum(len(x) for x in store.values()) == 1
    assert KEY not in json.dumps(report)


@pytest.mark.parametrize("changed", [{"version": "0.2.6"}, {"source_commit": "b" * 40}])
def test_wrong_backend_blocks_before_any_write(changed):
    calls = []

    def handler(request):
        calls.append(request)
        body = healthy_handler(request, {}).json() | changed
        return httpx.Response(200, json=body)

    with pytest.raises(PreflightBlocked) as failure:
        run(handler)
    assert failure.value.report["failure"] == "unexpected_backend"
    assert len(calls) == 1


def test_storage_http_redirect_never_forwards_authorization():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/health":
            return healthy_handler(request, {})
        return httpx.Response(307, headers={"Location": "https://unrelated.invalid"})

    with pytest.raises(PreflightBlocked):
        run(handler)
    assert len(seen) == 2
    assert all(r.url.host == "synthetic.invalid" for r in seen)


def test_healthy_empty_search_is_not_enough_without_correct_readback():
    store = {}

    def handler(request):
        result = healthy_handler(request, store)
        if request.url.path.endswith("/search") and result.status_code == 200:
            return httpx.Response(200, json={"data": []})
        return result

    with pytest.raises(PreflightBlocked) as failure:
        run(handler)
    assert failure.value.report["failure"] == "idempotence_or_readback_failure"


def test_request_budget_is_hard_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return healthy_handler(request, {})

    with pytest.raises(PreflightBlocked) as failure:
        run(handler, request_budget=2)
    assert failure.value.report["failure"] == "request_budget_exhausted"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "bad_origin",
    [
        "http://synthetic.invalid",
        "https://user:secret@synthetic.invalid",
        "https://synthetic.invalid/path",
        "https://synthetic.invalid?secret=x",
    ],
)
def test_only_explicit_https_origins(bad_origin):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: pytest.fail("Unexpected request"))
        ) as c,
        pytest.raises(ValueError),
    ):
        check_storage(c, base_url=bad_origin, key=KEY, version="0.2.5", commit=COMMIT)


def test_metrics_aggregate_account_wide_in_utc_not_one_candidate():
    now = datetime(2026, 10, 30, 3, 30, tzinfo=UTC)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "viewer": {
                        "accounts": [
                            {
                                "durableObjectsPeriodicGroups": [
                                    {"sum": {"rowsRead": 100, "rowsWritten": 23}}
                                ],
                            }
                        ]
                    }
                },
                "errors": None,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = sql_budget(client, account_id="a" * 32, token=KEY, now=now)
    assert result["rows_written"] == 23
    query = json.loads(calls[0].content)["query"]
    assert "2026-10-30T00:00:00+00:00" in query
    assert "namespaceId" not in query
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"message": KEY}]},
        {"data": None},
        {"data": {"viewer": {"accounts": []}}},
        {"data": {"viewer": {"accounts": [{"durableObjectsPeriodicGroups": []}]}}},
        {
            "data": {
                "viewer": {
                    "accounts": [
                        {
                            "durableObjectsPeriodicGroups": [
                                {
                                    "sum": {"rowsRead": 1, "rowsWritten": -1},
                                }
                            ]
                        }
                    ]
                }
            }
        },
    ],
)
def test_missing_failed_or_invalid_metrics_never_mean_zero_usage(payload):
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as c,
        pytest.raises(ValueError),
    ):
        sql_budget(c, account_id="a" * 32, token=KEY)


@pytest.mark.parametrize(
    "overrides",
    [
        {"rows_written": 80_000},
        {"rows_written": 104_933},
        {"rows_read": 4_000_000},
        {"rows_written": None},
        {"rows_written": True},
        {"utc_day": "2026-10-29"},
        {"at": "invalid"},
    ],
)
def test_budget_fail_closed(overrides):
    now = datetime(2026, 10, 30, 3, tzinfo=UTC)
    metrics = {
        "at": now.isoformat(),
        "utc_day": "2026-10-30",
        "rows_written": 100,
        "rows_read": 200,
    } | overrides
    with pytest.raises(PreflightBlocked):
        budget_gate(metrics, now=now)


def test_budget_cannot_reuse_stale_or_future_receipt():
    now = datetime(2026, 10, 30, 3, tzinfo=UTC)
    for delta in (timedelta(minutes=-3), timedelta(seconds=1)):
        with pytest.raises(PreflightBlocked):
            budget_gate(
                {
                    "at": (now + delta).isoformat(),
                    "utc_day": "2026-10-30",
                    "rows_written": 10,
                    "rows_read": 10,
                },
                now=now,
            )


def test_budget_policy_passed_is_not_claim_of_paid_plan_or_remaining_quota():
    now = datetime(2026, 10, 30, 3, tzinfo=UTC)
    metrics = {
        "at": now.isoformat(),
        "utc_day": "2026-10-30",
        "rows_written": 79_999,
        "rows_read": 3_999_999,
    }
    result = budget_gate(metrics, now=now)
    assert result["status"] == "passed"
    assert "remaining" not in result
