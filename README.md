# AccessWay

AccessWay is a mobile-first accessibility intelligence service for urban places.

Instead of treating accessibility as one unreliable `wheelchair=yes/no` flag, AccessWay collects evidence, keeps track of uncertainty and freshness, and returns an explainable assessment:

- `verdict` — likely accessible / partially accessible / risky / unlikely accessible / unknown;
- `confidence` — how strongly the available data supports the verdict;
- `reasons` — evidence supporting the assessment;
- `risks` — contradictions, stale data, or known barriers;
- `unknowns` — accessibility facts that were checked but are still missing;
- `decision_advice` — a practical recommendation derived from the current evidence.

The long-term model is source-agnostic:

```text
OSM / user confirmation / official data / visual analysis
                         ↓
                      Evidence
                         ↓
                   Scoring Engine
                         ↓
                     PlaceReport
                         ↓
                 Mobile-first client
```

## Current MVP

MVP 0.2 focuses on one complete user flow:

1. search by a human-readable address or use device geolocation;
2. choose a place category and search radius;
3. find nearby OpenStreetMap POIs;
4. extract accessibility evidence;
5. calculate an explainable verdict and confidence level;
6. show results in a mobile-first interface.

The current frontend is deliberately lightweight (`debug.html`) while the product contract is stabilised. It is no longer a raw debugger: it consumes the same API contract intended for the future React/PWA client.

## Architecture

```text
app/
├── main.py          # FastAPI orchestration, search endpoints, ranking
├── collector.py     # Overpass + Nominatim I/O
├── engine.py        # pure Evidence extraction and scoring logic
├── models.py        # Pydantic API/domain contracts
├── database.py      # async SQLAlchemy configuration
└── db_models.py     # current PostGIS persistence model

tests/
└── test_engine.py   # pure scoring and adversarial tests

debug.html           # first mobile-first AccessWay shell
```

A PostGIS persistence layer and OSM PBF ingestion script are already present, but the live scoring path currently reads from Overpass. This keeps the MVP understandable while the future multi-source persistence model is designed.

## Tech stack

- Python 3.10+
- FastAPI
- Pydantic v2
- aiohttp
- OpenStreetMap / Overpass API
- Nominatim geocoding
- PostgreSQL + PostGIS (persistence groundwork)
- SQLAlchemy + Alembic
- Pytest
- Docker Compose

## Run locally

### Python

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies and start the API:

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

Open `debug.html` in a browser to use the current mobile-first client.

> Browser geolocation normally requires a secure context (`https://`) or localhost. Address search works without geolocation permission.

### Docker Compose

```bash
docker compose up --build
```

The API is exposed on port `8000`; PostgreSQL/PostGIS on port `5432`.

## Environment variables

```text
ACCESSWAY_CITY=Moscow
ACCESSWAY_DEBUG=false
ACCESSWAY_CORS_ORIGINS=*
ACCESSWAY_USER_AGENT=AccessWay/0.1 (accessibility research MVP)
DATABASE_URL=postgresql+asyncpg://accessway_user:accessway_password@localhost:5432/accessway
```

For a public deployment, set a descriptive `ACCESSWAY_USER_AGENT` with project contact information and restrict `ACCESSWAY_CORS_ORIGINS` to the deployed frontend origin.

## API

### Health

```http
GET /api/v1/health
```

### Nearby search

```http
GET /api/v1/search/nearby?lat=55.7558&lon=37.6173&category=cafe&radius_m=700&limit=20
```

Returns a list of `PlaceReport` objects.

### Address search

```http
GET /api/v1/search/address?query=Москва%2C%20Красная%20площадь&category=cafe&radius_m=700&limit=20
```

Response shape:

```json
{
  "query": "Москва, Красная площадь",
  "location": {
    "lat": 55.7536,
    "lon": 37.6210,
    "display_name": "..."
  },
  "results": []
}
```

### Single OSM place

```http
GET /api/v1/places/osm/{osm_type}/{osm_id}
```

### Raw evidence inspection

```http
GET /api/v1/places/osm/{osm_type}/{osm_id}/evidence
```

This endpoint is intended for development and validation of the evidence extractor.

## Evidence model

The central domain object is `Evidence`, not an OSM-specific accessibility signal. One evidence item records:

- `fact_key` — what accessibility fact is described;
- `source_type` / `source_id` — where the fact came from;
- `raw_value` and normalized `interpreted_value`;
- `present`;
- evidence-level `confidence`;
- collection and observation timestamps;
- freshness metadata.

This is the main architectural foundation for future user confirmations, official datasets, and image-based analysis.

## Tests

```bash
python -m pytest tests/test_engine.py
```

The scoring engine is deliberately pure, so core decisions can be tested without network or database mocks.

## Near-term roadmap

1. Stabilise the first mobile user flow and replace the temporary HTML shell with a React/TypeScript PWA.
2. Add user accessibility needs and make them influence scoring.
3. Redesign scoring to aggregate multiple Evidence items for the same fact rather than assuming one source per fact.
4. Move persistence to an internal `place_id + sources + evidence` model.
5. Add lightweight user confirmations and contradiction handling.
6. Add MapLibre as the product map layer.
7. Later: accessibility-aware routing and visual evidence sources.

## License

MIT
