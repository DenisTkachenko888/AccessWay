# Accessibility Finder API - MVP 0 (Scoring Engine)

An intelligent backend service that evaluates the accessibility of urban places using OpenStreetMap (OSM) data. 

Unlike standard map proxies that simply filter out places without accessibility tags, this engine implements a **Confidence-Based Scoring System**. It collects raw spatial data, extracts specific accessibility signals (wheelchair access, step-free entrances, ramps, accessible toilets), and calculates a structured verdict. It handles missing data gracefully by returning honest `unknown` states rather than hiding locations from users.

## 🚀 Features

- **Raw Data Collector:** Fetches complete POI data via Overpass API without premature filtering.
- **Signal Extractor:** Normalizes chaotic OSM tags into standardized boolean signals.
- **Scoring Engine (Core):** - Calculates verdicts: `likely_accessible`, `partially_accessible`, `unlikely_accessible`, `unknown`.
  - Determines confidence levels (`high`, `medium`, `low`, `none`) based on signal density.
  - Generates human-readable arrays of `reasons`, `risks`, and `unknowns` for frontend rendering.
- **CORS-ready & Debuggable:** Includes a built-in visualizer setup for rapid local testing.

## 🛠 Tech Stack

- **Python 3.10+** (Tested on 3.12)
- **FastAPI** (Web framework)
- **aiohttp** (Asynchronous HTTP client for Overpass API)
- **Pydantic** (Strict data validation and contract enforcement)
- **Pytest** (Unit testing for pure business logic)

## 🏗 Architecture & Project Structure

The project strictly separates data collection from business logic to allow 100% test coverage of the scoring engine without network mocking.

```text
├── app/
│   ├── main.py           # FastAPI orchestration layer
│   ├── models.py         # Pydantic schemas (Data Contracts)
│   ├── collector.py      # Overpass API interactions (No business logic)
│   └── engine.py         # Pure functions: Signal Extraction & Scoring
├── tests/
│   └── test_engine.py    # Unit tests for the scoring logic edge cases
├── requirements.txt
└── debug.html            # Local vanilla JS visualizer for debugging
```

## ⚙️ Installation & Local Development

1. **Create and activate a virtual environment:**
```bash
python -m venv .venv
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate
```

2. **Install dependencies:**
```bash
pip install -r requirements.txt
```

3. **Run the development server:**
```bash
uvicorn app.main:app --reload
```

Open the API docs (Swagger UI): `http://127.0.0.1:8000/docs`

## 🧪 Testing

The Scoring Engine is fully covered by unit tests. To run them:

```bash
python -m pytest tests/test_engine.py
```

## 📡 API Reference

### `GET /api/search`

Searches for places and evaluates their accessibility.

**Query Parameters:**
- `lat` (float): Latitude
- `lon` (float): Longitude
- `category` (string): e.g., `pharmacy`, `cafe`, `museum`
- `radius` (int): Search radius in meters (default: 500)

**Response Sample (`PlaceReport`):**
```json
[
  {
    "name": "Pharmacy Example",
    "category": "pharmacy",
    "lat": 55.758,
    "lon": 37.613,
    "osm_id": 2145730008,
    "signals_extracted": 4,
    "signals": [
      {
        "key": "wheelchair_access",
        "value": "limited",
        "present": true,
        "confidence": "medium",
        "source_detail": "OSM tag wheelchair=limited"
      }
    ],
    "scoring": {
      "verdict": "partially_accessible",
      "confidence": "medium",
      "reasons": [
        "Tagged as having limited accessibility (partially accessible)."
      ],
      "risks": [
        "May contain minor barriers (e.g., small step or manual door)."
      ],
      "unknowns": [
        "entrance_step_free",
        "entrance_ramp",
        "accessible_toilet"
      ]
    }
  }
]
```

## 📄 License
MIT License