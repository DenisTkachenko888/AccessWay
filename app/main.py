from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from app.models import PlaceReport
from app.collector import fetch_raw_places
from app.engine import extract_signals, calculate_score

app = FastAPI(title="Accessibility Finder API - MVP 0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Разрешаем запросы отовсюду (только для локальной отладки!)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/search", response_model=List[PlaceReport])
async def search_places(lat: float, lon: float, category: str = "cafe", radius: int = 500):
    try:
        raw_elements = await fetch_raw_places(lat, lon, radius, category)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Overpass error: {str(e)}")

    reports = []
    for el in raw_elements:
        tags = el.get("tags", {})
        
        # 1. Извлекаем сигналы
        signals = extract_signals(tags)
        
        # 2. Оцениваем
        scoring = calculate_score(signals)
        
        # 3. Формируем ответ
        lat_coord = el.get("lat") or el.get("center", {}).get("lat", 0.0)
        lon_coord = el.get("lon") or el.get("center", {}).get("lon", 0.0)
        
        reports.append(PlaceReport(
            name=tags.get("name", "Unknown Name"),
            category=category,
            lat=lat_coord,
            lon=lon_coord,
            osm_id=el.get("id", 0),
            signals_extracted=len(signals),
            signals=signals,
            scoring=scoring
        ))
        
    return reports