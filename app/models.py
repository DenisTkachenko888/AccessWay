from pydantic import BaseModel, Field
from typing import List, Optional

class AccessibilitySignal(BaseModel):
    key: str
    value: Optional[str]
    present: bool
    confidence: str = Field(pattern="^(high|medium|low|none)$")
    source_detail: str

class ScoringOutput(BaseModel):
    verdict: str = Field(pattern="^(likely_accessible|partially_accessible|risky|unlikely_accessible|unknown)$")
    confidence: str = Field(pattern="^(high|medium|low|none)$")
    reasons: List[str]
    risks: List[str]
    unknowns: List[str]

class PlaceReport(BaseModel):
    name: str
    category: str
    lat: float
    lon: float
    osm_id: int
    signals_extracted: int
    signals: List[AccessibilitySignal]
    scoring: ScoringOutput