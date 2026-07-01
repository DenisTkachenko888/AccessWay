from typing import Dict, List
from app.models import AccessibilitySignal, ScoringOutput

def extract_signals(tags: Dict[str, str]) -> List[AccessibilitySignal]:
    """Преобразует сырые теги OSM в массив стандартизированных сигналов."""
    signals = []
    
    # Сигнал 1: Основной статус инвалидной коляски
    wheelchair = tags.get("wheelchair")
    signals.append(AccessibilitySignal(
        key="wheelchair_access",
        value=wheelchair,
        present=bool(wheelchair),
        confidence="medium" if wheelchair else "none",
        source_detail=f"OSM tag wheelchair={wheelchair}" if wheelchair else "Tag missing"
    ))
    
    # Сигнал 2: Вход без ступеней
    step_free = tags.get("entrance:step_free") or tags.get("step_free")
    signals.append(AccessibilitySignal(
        key="entrance_step_free",
        value=step_free,
        present=bool(step_free),
        confidence="medium" if step_free else "none",
        source_detail=f"OSM tag step_free={step_free}" if step_free else "Tag missing"
    ))

    # Сигнал 3: Наличие пандуса
    ramp = tags.get("ramp") or tags.get("ramp:wheelchair")
    signals.append(AccessibilitySignal(
        key="entrance_ramp",
        value=ramp,
        present=bool(ramp),
        confidence="medium" if ramp else "none",
        source_detail=f"OSM tag ramp={ramp}" if ramp else "Tag missing"
    ))

    # Сигнал 4: Доступный туалет
    toilet = tags.get("toilets:wheelchair")
    signals.append(AccessibilitySignal(
        key="accessible_toilet",
        value=toilet,
        present=bool(toilet),
        confidence="medium" if toilet else "none",
        source_detail=f"OSM tag toilets:wheelchair={toilet}" if toilet else "Tag missing"
    ))
    
    return signals

def calculate_score(signals: List[AccessibilitySignal]) -> ScoringOutput:
    """Выносит вердикт исключительно на основе переданных сигналов."""
    present_signals = [s for s in signals if s.present]
    unknowns = [s.key for s in signals if not s.present]
    
    # Главное архитектурное правило: нет данных = честный Unknown
    if not present_signals:
        return ScoringOutput(
            verdict="unknown",
            confidence="none",
            reasons=[],
            risks=["No accessibility tags found for this location."],
            unknowns=unknowns
        )
        
    wc_signal = next((s for s in signals if s.key == "wheelchair_access"), None)
    
    # Обработка явных статусов
    if wc_signal and wc_signal.value == "yes":
        # НОВАЯ ЛОГИКА: Плотность сигналов
        # Ищем подтверждающие метки с позитивным значением (yes, true, 1)
        supporting_signals = [
            s for s in present_signals 
            if s.key != "wheelchair_access" and s.value in ("yes", "true", "1", "designated")
        ]
        
        if len(supporting_signals) >= 2:
            # У нас есть wheelchair=yes + еще как минимум 2 сигнала (итого 3+)
            reasons = ["Explicitly tagged as wheelchair accessible."]
            for s in supporting_signals:
                reasons.append(f"Confirmed by supporting detail: {s.key}={s.value}")
                
            return ScoringOutput(
                verdict="likely_accessible",
                confidence="high",
                reasons=reasons,
                risks=[],
                unknowns=unknowns
            )
        else:
            # Только базовая метка, недостаточно деталей для 100% гарантии
            return ScoringOutput(
                verdict="likely_accessible",
                confidence="medium",
                reasons=["Tagged as wheelchair accessible."],
                risks=["Confidence is medium due to a lack of detailed entrance/toilet tags."],
                unknowns=unknowns
            )
            
    elif wc_signal and wc_signal.value == "no":
        return ScoringOutput(
            verdict="unlikely_accessible",
            confidence="high",
            reasons=["Explicitly tagged as NOT wheelchair accessible."],
            risks=[],
            unknowns=unknowns
        )
        
    elif wc_signal and wc_signal.value == "limited":
        reasons = ["Tagged as having limited accessibility (partially accessible)."]
        
        ramp_signal = next((s for s in signals if s.key == "entrance_ramp"), None)
        if ramp_signal and ramp_signal.value == "yes":
            reasons.append("Ramp is confirmed to be present.")

        return ScoringOutput(
            verdict="partially_accessible",
            confidence="medium",
            reasons=reasons,
            risks=["May contain minor barriers (e.g., small step or manual door)."],
            unknowns=unknowns
        )
        
    # Fallback
    return ScoringOutput(
        verdict="unknown",
        confidence="low",
        reasons=[],
        risks=[f"Tag present ({wc_signal.value if wc_signal else 'various'}), but insufficient to determine overall accessibility."],
        unknowns=unknowns
    )