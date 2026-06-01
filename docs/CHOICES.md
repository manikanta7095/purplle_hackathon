# Engineering Choices Log

**Project:** Purplle Store Intelligence  
**Purpose:** Record major decisions, what AI tools recommended, and what the team actually shipped.

---

## Decision 1: Detection Model Selection

### Options considered

| Option | Role | Typical use |
|--------|------|-------------|
| **YOLOv8n** | Nano variant, ~3M params | Edge / CPU batch CV |
| **YOLOv8s** | Small variant, better mAP | GPU stores, fewer cameras |
| **RT-DETR** | Transformer detector, NMS-free | Accuracy-critical, GPU |

### What AI suggested

- Prefer **RT-DETR** or **YOLOv8s** for retail: “entrances are small in frame” and “conversion metrics depend on not missing people.”
- Treat YOLOv8n as “prototype only” and upgrade before production.

### Final choice

**YOLOv8n** (`yolov8n.pt`, default in `pipeline/detect.py`)

### Why

1. **Batch runtime** — Five camera MP4s must complete on developer laptops (CPU). Nano was the only variant that kept per-frame latency within a practical overnight batch.
2. **Person-only task** — We filter COCO class 0; we do not need open-vocabulary detection.
3. **Tracker coupling** — ByteTrack recovers short occlusions; detector recall gaps at 0.35 confidence were acceptable after visual QA on entrance zones.
4. **Upgrade path** — `PipelineConfig.model_path` allows per-camera YOLOv8s on GPU workers without architectural change.

### Trade-offs

| Gain | Cost |
|------|------|
| Fast CPU inference, simple Ultralytics integration | Lower mAP on distant / partially occluded shoppers |
| Small artifact size for CI and demos | More calibration work on zone polygons vs relying on detector precision |
| Consistent with challenge time budget | May need YOLOv8s or tensor RT on GPU for 20+ camera stores |

---

## Decision 2: Event Schema Design

### Options considered

| Approach | Description |
|----------|-------------|
| **Minimal schema** | `{ visitor_id, event_type, timestamp }` only |
| **Session-centric schema** | Events + derived `visitor_sessions` table with funnel flags |
| **Rich event schema** | Full CV telemetry per event (embeddings, full bbox tracks, frame URLs) |

### What AI suggested

- **Rich event schema** stored in the database: embed bbox arrays, track histories, and frame pointers “so you never re-run the pipeline.”
- Alternatively, a **minimal schema** with all analytics computed in-stream during ingestion.

### Final choice

**Rich canonical event contract, session-centric analytics storage**

- **Events table:** Full `StoreEvent` fields (`event_id`, zones, `dwell_ms`, `confidence`, `metadata` JSON for `queue_depth`, `sku_zone`, `session_seq`).
- **Sessions table:** Denormalized funnel flags (`has_zone_visit`, `has_billing_queue`, `has_purchase`, `is_abandoned`) updated incrementally at ingest time.

### Why

1. **Contract stability** — Judges and `load_events.py` consume the documented JSON shape; pipeline and API share `emit.py` / Pydantic models.
2. **Query performance** — Funnel and conversion are O(sessions), not O(events). Replaying millions of ZONE_DWELL rows per request is unnecessary for Round 2 endpoints.
3. **Pragmatic richness** — We kept operational fields (queue depth, SKU zone) but **did not** persist full bbox histories in SQLite (AI’s richest option). Track history stays in the pipeline; only behavioral transitions cross the boundary.
4. **Re-entry** — `session_seq` in metadata plus `REENTRY` event type bridges physical re-entrance to analytics without collapsing multiple visits into one session incorrectly.

### Trade-offs

| Gain | Cost |
|------|------|
| Idempotent ingest via `event_id` | Two layers to keep in sync (events + sessions) |
| Fast `/funnel` and `/metrics` | Session update logic must handle ENTRY / REENTRY / EXIT edge cases |
| Extensible `metadata` without migrations | JSON metadata is less query-friendly than normalized columns until PostgreSQL JSONB |

---

## Decision 3: API Architecture

### Options considered

| Pattern | Description |
|---------|-------------|
| **Monolithic FastAPI** | Single app, modular routers, one SQLite file |
| **Microservices** | Separate ingest, metrics, anomaly services |
| **Event-driven** | Kafka + stream processors + materialized views |

### What AI suggested

- **Event-driven microservices** with a dedicated ingest service and Kafka topic `store.events` “because you’ll scale beyond one store.”
- Generate OpenAPI clients per service and deploy on Kubernetes.

### Final choice

**Monolithic FastAPI** (`app/main.py` + routers)

### Why

1. **Scope** — Round 2 deliverable is one store, batch-ingested events, and five REST surfaces. Operational complexity of Kafka was not justified.
2. **Transactional ingest** — Session updates and event inserts share one SQLite connection and transaction boundary; splitting services would require distributed transactions or eventual consistency handling not needed yet.
3. **Testability** — `pytest` + `TestClient` achieve ≥70% coverage on `app/` with in-memory DB overrides (`reset_app_database`).
4. **Extraction ready** — Routers mirror future services (`ingestion`, `metrics`, `funnel`, `anomalies`, `health`); the event JSON contract is already the integration point.

### Trade-offs

| Gain | Cost |
|------|------|
| One `uvicorn` command for judges | All endpoints scale together (no independent autoscaling per concern) |
| Simple local dev and CI | CPU-bound anomaly + metrics work blocks the same process under heavy load |
| Clear module boundaries for later split | No built-in backpressure if ingest outpaces analytics (mitigate with queue + worker later) |

---

## Summary matrix

| Decision | AI bias | Shipped | Override? |
|----------|---------|---------|-----------|
| Detector | RT-DETR / YOLOv8s | YOLOv8n | **Yes** — throughput over mAP |
| Schema | Rich CV telemetry in DB | Rich events + session flags, no bbox store | **Partial** — rejected full telemetry persistence |
| API shape | Microservices + Kafka | Monolithic FastAPI | **Yes** — simplicity for challenge scope |

---

*See [DESIGN.md](./DESIGN.md) for architecture detail and AI-assisted decision narratives.*
