# Store Intelligence Platform

**Purplle Tech Challenge 2026 · Round 2**

Turn CCTV footage into real-time retail intelligence — visitor metrics, conversion funnels, zone heatmaps, and operational anomaly alerts.

---

## Overview

The platform follows a clear end-to-end pipeline:

```
CCTV Video  →  Detection Pipeline  →  Event Generation  →  Intelligence API  →  Analytics & Anomaly Detection
```

| Stage | What happens |
|-------|--------------|
| **CCTV Video** | Multi-camera MP4 feeds from a retail store (e.g. Brigade Bangalore F.O.H.) |
| **Detection Pipeline** | YOLO person detection, ByteTrack multi-object tracking, zone mapping, queue estimation |
| **Event Generation** | Canonical JSON events (`ENTRY`, `ZONE_ENTER`, `BILLING_QUEUE_JOIN`, `PURCHASE`, …) written to JSONL |
| **Intelligence API** | FastAPI service ingests events, maintains visitor sessions, and serves analytics |
| **Analytics & Anomaly Detection** | KPIs, conversion funnel, heatmaps, and rule-based anomaly alerts |

---

## Features

### Detection Pipeline

- **Person detection** — YOLOv8n (Ultralytics) filtered to COCO person class
- **Tracking** — ByteTrack via Supervision for stable per-frame track IDs
- **Re-identification** — Cross-camera visitor ID assignment (`ReIdentifier`)
- **Zone analytics** — Polygon-based zone mapping from store layout
- **Dwell detection** — Time-in-zone measurement with `ZONE_DWELL` events
- **Queue monitoring** — Billing queue depth estimation and join/abandon events

### Intelligence API

- **Event ingestion** — Batch POST with idempotent UUID deduplication (up to 500 events per request)
- **Metrics** — Unique visitors, conversion rate, dwell times, queue depth, abandonment rate
- **Funnel analysis** — Session-based Entry → Zone Visit → Billing Queue → Purchase funnel
- **Heatmaps** — Normalized zone engagement scores (0–100)
- **Anomaly detection** — Queue spikes, conversion drops, dead zones
- **Health monitoring** — Feed freshness checks and structured JSON logging

---

## Architecture

```
store-intelligence/
├── pipeline/          # Offline CV pipeline (detect, track, zones, emit)
├── app/               # FastAPI analytics service
├── dashboard/         # Streamlit live dashboard (bonus)
├── tests/             # Pytest suite (API + pipeline unit tests)
├── docs/              # Design docs and engineering decision log
├── scripts/           # Utility scripts (event loader)
├── output/            # Pipeline JSONL output (events_all.jsonl, cameras/)
└── data/              # SQLite database (created at runtime)
```

See [docs/DESIGN.md](docs/DESIGN.md) for the full system design and data-flow diagrams.

---

## Installation

**Step 1 — Clone the repository**

```bash
git clone <repository>
```

**Step 2 — Enter the project directory**

```bash
cd store-intelligence
```

**Step 3 — Install pipeline dependencies**

```bash
pip install -r requirements.txt
```

**Step 4 — Install API dependencies**

```bash
pip install -r requirements-api.txt
```

**Step 5 — Install dashboard dependencies (optional, for live dashboard)**

```bash
pip install -r requirements-dashboard.txt
```

> **Note:** YOLO weights (`yolov8n.pt`) are downloaded automatically on first pipeline run.

---

## Running Detection

Process a single video:

```bash
bash pipeline/run.sh /path/to/video.mp4
```

Process all cameras in a folder (recommended for multi-camera stores):

```bash
bash pipeline/run_batch.sh "/path/to/CCTV Footage"
```

On Windows:

```powershell
.\pipeline\run_batch.ps1 "C:\path\to\CCTV Footage"
```

### Output location

| File | Description |
|------|-------------|
| `output/events_all.jsonl` | Merged events from all cameras, sorted by timestamp |
| `output/cameras/cam_*_events.jsonl` | Per-camera event shards |

