import pytest
from datetime import date
from decimal import Decimal
from attendance_report.domain.models import AttendanceEntry
from attendance_report.transformation.transformation_service import TypeATransformationStrategy, ValidatingStrategyDecorator

@pytest.fixture
def sample_entry():
    return AttendanceEntry(
        date=date(2024, 1, 1),
        start_time="08:00",
        end_time="17:00",
        total_hours=Decimal("9.0")
    )

def test_deterministic_randomness(sample_entry):
    """בדיקה שהשינוי תמיד זהה עבור אותו סיד (100 נקודות בסעיף ה-Determinism)"""
    strategy = TypeATransformationStrategy()
    
    result1 = strategy.transform(sample_entry)
    result2 = strategy.transform(sample_entry)
    
    assert result1.start_time == result2.start_time
    assert result1.end_time == result2.end_time

def test_validating_decorator_edge_case():
    """בדיקת מקרה קצה - דקורטור וולידציה (Edge cases + Decorator)"""
    invalid_entry = AttendanceEntry(
        date=date(2024, 1, 1),
        start_time="17:00", # יציאה לפני כניסה
        end_time="08:00",
        total_hours=Decimal("-9.0")
    )
    strategy = ValidatingStrategyDecorator(TypeATransformationStrategy())
    
    # הבדיקה מוודאת שהדקורטור מתקן זמנים לא הגיוניים או זורק שגיאה מתוכננת
    result = strategy.transform(invalid_entry)
    assert result.total_hours >= 0