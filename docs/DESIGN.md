# Store Intelligence — System Design

**Purplle Retail Analytics · Brigade Bangalore (F.O.H)**  
**Version:** 1.0 · **Last updated:** May 2026

---

## 1. System Overview

The Store Intelligence platform turns CCTV footage into actionable retail KPIs. It is deliberately split into an **offline detection pipeline** (computer vision on video) and an **online analytics API** (event ingestion and business logic). The two halves communicate only through a stable JSON event contract, which keeps CV experimentation independent from API deployments.

### End-to-end flow

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐     ┌─────────────────┐
│ CCTV Video  │────▶│ YOLO Detection   │────▶│ ByteTrack   │────▶│ Event Generation │
│ (MP4 feeds) │     │ (detect.py)      │     │ (tracker.py)│     │ (emit.py +       │
└─────────────┘     └──────────────────┘     └─────────────┘     │  EventEngine)    │
                                                                  └────────┬────────┘
                                                                           │
                     ┌──────────────────┐     ┌──────────────────────────▼──────────┐
                     │ JSONL / HTTP POST│◀────│ Zone mapping · Queue · Re-ID        │
                     │ (events)         │     │ (zones.py, queue.py, classifiers)   │
                     └────────┬─────────┘     └─────────────────────────────────────┘
                              │
                     ┌────────▼─────────┐     ┌──────────────┐     ┌────────────────┐
                     │ Event Ingestion  │────▶│ SQLite DB    │────▶│ Metrics Engine │
                     │ POST /events/    │     │ events +     │     │ (metrics.py)   │
                     │ ingest           │     │ sessions     │     └───────┬────────┘
                     └──────────────────┘     └──────────────┘             │
                              │                                            │
                     ┌────────▼─────────┐     ┌──────────────┐     ┌───────▼────────┐
                     │ Session lifecycle│     │ Funnel       │     │ Anomaly        │
                     │ (ingestion.py)   │     │ (funnel.py)  │     │ Detection      │
                     └──────────────────┘     └──────────────┘     │ (anomalies.py) │
                                                                    └───────┬────────┘
                                                                            │
                     ┌──────────────────────────────────────────────────────▼────────┐
                     │ REST Endpoints: /health, /stores/{id}/metrics, /funnel,         │
                     │ /heatmap, /anomalies                                            │
                     └─────────────────────────────────────────────────────────────────┘
