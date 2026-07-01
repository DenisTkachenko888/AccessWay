from app.models import AccessibilitySignal
from app.engine import calculate_score

def test_empty_signals_returns_unknown():
    # Имитируем отсутствие данных (как у 90% музеев)
    signals = [
        AccessibilitySignal(key="wheelchair_access", value=None, present=False, confidence="none", source_detail=""),
    ]
    result = calculate_score(signals)
    assert result.verdict == "unknown"
    assert result.confidence == "none"

def test_wheelchair_no_returns_unlikely_high():
    # Имитируем музей Щусева
    signals = [
        AccessibilitySignal(key="wheelchair_access", value="no", present=True, confidence="medium", source_detail=""),
    ]
    result = calculate_score(signals)
    assert result.verdict == "unlikely_accessible"
    assert result.confidence == "high"

def test_wheelchair_yes_alone_returns_medium():
    # Имитируем одинокий тег
    signals = [
        AccessibilitySignal(key="wheelchair_access", value="yes", present=True, confidence="medium", source_detail=""),
        AccessibilitySignal(key="entrance_step_free", value=None, present=False, confidence="none", source_detail=""),
    ]
    result = calculate_score(signals)
    assert result.verdict == "likely_accessible"
    assert result.confidence == "medium"

def test_high_density_signals_returns_high_confidence():
    # Имитируем идеальную разметку (3 позитивных сигнала)
    signals = [
        AccessibilitySignal(key="wheelchair_access", value="yes", present=True, confidence="medium", source_detail=""),
        AccessibilitySignal(key="entrance_step_free", value="yes", present=True, confidence="medium", source_detail=""),
        AccessibilitySignal(key="entrance_ramp", value="yes", present=True, confidence="medium", source_detail=""),
    ]
    result = calculate_score(signals)
    assert result.verdict == "likely_accessible"
    assert result.confidence == "high"  # Наша новая логика плотности сработала!

def test_limited_status_returns_partially_accessible():
    # Имитируем аптеку "Эвалар"
    signals = [
        AccessibilitySignal(key="wheelchair_access", value="limited", present=True, confidence="medium", source_detail=""),
    ]
    result = calculate_score(signals)
    assert result.verdict == "partially_accessible"
    assert result.confidence == "medium"