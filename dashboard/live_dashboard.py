"""
Store Intelligence Live Dashboard — real-time KPIs, charts, and anomaly alerts.

Run from the project root:
    streamlit run dashboard/live_dashboard.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from fastapi import HTTPException

# Project root on sys.path so demo mode can reuse the metrics engine.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.anomalies import detect_anomalies  # noqa: E402
from app.database import Database  # noqa: E402
from app.funnel import compute_funnel  # noqa: E402
from app.health import build_health_response  # noqa: E402
from app.ingestion import IngestRequest, ingest_events  # noqa: E402
from app.metrics import compute_store_metrics  # noqa: E402
from app.models import AnomalySeverity, AnomalyType  # noqa: E402

REFRESH_SECONDS = 2
HISTORY_LIMIT = 120
DEFAULT_API_URL = os.getenv("DASHBOARD_API_URL", "http://127.0.0.1:8000")
DEFAULT_STORE_ID = os.getenv("DASHBOARD_STORE_ID", "default")
EVENTS_JSONL = PROJECT_ROOT / "output" / "events_all.jsonl"
DEMO_BATCH_SIZE = 8

FUNNEL_STAGES = [
    ("Entry", "entry_count"),
    ("Zone Visit", "zone_visit_count"),
    ("Billing Queue", "billing_queue_count"),
    ("Purchase", "purchase_count"),
]

SEVERITY_COLORS = {
    "CRITICAL": "#dc2626",
    "WARN": "#f59e0b",
    "INFO": "#2563eb",
}

ANOMALY_LABELS = {
    AnomalyType.QUEUE_SPIKE.value: "Queue Spike",
    AnomalyType.CONVERSION_DROP.value: "Conversion Drop",
    AnomalyType.DEAD_ZONE.value: "Dead Zone",
}


def _init_session_state() -> None:
    defaults = {
        "visitor_history": [],
        "queue_history": [],
        "demo_db": None,
        "demo_events": [],
        "demo_cursor": 0,
        "demo_store_id": None,
        "last_mode": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _api_base() -> str:
    return st.session_state.get("api_url", DEFAULT_API_URL).rstrip("/")


def _store_id() -> str:
    return st.session_state.get("store_id", DEFAULT_STORE_ID)


def _unwrap_envelope(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload and payload.get("success") is True:
        return payload["data"]
    return payload


def _api_available() -> bool:
    try:
        response = requests.get(f"{_api_base()}/health", timeout=1.5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _fetch_api(path: str) -> Tuple[Optional[Any], Optional[str]]:
    try:
        response = requests.get(f"{_api_base()}{path}", timeout=3.0)
        if response.status_code == 404:
            return {}, None
        response.raise_for_status()
        return response.json(), None
    except requests.RequestException as exc:
        return None, str(exc)


def _safe_detect_anomalies(db: Database, store_id: str) -> List[Dict[str, Any]]:
    try:
        return [a.model_dump(mode="json") for a in detect_anomalies(db, store_id)]
    except HTTPException:
        return []


def _load_jsonl_events(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    events.sort(key=lambda row: row.get("timestamp", ""))
    return events


def _remap_store(events: List[Dict[str, Any]], target_store: str) -> List[Dict[str, Any]]:
    remapped: List[Dict[str, Any]] = []
    for event in events:
        copy = dict(event)
        copy["store_id"] = target_store
        remapped.append(copy)
    return remapped


def _demo_db_path() -> Path:
    return PROJECT_ROOT / "data" / ".live_dashboard_demo.db"


def _get_demo_db() -> Database:
    if st.session_state.demo_db is None:
        path = _demo_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        st.session_state.demo_db = Database(path)
    return st.session_state.demo_db


def _reset_demo_db() -> None:
    path = _demo_db_path()
    if path.exists():
        path.unlink()
    st.session_state.demo_db = None


def _ensure_demo_events_loaded() -> None:
    if st.session_state.demo_events:
        return
    raw = _load_jsonl_events(EVENTS_JSONL)
    if not raw:
        st.session_state.demo_events = []
        st.session_state.demo_store_id = _store_id()
        return
    source_store = raw[0].get("store_id", _store_id())
    st.session_state.demo_store_id = source_store
    target = _store_id()
    st.session_state.demo_events = (
        _remap_store(raw, target) if target != source_store else raw
    )


def _advance_demo_feed() -> None:
    _ensure_demo_events_loaded()
    events = st.session_state.demo_events
    if not events:
        return
    cursor = st.session_state.demo_cursor
    if cursor >= len(events):
        st.session_state.demo_cursor = 0
        _reset_demo_db()
        cursor = 0
    batch = events[cursor : cursor + DEMO_BATCH_SIZE]
    if batch:
        db = _get_demo_db()
        ingest_events(db, IngestRequest(events=batch))
        st.session_state.demo_cursor = cursor + len(batch)


def _fetch_live_data() -> Dict[str, Any]:
    use_api = st.session_state.get("force_api", False) or _api_available()
    mode = "api" if use_api else "demo"
    st.session_state.last_mode = mode

    if mode == "api":
        store = _store_id()
        metrics_raw, metrics_err = _fetch_api(f"/stores/{store}/metrics")
        funnel_raw, funnel_err = _fetch_api(f"/stores/{store}/funnel")
        anomalies_raw, anomalies_err = _fetch_api(f"/stores/{store}/anomalies")
        health_raw, health_err = _fetch_api("/health")

        if metrics_err and funnel_err:
            use_api = False
            mode = "demo"
            st.session_state.last_mode = mode
        else:
            health = health_raw if health_err is None else {}
            return {
                "mode": mode,
                "metrics": _unwrap_envelope(metrics_raw) if metrics_raw else {},
                "funnel": _unwrap_envelope(funnel_raw) if funnel_raw else {},
                "anomalies": _unwrap_envelope(anomalies_raw) if anomalies_raw else [],
                "health": health if isinstance(health, dict) else {},
                "errors": [e for e in (metrics_err, funnel_err, anomalies_err, health_err) if e],
            }

    _advance_demo_feed()
    db = _get_demo_db()
    store = _store_id()
    metrics = compute_store_metrics(db, store).model_dump(mode="json")
    sessions = db.fetch_sessions_for_store(store, exclude_staff=True)
    funnel = compute_funnel(sessions).model_dump(mode="json")
    anomalies = _safe_detect_anomalies(db, store)
    health = build_health_response(db).model_dump(mode="json")

    return {
        "mode": "demo",
        "metrics": metrics,
        "funnel": funnel,
        "anomalies": anomalies,
        "health": health,
        "errors": [],
    }


def _count_open_visitors(store_id: str) -> Optional[int]:
    if st.session_state.last_mode != "demo" or st.session_state.demo_db is None:
        return None
    db: Database = st.session_state.demo_db
    sessions = db.fetch_sessions_for_store(store_id, exclude_staff=True)
    return sum(1 for session in sessions if session.ended_at is None)


def _average_dwell_ms(metrics: Dict[str, Any]) -> float:
    dwell_map = metrics.get("avg_dwell_per_zone") or {}
    if not dwell_map:
        return 0.0
    values = [float(v) for v in dwell_map.values() if v]
    return sum(values) / len(values) if values else 0.0


def _format_duration(ms: float) -> str:
    if ms <= 0:
        return "0s"
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def _append_history(key: str, timestamp: datetime, value: float) -> None:
    history: List[Tuple[str, float]] = st.session_state[key]
    history.append((timestamp.isoformat(), value))
    if len(history) > HISTORY_LIMIT:
        st.session_state[key] = history[-HISTORY_LIMIT:]


def _severity_rank(severity: str) -> int:
    order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    return order.get(severity, 3)


def _anomaly_title(anomaly: Dict[str, Any]) -> str:
    raw_type = anomaly.get("type", "")
    if isinstance(raw_type, dict):
        raw_type = raw_type.get("value", "")
    return ANOMALY_LABELS.get(str(raw_type), str(raw_type).replace("_", " ").title())


def _render_kpis(data: Dict[str, Any]) -> None:
    metrics = data.get("metrics") or {}
    anomalies = data.get("anomalies") or []
    store = _store_id()

    open_visitors = _count_open_visitors(store)
    visitor_count = (
        open_visitors
        if open_visitors is not None
        else int(metrics.get("unique_visitors", 0))
    )
    conversion = float(metrics.get("conversion_rate", 0.0)) * 100.0
    avg_dwell = _average_dwell_ms(metrics)
    queue_depth = float(metrics.get("avg_queue_depth", 0.0))
    active_anomalies = len(anomalies)

    now = datetime.now(timezone.utc)
    _append_history("visitor_history", now, float(visitor_count))
    _append_history("queue_history", now, queue_depth)

    cols = st.columns(5)
    cols[0].metric(
        "Current Visitors",
        f"{visitor_count}",
        help="Open sessions in demo mode; unique visitors from API metrics otherwise.",
    )
    cols[1].metric("Conversion Rate", f"{conversion:.1f}%")
    cols[2].metric("Avg Dwell Time", _format_duration(avg_dwell))
    cols[3].metric("Queue Depth", f"{queue_depth:.1f}")
    cols[4].metric("Active Anomalies", str(active_anomalies))


def _chart_visitor_trend() -> None:
    history = st.session_state.visitor_history
    if not history:
        st.info("Collecting visitor samples…")
        return
    df = pd.DataFrame(history, columns=["timestamp", "visitors"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    fig = px.line(
        df,
        x="timestamp",
        y="visitors",
        title="Visitor Count Over Time",
        markers=True,
    )
    fig.update_layout(
        height=320,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Visitors",
        xaxis_title="",
    )
    st.plotly_chart(fig, use_container_width=True)


def _chart_zone_dwell(metrics: Dict[str, Any]) -> None:
    dwell_map = metrics.get("avg_dwell_per_zone") or {}
    if not dwell_map:
        st.info("No zone dwell data yet.")
        return
    df = pd.DataFrame(
        [{"zone": zone, "dwell_sec": ms / 1000.0} for zone, ms in dwell_map.items()]
    ).sort_values("dwell_sec", ascending=True)
    fig = px.bar(
        df,
        x="dwell_sec",
        y="zone",
        orientation="h",
        title="Zone Dwell Distribution",
        labels={"dwell_sec": "Avg dwell (seconds)", "zone": "Zone"},
    )
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)


def _chart_funnel(funnel: Dict[str, Any]) -> None:
    labels = [stage[0] for stage in FUNNEL_STAGES]
    values = [int(funnel.get(stage[1], 0)) for stage in FUNNEL_STAGES]
    fig = go.Figure(
        go.Funnel(
            y=labels,
            x=values,
            textinfo="value+percent initial",
        )
    )
    fig.update_layout(
        title="Conversion Funnel",
        height=380,
        margin=dict(l=20, r=20, t=50, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    dropoff = funnel.get("dropoff_percentages") or {}
    if dropoff:
        parts = [
            f"Entry→Zone: {dropoff.get('entry_to_zone_visit', 0):.1f}%",
            f"Zone→Billing: {dropoff.get('zone_visit_to_billing', 0):.1f}%",
            f"Billing→Purchase: {dropoff.get('billing_to_purchase', 0):.1f}%",
        ]
        st.caption("Drop-off · " + " · ".join(parts))


def _chart_queue_trend() -> None:
    history = st.session_state.queue_history
    if not history:
        st.info("Collecting queue depth samples…")
        return
    df = pd.DataFrame(history, columns=["timestamp", "queue_depth"])
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    fig = px.area(
        df,
        x="timestamp",
        y="queue_depth",
        title="Queue Depth Trend",
    )
    fig.update_layout(
        height=320,
        margin=dict(l=20, r=20, t=40, b=20),
        yaxis_title="Avg queue depth",
        xaxis_title="",
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_anomalies(anomalies: List[Dict[str, Any]]) -> None:
    st.subheader("Anomaly Panel")
    if not anomalies:
        st.success("No active anomalies detected.")
        return

    sorted_anomalies = sorted(
        anomalies,
        key=lambda item: _severity_rank(
            item.get("severity", {}).get("value", item.get("severity", "INFO"))
            if isinstance(item.get("severity"), dict)
            else str(item.get("severity", "INFO"))
        ),
    )

    for anomaly in sorted_anomalies:
        severity = anomaly.get("severity", "INFO")
        if isinstance(severity, dict):
            severity = severity.get("value", "INFO")
        severity = str(severity).upper()
        color = SEVERITY_COLORS.get(severity, "#64748b")
        title = _anomaly_title(anomaly)
        action = anomaly.get("suggested_action", "Review store operations")
        detail = anomaly.get("detail")
        zone = anomaly.get("zone_id")

        st.markdown(
            f"""
            <div style="border-left:4px solid {color};padding:0.75rem 1rem;
            margin-bottom:0.75rem;background:#f8fafc;border-radius:4px;">
              <div style="font-weight:700;color:{color};">{severity}</div>
              <div style="font-size:1.05rem;font-weight:600;">{title}</div>
              {f'<div style="color:#475569;font-size:0.9rem;">Zone: {zone}</div>' if zone else ''}
              {f'<div style="color:#475569;font-size:0.9rem;">{detail}</div>' if detail else ''}
              <div style="margin-top:0.35rem;"><strong>Suggested Action:</strong> {action}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_health(health: Dict[str, Any], mode: str) -> None:
    st.subheader("Health Panel")
    status = health.get("status", "unknown")
    last_ts = health.get("last_event_timestamp")
    warnings = health.get("warnings") or []
    stores = health.get("stores") or []

    status_color = "#16a34a" if status == "healthy" and not warnings else "#f59e0b"
    if any("STALE_FEED" in str(w) for w in warnings):
        status_color = "#dc2626"

    st.markdown(
        f'**API status:** <span style="color:{status_color};font-weight:600;">'
        f"{status.upper()}</span>",
        unsafe_allow_html=True,
    )
    st.write(f"**Last event:** {last_ts or '—'}")
    if stores:
        st.write(f"**Stores ingested:** {', '.join(stores)}")
    st.write(f"**Feed mode:** `{mode}`")

    if warnings:
        for warning in warnings:
            label = "Stale feed warning" if warning == "STALE_FEED" else warning
            st.warning(label)
    else:
        st.success("Feed is fresh — no stale warnings.")

    if mode == "demo":
        cursor = st.session_state.demo_cursor
        total = len(st.session_state.demo_events)
        st.caption(
            f"Demo playback: {cursor}/{total} events from `{EVENTS_JSONL.name}` "
            f"(refreshes every {REFRESH_SECONDS}s)."
        )


