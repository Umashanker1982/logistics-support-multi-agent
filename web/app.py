"""Web demo: a FastAPI server over the same orchestrator the CLI uses.

    uvicorn web.app:app --reload          (local, http://127.0.0.1:8000)

Live features:
* /api/chat/stream streams every agent step as it happens (server-sent events);
* /api/policy hot-reloads business rules (approval limit, refund window) per visitor;
* /api/benchmark/run runs the golden test suite on demand.

Protection for a public deployment: per-visitor sandboxes, per-visitor and per-IP
rate limits, and a global daily cap on Claude-backed requests.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import sys
import os
import queue
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
import html

from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.llm import LLMClient
from config import ROOT, load_settings
from security.approvals import ApprovalError
from web.sandbox import VISITOR_ID, CircuitBreaker, DailyCap, SandboxManager

SETTINGS = load_settings()
AUTHOR = os.environ.get("DEMO_AUTHOR", "Umashanker")
# Optional link back to a portfolio/landing page listing all projects (e.g. https://umashanker.com).
HOME_URL = os.environ.get("DEMO_HOME_URL", "").strip()
if HOME_URL and not re.match(r"^https://[\w.-]+(/[\w./-]*)?$", HOME_URL):
    HOME_URL = ""
MSGS_PER_WINDOW = int(os.environ.get("DEMO_MSGS_PER_10MIN", "30"))
IP_MSGS_PER_WINDOW = int(os.environ.get("DEMO_IP_MSGS_PER_10MIN", "80"))
WINDOW_S = 600

_claude = LLMClient(dataclasses.replace(SETTINGS, llm_mode="claude"))
CLAUDE_CLIENT = _claude._client if _claude.enabled else None
CLAUDE_CAP = DailyCap(int(os.environ.get("DEMO_DAILY_CLAUDE_REQUESTS", "300")))
CLAUDE_BREAKER = CircuitBreaker(threshold=3, window_s=120, cooldown_s=300)
STREAM_TIMEOUT_S = 90
log = logging.getLogger("web")
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
MANAGER = SandboxManager(SETTINGS)

_ip_hits: dict[str, deque[float]] = defaultdict(deque)
_ip_lock = threading.Lock()
STATIC = Path(__file__).resolve().parent / "static"

MAX_BODY_BYTES = 16 * 1024
MAINTENANCE = os.environ.get("MAINTENANCE_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

# Browser security headers on every response.
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
        "object-src 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Cross-Origin-Opener-Policy": "same-origin",
}

app = FastAPI(title="SwiftParcel multi-agent support", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- error pages
ERROR_COPY = {
    404: ("Page not found", "Wrong address", "This address doesn't lead anywhere on the demo. The link may be mistyped, or the page may have moved.", "Back to the demo"),
    429: ("Slow down a little", "Rate limited", "That was a lot of requests in a short time. Wait a minute and try again.", "Back to the demo"),
    500: ("Something went wrong on our side", "Delivery exception", "The demo hit an unexpected problem. It has been logged, and the site is still running. Please try again.", "Try again"),
    503: ("Back shortly", "Maintenance", "The demo is being updated right now. Please check back in a few minutes.", "Reload"),
}


def error_page(code: int, detail: str | None = None) -> HTMLResponse:
    title, stamp, message, action = ERROR_COPY.get(code, ERROR_COPY[500])
    page = (STATIC / "error.html").read_text(encoding="utf-8")
    for key, value in {"code": str(code), "title": title, "stamp": stamp, "message": detail or message,
                       "status_text": f"{code}: {stamp}", "action": action}.items():
        page = page.replace("{{" + key + "}}", html.escape(value))
    return HTMLResponse(page, status_code=code)


def _wants_html(request: Request) -> bool:
    return not request.url.path.startswith("/api/") and "text/html" in request.headers.get("accept", "")


@app.middleware("http")
async def guard(request: Request, call_next):
    """Maintenance switch, request size limit, and security headers for every response."""
    if MAINTENANCE and request.url.path != "/healthz":
        response = (error_page(503) if _wants_html(request)
                    else JSONResponse({"error": "The demo is being updated. Please try again in a few minutes."}, 503))
    elif int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        response = JSONResponse({"error": "Request too large."}, status_code=413)
    else:
        try:
            response = await call_next(request)
        except Exception:  # last-resort net for errors outside route handlers
            log.exception("unhandled error on %s", request.url.path)
            response = error_page(500) if _wants_html(request) else JSONResponse(
                {"error": "Something went wrong on our side. Please try again."}, status_code=500)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------- helpers
def _sandbox(visitor: str | None):
    if not visitor or not VISITOR_ID.match(visitor):
        raise HTTPException(400, "Missing or invalid visitor id. Reload the page.")
    return MANAGER.get(visitor)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _ip_allowed(ip: str) -> bool:
    now = time.time()
    with _ip_lock:
        hits = _ip_hits[ip]
        while hits and now - hits[0] > WINDOW_S:
            hits.popleft()
        if len(hits) >= IP_MSGS_PER_WINDOW:
            return False
        hits.append(now)
        return True


def _orders_view(box, customer_id: str) -> list[dict[str, Any]]:
    b = box.backend
    out = []
    for oid, o in sorted(b.orders.items()):
        if o["customer_id"] != customer_id:
            continue
        approvals = [a for a in box.approvals.for_order(oid) if a["status"] in ("pending", "approved")]
        out.append({
            "order_id": oid,
            "item": ", ".join(i["name"] for i in o["items"]),
            "total": round(sum(i["price"] for i in o["items"]), 2),
            "status": o["status"],
            "delivered_on": o["delivered_on"],
            "insured": bool(o["insurance"]),
            "returned": oid in b.returns,
            "return_reason": (b.returns.get(oid) or {}).get("reason"),
            "refund": b.refunds.get(oid),
            "claim": b.claims.get(oid),
            "pending_approval": approvals[0]["approval_id"] if approvals else None,
        })
    return out


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "author": AUTHOR,
        "home_url": HOME_URL or None,
        "project": "Capstone 1: Production Multi-Agent System",
        "company": "SwiftParcel Logistics",
        "model": SETTINGS.model,
        "claude_available": CLAUDE_CLIENT is not None,
        "claude_remaining_today": CLAUDE_CAP.remaining if CLAUDE_CLIENT else 0,
        "claude_breaker": CLAUDE_BREAKER.state,
        "default_policy": {"approval_threshold": SETTINGS.refund_approval_threshold,
                           "refund_window_days": SETTINGS.refund_window_days},
        "limits": {"messages_per_10_min": MSGS_PER_WINDOW, "max_chars": SETTINGS.max_input_chars},
    }


# ---------------------------------------------------------------- customers & orders
@app.get("/api/customers")
def customers(x_visitor_id: str | None = Header(default=None)) -> dict[str, Any]:
    box = _sandbox(x_visitor_id)
    return {"customers": [
        {"customer_id": cid, "name": c["name"], "orders": _orders_view(box, cid)}
        for cid, c in box.backend.customers.items()
    ]}


# ---------------------------------------------------------------- chat (streaming)
class ChatIn(BaseModel):
    customer_id: str = Field(max_length=10)
    message: str = Field(max_length=4000)
    mode: str = "offline"


@app.post("/api/chat/stream")
def chat_stream(body: ChatIn, request: Request, x_visitor_id: str | None = Header(default=None)):
    box = _sandbox(x_visitor_id)
    if not box.allow_message(MSGS_PER_WINDOW, WINDOW_S) or not _ip_allowed(_client_ip(request)):
        raise HTTPException(429, f"Demo limit reached: {MSGS_PER_WINDOW} messages per 10 minutes. Try again shortly.")

    notice = None
    use_claude = False
    if body.mode == "claude":
        if CLAUDE_CLIENT is None:
            notice = "Claude isn't configured on this server, so this reply used offline mode."
        elif not CLAUDE_BREAKER.allow():
            notice = "Claude is having trouble right now, so replies use rules-only mode for a few minutes."
        elif not CLAUDE_CAP.take():
            notice = "Today's Claude demo budget is used up, so this reply used offline mode."
        else:
            use_claude = True

    events: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            with box.lock:
                box.use_model(CLAUDE_CLIENT if use_claude else None)
                box.tracer.listener = events.put
                try:
                    resp = box.orchestrator.handle(body.message, customer_id=body.customer_id,
                                                   session_id=f"web-{box.visitor_id}-{body.customer_id}"[:64])
                finally:
                    box.tracer.listener = None
                trace = box.tracer.last_record
            final_notice = notice
            if use_claude:
                calls = (trace or {}).get("llm_calls", [])
                failed = any(not c.get("ok") for c in calls)
                CLAUDE_BREAKER.record(not failed)
                if failed:
                    final_notice = "Claude didn't respond in time, so this reply was finished with rules-only mode."
            events.put({"type": "final", "response": resp.to_dict(), "trace": trace, "notice": final_notice,
                        "mode": "claude" if use_claude else "offline"})
        except Exception:  # self-heal: log it, reset this visitor's sandbox, tell the UI
            log.exception("chat request failed; resetting sandbox %s", box.visitor_id)
            MANAGER.reset(box.visitor_id)
            events.put({"type": "error",
                        "message": "This demo session hit a problem and was reset. Please send your message again."})
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            try:
                item = events.get(timeout=STREAM_TIMEOUT_S)
            except queue.Empty:  # never leave the browser waiting forever
                yield "data: " + json.dumps({"type": "error", "message": "The request took too long. Please try again."}) + "\n\n"
                break
            if item is None:
                break
            yield f"data: {json.dumps(item, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
def reset(x_visitor_id: str | None = Header(default=None)) -> dict[str, str]:
    _sandbox(x_visitor_id)
    MANAGER.reset(x_visitor_id)  # type: ignore[arg-type]
    return {"status": "reset"}


# ---------------------------------------------------------------- human approvals
@app.get("/api/approvals")
def approvals(x_visitor_id: str | None = Header(default=None)) -> dict[str, Any]:
    box = _sandbox(x_visitor_id)
    names = {cid: c["name"] for cid, c in box.backend.customers.items()}
    items = sorted(box.approvals.list(), key=lambda a: a["approval_id"], reverse=True)
    return {"approvals": [{**a, "customer_name": names.get(a["customer_id"], a["customer_id"])} for a in items]}


class DecisionIn(BaseModel):
    approve: bool
    approver: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=200)


@app.post("/api/approvals/{approval_id}")
def decide(approval_id: str, body: DecisionIn, x_visitor_id: str | None = Header(default=None)) -> dict[str, Any]:
    box = _sandbox(x_visitor_id)
    if not re.match(r"^APR-\d{4}$", approval_id):
        raise HTTPException(400, "Unknown approval id.")
    with box.lock:
        box.use_model(None)
        try:
            result = box.orchestrator.approve(approval_id, body.approver.strip(), body.approve, body.note)
        except ApprovalError as exc:
            raise HTTPException(400, str(exc))
    return {"outcome": result.outcome, "actions": result.actions, "trace": box.tracer.last_record}


# ---------------------------------------------------------------- hot-reload policy
class PolicyIn(BaseModel):
    approval_threshold: float = Field(ge=50, le=1000)
    refund_window_days: int = Field(ge=7, le=90)


@app.get("/api/policy")
def get_policy(x_visitor_id: str | None = Header(default=None)) -> dict[str, Any]:
    box = _sandbox(x_visitor_id)
    return {"approval_threshold": box.settings.refund_approval_threshold,
            "refund_window_days": box.settings.refund_window_days}


@app.post("/api/policy")
def set_policy(body: PolicyIn, x_visitor_id: str | None = Header(default=None)) -> dict[str, Any]:
    box = _sandbox(x_visitor_id)
    t0 = time.perf_counter()
    with box.lock:
        box.apply_policy(approval_threshold=body.approval_threshold, refund_window_days=body.refund_window_days)
    return {"approval_threshold": box.settings.refund_approval_threshold,
            "refund_window_days": box.settings.refund_window_days,
            "applied_in_ms": round((time.perf_counter() - t0) * 1000, 2)}


# ---------------------------------------------------------------- evaluation
_last_bench: dict[str, float] = {}


@app.post("/api/benchmark/run")
def run_benchmark(x_visitor_id: str | None = Header(default=None)) -> dict[str, Any]:
    _sandbox(x_visitor_id)
    now = time.time()
    if now - _last_bench.get(x_visitor_id, 0) < 15:  # type: ignore[arg-type]
        raise HTTPException(429, "The test suite just ran. Try again in a few seconds.")
    _last_bench[x_visitor_id] = now  # type: ignore[index]
    from eval.benchmark import run_case, summarize
    from tools.backend import Backend
    offline = dataclasses.replace(SETTINGS, llm_mode="offline")
    base = Backend.from_dir(offline.data_dir)
    cases = json.loads((ROOT / "eval" / "golden_requests.json").read_text(encoding="utf-8"))
    results = [run_case(c, base, offline) for c in cases]
    for case, r in zip(cases, results):
        r["message"] = case["message"] or "(1,200-character message)"
        r["notes"] = case.get("notes", "")
    return {"summary": summarize(results, "offline", offline.model), "results": results}


@app.get("/api/results")
def results() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, path in {"golden_offline": "eval/results/latest_offline.json",
                      "golden_claude": "eval/results/latest_claude.json",
                      "holdout_offline": "eval/results/holdout/latest_offline.json",
                      "holdout_claude": "eval/results/holdout/latest_claude.json"}.items():
        f = ROOT / path
        try:
            if f.exists():
                out[key] = json.loads(f.read_text(encoding="utf-8"))["summary"]
        except Exception:  # a missing or corrupt results file must not break the page
            log.warning("could not read %s", path)
    return out


@app.exception_handler(Exception)
def unexpected_error(request: Request, exc: Exception):
    """Safety net: any unexpected error becomes a friendly reply; the server keeps running."""
    log.exception("unhandled error on %s", request.url.path)
    if _wants_html(request):
        return error_page(500)
    return JSONResponse({"error": "Something went wrong on our side. Please try again."}, status_code=500)


@app.exception_handler(StarletteHTTPException)
def http_error(request: Request, exc: StarletteHTTPException):
    if _wants_html(request):
        return error_page(exc.status_code if exc.status_code in ERROR_COPY else 500)
    detail = exc.detail if exc.status_code != 404 else "Not found."
    return JSONResponse({"error": detail}, status_code=exc.status_code)