```

**Operational summary**

| Stage | Responsibility | Output |
|-------|----------------|--------|
| Detection | Find persons per frame | Bounding boxes + confidence |
| Tracking | Assign stable `track_id` | `TrackRecord` per person |
| Zones | Map bbox center → store zone | `zone_id`, optional `sku_zone` |
| Events | Emit behavioral transitions | Canonical `StoreEvent` JSON |
| Ingestion | Validate, dedupe, persist | Rows in `events`, `visitor_sessions` |
| Analytics | Aggregate sessions + events | KPIs, funnel, heatmap, anomalies |

---

## 2. Detection Pipeline Architecture

The pipeline lives under `pipeline/`. A single orchestrator in `detect.py` wires detection, tracking, zone logic, and emission. Batch runners (`run.sh`, `run_batch.ps1`, `batch.py`) process multiple camera files into `output/events_all.jsonl` and per-camera JSONL shards.

### `detect.py`

- **Role:** Frame loop, model inference, and `EventEngine` orchestration.
- **Model:** Ultralytics YOLOv8n (`yolov8n.pt`) filtered to COCO person class (`PERSON_CLASS_ID`).
- **Key types:**
  - `PersonDetector` — loads weights, runs `detect_frame()` per BGR frame.
  - `PipelineConfig` — video path, camera/store IDs, confidence threshold, device.
  - `EventEngine` — converts `TrackRecord` updates into `StoreEvent` instances (ENTRY, ZONE_ENTER/EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN/ABANDON).
  - `SessionState` — per-track FSM: entrance flag, queue state, previous zone, dwell timestamps, `session_seq`.
- **Dependencies:** `StaffClassifier`, `ReIdentifier` (placeholders), `QueueEstimator`, `ZoneMapper`.
- **Output:** Append-only JSONL via `save_event_json()`; timestamps derived from video FPS.

### `tracker.py`

- **Role:** Multi-object tracking with **ByteTrack** (via `supervision` `ByteTrack` tracker).
- **Key type:** `TrackManager`
  - Accepts `FrameDetections` from the detector.
  - Maintains `TrackRecord` per `track_id`: bbox/confidence history, first/last seen frame and timestamp, zone history.
  - Configurable `lost_track_buffer` (default 30 frames) to tolerate brief occlusions.
- **Contract:** Downstream code never re-associates detections manually; all identity stability comes from ByteTrack until `ReIdentifier` maps `track_id` → `visitor_id`.

### `zones.py`

- **Role:** Spatial analytics aligned to the Brigade Bangalore floor plan.
- **Key types:**
  - `StoreZone` — enum of named zones (`ZONE_ENTRANCE`, brand gondolas, `BILLING_QUEUE`, `CASH_COUNTER`, etc.).
  - `ZonePolygon` — axis-aligned regions in **normalized** [0, 1] coordinates (calibrate per camera via homography in production).
  - `ZoneMapper` — `map_bbox_to_zone()` / `map_bbox_to_sku_zone()` using bbox center point-in-polygon tests.
- **Note:** `BRIGADE_BANGALORE_ZONES` ships with placeholder polygons; deployment requires per-camera calibration against the layout spreadsheet.

### `emit.py`

- **Role:** Canonical event schema and JSONL persistence.
- **Schema:** `StoreEvent` with UUID `event_id`, typed `EventType`, ISO-8601 `timestamp`, `dwell_ms`, `is_staff`, `confidence`, and nested `EventMetadata` (`queue_depth`, `sku_zone`, `session_seq`).
- **Functions:** `build_event()`, `generate_event_id()`, `save_event_json()`, validation helpers.
- **Event types:** ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY (defined for API parity; pipeline may emit REENTRY when re-entrance logic is extended beyond ENTRY + `session_seq`).

Supporting modules: `models.py` (detection dataclasses), `queue.py` (billing queue depth estimation), `classifiers.py` (staff / re-ID placeholders).

---

## 3. Intelligence API Architecture

The API is a **monolithic FastAPI** application (`app/main.py`) with modular routers. Persistence uses SQLite today with a PostgreSQL-compatible DDL in `app/database.py`.

### `ingestion.py`

- **Endpoint:** `POST /events/ingest` (batch up to 500 events).
- **Flow:**
  1. Per-event Pydantic validation (`StoreEvent` — UUIDv4 `event_id`, typed enums).
  2. In-batch and database **deduplication** on `event_id`.
  3. Sort accepted events by `timestamp` before insert.
  4. Partial success: valid rows persist even when siblings fail.
  5. `process_session_for_event()` updates `visitor_sessions` (skipped for `is_staff`).
- **Session rules:**
  - `ENTRY` opens a session only if none is open; duplicate ENTRY on open session updates flags without a second funnel entry.
  - `REENTRY` resumes an open session or creates one if the visitor returned after EXIT.
  - `EXIT` sets `ended_at`.
  - Zone visits, queue joins, purchases, and abandonment mutate boolean session flags used by metrics and funnel.

### `metrics.py`

- **Endpoints:** `GET /stores/{store_id}/metrics`, `GET /stores/{store_id}/heatmap`.
- **Computation:** Loads non-staff `visitor_sessions` and raw events from SQLite.
- **KPIs:** `unique_visitors` (distinct `visitor_id`), `conversion_rate`, `avg_dwell_per_zone`, `avg_queue_depth`, `abandonment_rate`.
- **Helpers:** `compute_metrics_from_sessions()`, `compute_daily_conversion_rates()` for anomaly baselines, heatmap aggregation excluding `SYSTEM_ZONES`.

### `funnel.py`

- **Endpoint:** `GET /stores/{store_id}/funnel`.
- **Model:** Session-based funnel stages — Entry → Zone visit → Billing queue → Purchase.
- **Design choice:** Counts are **per session**, not per event, so re-entry and duplicate ENTRY do not inflate visitor counts.

### `anomalies.py`

- **Endpoint:** `GET /stores/{store_id}/anomalies`.
- **Detectors:**
  - `QUEUE_SPIKE` — latest `queue_depth` in metadata vs rolling window (20 events, 1.5× threshold).
  - `CONVERSION_DROP` — current conversion vs 7-day daily average (85% threshold).
  - `DEAD_ZONE` — retail zones with no `ZONE_ENTER` in 30 minutes.
- **Output:** Typed `Anomaly` with severity (INFO / WARN / CRITICAL) and `suggested_action`.

### `health.py`

- **Endpoint:** `GET /health`.
- **Checks:** Lists ingested `store_id`s, `last_event_timestamp`, warnings (`NO_EVENTS`, `STALE_FEED` if no event for > 10 minutes).
- **Purpose:** Load-balancer and ops dashboards; does not gate analytics computation.

**Cross-cutting:** `exceptions.py` (structured errors, no stack traces to clients), `middleware/logging_middleware.py` (JSON logs with `trace_id`, `latency_ms`, `event_count`).

---

## 4. Data Flow

### Event lifecycle (frame → REST response)

1. **Frame acquisition** — OpenCV reads MP4; `frame_id` and `timestamp = frame_id / fps`.
2. **Detection** — YOLOv8n returns person boxes above `confidence_threshold` (default 0.35).
3. **Tracking** — ByteTrack assigns/updates `track_id`; `TrackManager` merges zone into `TrackRecord`.
4. **Spatial mapping** — Bbox center → `StoreZone`; optional SKU category from polygon metadata.
5. **Identity** — `ReIdentifier.resolve_visitor_id(track_id)` → `VIS_XXXXXX` (placeholder: stable within video).
6. **State machine** — `EventEngine` compares current zone vs `SessionState`:
   - First entrance polygon hit → `ENTRY`, increment `session_seq`.
   - Zone change → `ZONE_EXIT` (with dwell) + `ZONE_ENTER`.
   - Periodic → `ZONE_DWELL` (~1 Hz) with accumulated `dwell_ms`.
   - Billing queue polygon → `BILLING_QUEUE_JOIN` / `BILLING_QUEUE_ABANDON`.
7. **Serialization** — `build_event()` assigns UUID, ISO timestamp, metadata (`queue_depth`, `sku_zone`, `session_seq`).
8. **Persistence (offline)** — Append to `output/*.jsonl`; optional `scripts/load_events.py` POSTs batches to the API.
9. **Ingestion** — API validates, dedupes on `event_id`, inserts into `events`, runs session lifecycle.
10. **Analytics read path** — Metrics/funnel/anomalies queries read sessions + events; responses wrapped in `{ "success": true, "data": ... }`.

### Idempotency and ordering

- Replaying the same `event_id` yields `duplicates` count, zero new rows, unchanged KPIs.
- Within a batch, events are sorted by `timestamp` before session updates to reduce out-of-order artifacts.

---

## 5. Scalability Considerations

### Event deduplication

- **Primary key:** `event_id` (UUIDv4) on `events` table.
- **Ingestion layers:** (1) duplicate IDs within a single POST body, (2) `db.event_exists()` before insert, (3) race-safe `INSERT` with duplicate handling.
- **Why it matters:** Pipeline retries, overlapping camera batches, and `load_events.py` re-runs must not double-count visitors or corrupt funnel metrics.

### Modular services

- Pipeline and API are separate processes with no shared in-memory state.
- Routers (`ingestion`, `metrics`, `funnel`, `anomalies`, `health`) map to logical service boundaries; they can be extracted behind an API gateway later without changing the event contract.
- CV dependencies (`ultralytics`, `opencv`) stay out of the API container image.

### Database abstraction

- `Database` class centralizes SQL, connection context managers, and row dataclasses (`EventRow`, `SessionRow`).
- DDL uses portable types (`TEXT`, `REAL`, `INTEGER`) and parameterized queries.
- `metadata_json` stores flexible pipeline fields without schema migrations for every new sensor attribute.

### Future PostgreSQL migration

- Schema comments and column choices target PostgreSQL compatibility (ISO timestamps as TEXT today; migrate to `TIMESTAMPTZ`).
- Swap `sqlite3` connection factory for `psycopg` / async pool; keep router and business logic unchanged.
- Add connection pooling, read replicas for `/metrics` and `/heatmap`, and partitioning on `(store_id, timestamp)` at event volume > ~10M rows/day.

**Near-term scale path:** Multiple pipeline workers → object storage for JSONL → message queue (Kafka/SQS) → horizontally scaled API instances → PostgreSQL + Redis cache for hot metrics.

---

## 6. AI-Assisted Decisions

The following examples reflect real trade-offs encountered while building this system. AI pair-programming accelerated exploration; **human judgment retained ownership of production constraints.**

### Example A — Detector choice: YOLOv8n vs RT-DETR

| | |
|---|---|
| **Problem** | Choose a person detector that runs on CPU across five hours of multi-camera CCTV without missing entrance traffic. |
| **What AI suggested** | Use **RT-DETR** for higher mAP and cleaner NMS-free outputs; “worth the extra latency for retail accuracy.” |
| **What we chose** | **YOLOv8n** with confidence threshold 0.35 on CPU. |
| **Why** | Profiling on Brigade footage showed RT-DETR at ~2–3× frame time on CPU, pushing full-store batch processing past acceptable overnight windows. YOLOv8n plus ByteTrack was sufficient for doorway-scale boxes; false negatives were addressed by zone-centered bbox logic rather than a heavier backbone. **We overrode AI’s accuracy-first default in favor of throughput.** |

### Example B — Database: SQLite vs PostgreSQL for Round 2

| | |
|---|---|
| **Problem** | Persist events and sessions for demo metrics, tests, and judge evaluation with zero DevOps setup. |
| **What AI suggested** | Start with **PostgreSQL + Docker Compose** immediately so “you don’t migrate later under pressure.” |
| **What we chose** | **SQLite** with PostgreSQL-compatible DDL and `STORE_INTEL_DB` path override. |
| **Why** | Submission requirements favor a single-command API start (`uvicorn`) and deterministic pytest on ephemeral DB files. The abstraction layer in `database.py` was the compromise: AI’s migration concern was valid, but operational simplicity won for the challenge timeline. PostgreSQL remains the documented production target. |

### Example C — Re-entry session handling

| | |
|---|---|
| **Problem** | Visitors leave and re-enter the store within one trading day; naive ENTRY counting inflates `unique_visitors` and funnel entry. |
| **What AI suggested** | Treat every second **`ENTRY` as a new session** and deduplicate visitors only by `visitor_id` per calendar day (simpler code). |
| **What we chose** | **Session-centric model:** `ENTRY` does not open a second session if one is already open; **`REENTRY`** event type resumes or creates sessions explicitly; funnel and `unique_visitors` use session rows and distinct `visitor_id`. Pipeline increments `session_seq` on each physical entrance. |
| **Why** | Partially aligned with AI on using `session_seq` in metadata, but **rejected** the “every ENTRY = new session” shortcut after tests showed double-counting on exit-and-return journeys. `test_reentry_counts_visitor_once` encodes the intended behavior. |

---

## Related documents

- [CHOICES.md](./CHOICES.md) — Decision log for model, schema, and API shape.
- [../README.md](../README.md) — Install, run, and API samples.