Each line is a self-contained JSON object matching the API ingestion schema.

---

## Loading Events Into API

With the API running (see below), bulk-load pipeline output:

```bash
python scripts/load_events.py output/events_all.jsonl
```

Optional API base URL:

```bash
python scripts/load_events.py output/events_all.jsonl --url http://127.0.0.1:8000
```

The loader sends events in batches of 500 and reports accepted / duplicate / rejected counts.

---

## Running API

**Local development:**

```bash
uvicorn app.main:app --reload
```

**Swagger UI:** http://localhost:8000/docs

**ReDoc:** http://localhost:8000/redoc

Optional database path override:

```bash
export STORE_INTEL_DB=data/store_intelligence.db   # Linux/macOS
set STORE_INTEL_DB=data/store_intelligence.db      # Windows
```

### Docker

```bash
docker compose up --build
```

The API is available at http://localhost:8000. SQLite data persists in `./data`; pipeline output is mounted from `./output`.

To start the optional PostgreSQL service (for future migration):

```bash
docker compose --profile postgres up --build
```

See comments in `docker-compose.yml` for wiring `DATABASE_URL` when switching from SQLite to PostgreSQL.

---

## Live Dashboard

The live dashboard closes the loop from the detection pipeline through the intelligence API to real-time KPIs and charts. It polls the same endpoints the API exposes to downstream consumers and refreshes every **2 seconds**.

### Run

Start the API in one terminal:

```bash
uvicorn app.main:app --reload
```

Load pipeline events (if you have not already):

```bash
python scripts/load_events.py output/events_all.jsonl
```

> Events in `output/events_all.jsonl` use store ID `STORE_BLR_002`. Either ingest as-is and set **Store ID** to `STORE_BLR_002` in the sidebar, or keep the default `default` and rely on **demo mode** (see below).

Start the dashboard in a second terminal (from `store-intelligence/`):

```bash
streamlit run dashboard/live_dashboard.py
```