def _render_sidebar() -> None:
    with st.sidebar:
        st.header("Configuration")
        st.session_state.api_url = st.text_input(
            "API base URL",
            value=st.session_state.get("api_url", DEFAULT_API_URL),
        )
        st.session_state.store_id = st.text_input(
            "Store ID",
            value=st.session_state.get("store_id", DEFAULT_STORE_ID),
            help="API paths use /stores/{id}/…. Demo mode remaps JSONL store IDs to this value.",
        )
        st.session_state.force_api = st.checkbox(
            "Require API (disable demo fallback)",
            value=st.session_state.get("force_api", False),
        )
        if st.button("Reset demo playback"):
            st.session_state.demo_cursor = 0
            _reset_demo_db()
            st.session_state.visitor_history = []
            st.session_state.queue_history = []
            st.rerun()


def _live_body() -> None:
    data = _fetch_live_data()
    mode = data["mode"]
    metrics = data.get("metrics") or {}
    funnel = data.get("funnel") or {}
    anomalies = data.get("anomalies") or []
    health = data.get("health") or {}
    errors = data.get("errors") or []

    badge = "🟢 API" if mode == "api" else "🟡 Demo"
    st.caption(f"{badge} · auto-refresh every {REFRESH_SECONDS}s · store `{_store_id()}`")

    if errors:
        st.warning("Partial API errors: " + "; ".join(errors))

    _render_kpis(data)

    left, right = st.columns(2)
    with left:
        _chart_visitor_trend()
        _chart_funnel(funnel)
    with right:
        _chart_zone_dwell(metrics)
        _chart_queue_trend()

    health_col, anomaly_col = st.columns([1, 1])
    with health_col:
        _render_health(health, mode)
    with anomaly_col:
        _render_anomalies(anomalies)


def main() -> None:
    st.set_page_config(
        page_title="Store Intelligence Live",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_session_state()
    _render_sidebar()

    st.title("Store Intelligence Live Dashboard")
    st.markdown(
        "Real-time view of the detection pipeline → ingestion API → metrics engine."
    )

    fragment = getattr(st, "fragment", None)
    if fragment is not None:
        fragment(run_every=timedelta(seconds=REFRESH_SECONDS))(_live_body)()
    else:
        _live_body()
        time.sleep(REFRESH_SECONDS)
        st.rerun()


if __name__ == "__main__":
    main()