Open the URL Streamlit prints (typically http://localhost:8501).

### How it stays in sync

| Mode | Data source | Updates when |
|------|-------------|--------------|
| **API** | `GET /stores/{id}/metrics`, `/funnel`, `/anomalies`, `GET /health` | New events are ingested via `POST /events/ingest` or `scripts/load_events.py` while the API is running |
| **Demo** | `output/events_all.jsonl` replayed into an in-memory metrics engine | API is unreachable; events are applied in small batches on each refresh to simulate a live feed |

The dashboard does **not** read the JSONL file directly when the API is healthy — it reflects whatever the API has ingested. As the pipeline or loader posts events, metrics, funnel stages, queue depth, and anomaly panels update on the next 2-second refresh cycle.

Optional environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DASHBOARD_API_URL` | `http://127.0.0.1:8000` | API base URL |
| `DASHBOARD_STORE_ID` | `default` | Store ID in `/stores/{id}/…` paths |

### Panels and charts

- **KPI row** — current visitors, conversion rate, average dwell, queue depth, active anomaly count
- **Charts** — visitor trend, zone dwell distribution, conversion funnel, queue depth trend
- **Anomaly panel** — severity, type, suggested action (e.g. queue spike → open another billing counter)
- **Health panel** — API status, last event timestamp, stale-feed warnings

---

## Running Tests

```bash
pytest
```

Generate a coverage report:

```bash
pytest --cov=app --cov=pipeline
```

Coverage target: **≥ 70%** statement coverage on `app/`.

---

## API Endpoints

All analytics responses use a consistent envelope: `{ "success": true, "data": … }`.

---

### `POST /events/ingest`

**Purpose:** Accept a batch of store events (1–500 per request). Validates schema, deduplicates by `event_id`, and updates visitor sessions. Supports partial success — valid events are stored even when others fail.

**Sample request**

```http
POST /events/ingest HTTP/1.1
Host: localhost:8000
Content-Type: application/json
```

```json
{
  "events": [
    {
      "event_id": "a1b2c3d4-e5f6-4789-a012-3456789abcde",
      "camera_id": "CAM_1",
      "store_id": "STORE_BLR_002",
      "visitor_id": "VIS_000001",
      "event_type": "ENTRY",
      "timestamp": "2026-05-31T10:00:00+00:00",
      "zone_id": "ZONE_ENTRANCE",
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.92,
      "metadata": { "session_seq": 0 }
    }
  ]
}
```

**Sample response** `200`

```json
{
  "accepted": 1,
  "rejected": 0,
  "duplicates": 0,
  "errors": []
}
```

Re-submitting the same `event_id` is idempotent:

```json
{
  "accepted": 0,
  "rejected": 0,
  "duplicates": 1,
  "errors": []
}
```

---

### `GET /stores/{id}/metrics`

**Purpose:** Return real-time store KPIs derived from ingested events and visitor sessions (staff activity excluded).

**Sample request**

```http
GET /stores/STORE_BLR_002/metrics HTTP/1.1
Host: localhost:8000
```

**Sample response** `200`

```json
{
  "success": true,
  "data": {
    "unique_visitors": 42,
    "conversion_rate": 0.35,
    "avg_dwell_per_zone": {
      "SKIN_CARE": 45000.0,
      "MAKEUP": 32000.0
    },
    "avg_queue_depth": 2.1,
    "abandonment_rate": 0.12
  }
}
```

---

### `GET /stores/{id}/funnel`

**Purpose:** Session-based conversion funnel: Entry → Zone Visit → Billing Queue → Purchase, with drop-off percentages between stages.

**Sample request**

```http
GET /stores/STORE_BLR_002/funnel HTTP/1.1
Host: localhost:8000
```

**Sample response** `200`

```json
{
  "success": true,
  "data": {
    "entry_count": 120,
    "zone_visit_count": 95,
    "billing_queue_count": 48,
    "purchase_count": 38,
    "dropoff_percentages": {
      "entry_to_zone_visit": 20.83,
      "zone_visit_to_billing": 49.47,
      "billing_to_purchase": 20.83,
      "entry_to_purchase": 68.33
    }
  }
}
```

---

### `GET /stores/{id}/heatmap`

**Purpose:** Normalized zone engagement heatmap combining visit frequency and average dwell time (scores 0–100).

**Sample request**

```http
GET /stores/STORE_BLR_002/heatmap HTTP/1.1
Host: localhost:8000
```

**Sample response** `200`

```json
{
  "success": true,
  "data": {
    "zones": [
      {
        "zone_id": "SKIN_CARE",
        "visit_frequency": 85,
        "avg_dwell_ms": 52000.0,
        "score": 92.5
      },
      {
        "zone_id": "MAKEUP",
        "visit_frequency": 62,
        "avg_dwell_ms": 38000.0,
        "score": 71.2
      }
    ],
    "data_confidence": true
  }
}
```

`data_confidence` is `true` when at least 20 visitor sessions exist.

---

### `GET /stores/{id}/anomalies`

**Purpose:** Detect operational anomalies: queue spikes, conversion rate drops vs 7-day baseline, and retail zones with no recent visits.

**Sample request**

```http
GET /stores/STORE_BLR_002/anomalies HTTP/1.1
Host: localhost:8000
```

**Sample response** `200`

```json
{
  "success": true,
  "data": [
    {
      "type": "QUEUE_SPIKE",
      "severity": "WARN",
      "suggested_action": "Open additional billing counter",
      "zone_id": null,
      "detail": "Current queue 8 vs rolling avg 3.2"
    },
    {
      "type": "DEAD_ZONE",
      "severity": "WARN",
      "suggested_action": "Inspect zone merchandising and staff allocation",
      "zone_id": "FRAGRANCE",
      "detail": "No visits for 35+ minutes"
    }
  ]
}
```

Returns `404` if the store ID has never received events.

---

### `GET /health`

**Purpose:** Service health snapshot including last ingested event timestamp, known store IDs, and feed freshness warnings.

**Sample request**

```http
GET /health HTTP/1.1
Host: localhost:8000
```

**Sample response** `200`

```json
{
  "status": "healthy",
  "last_event_timestamp": "2026-05-31T10:15:00+00:00",
  "stores": ["STORE_BLR_002"],
  "warnings": []
}
```

When no events have been ingested:

```json
{
  "status": "healthy",
  "last_event_timestamp": null,
  "stores": [],
  "warnings": ["NO_EVENTS: No events have been ingested yet"]
}
```

When the feed is stale (> 10 minutes since last event):

```json
{
  "status": "healthy",
  "last_event_timestamp": "2026-05-31T09:00:00+00:00",
  "stores": ["STORE_BLR_002"],
  "warnings": ["STALE_FEED"]
}
```

---

## AI-Assisted Development

Design rationale and trade-offs are documented in:

- [docs/DESIGN.md](docs/DESIGN.md) — System architecture, data flow, schema, and API contracts
- [docs/CHOICES.md](docs/CHOICES.md) — Engineering decision log (model selection, schema design, persistence)

### How AI was used

| Area | AI contribution |
|------|-----------------|
| **Architecture decisions** | Explored detection model trade-offs (YOLOv8n vs YOLOv8s vs RT-DETR), event schema design, and SQLite-vs-PostgreSQL persistence; documented in `CHOICES.md` |
| **Test generation** | Generated pytest fixtures, edge-case tests for ingestion idempotency, funnel math, anomaly thresholds, and pipeline unit tests |
| **Documentation** | Drafted system design diagrams, API contract specs, and structured logging conventions in `DESIGN.md` |

All AI-generated code was reviewed, adapted to project conventions, and validated against the challenge requirements and test suite.

---

## Future Improvements

- **PostgreSQL** — Swap SQLite connection factory for production-grade concurrent writes (schema already compatible)
- **Redis caching** — Cache hot metrics and heatmap responses with TTL invalidation on ingest
- **Kafka streaming** — Real-time event bus from pipeline → API instead of batch JSONL loading
- **Multi-store deployment** — Tenant isolation, per-store configuration, and horizontal API scaling
- **Dashboard UI** — React dashboard for live metrics, heatmaps, and anomaly alerts

---

## Troubleshooting

### Missing model weights

**Symptom:** Pipeline fails with a model download or file-not-found error.

**Fix:** Ensure network access on first run — Ultralytics downloads `yolov8n.pt` automatically. Alternatively, place the weights file in the project root and pass `--model yolov8n.pt` to `detect.py`.

### Database connection issues

**Symptom:** API returns `503 DATABASE_UNAVAILABLE`.

**Fix:** Check that the `data/` directory exists and is writable. With Docker, confirm `./data` is mounted and `STORE_INTEL_DB` points to `/app/data/store_intelligence.db`.

### Empty event files

**Symptom:** `output/events_all.jsonl` is empty or missing after pipeline run.

**Fix:** Verify video paths are correct and readable. Run with `--verbose` to inspect per-frame detection counts. Check that zone polygons in `pipeline/zones.py` match the camera viewpoint.

### No detections

**Symptom:** Pipeline completes but produces zero events.

**Fix:** Lower the confidence threshold (`--conf 0.25`). Confirm videos contain visible persons. On CPU, ensure OpenCV can decode the MP4 codec. Review camera-to-zone mapping — detections outside defined polygons won't emit zone events.

### API returns empty metrics

**Symptom:** Metrics endpoints return zeros after loading events.

**Fix:** Confirm events were loaded via `scripts/load_events.py` while the API was running. Check `GET /health` for ingested store IDs. Ensure events include `ENTRY` and session-driving types (`ZONE_ENTER`, `BILLING_QUEUE_JOIN`, etc.).

---

## License

Internal Purplle Tech Challenge 2026 submission.
