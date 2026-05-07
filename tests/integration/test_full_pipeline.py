import pytest
from pathlib import Path
from attendance_report.app import process_report

def test_full_pdf_to_pdf_flow(tmp_path):
    """
    בדיקת ה-Pipeline מקצה לקצה: PDF In -> PDF Out
    עונה על דרישת: fixture PDFs + script
    """
    # הגדרת נתיבים
    input_pdf = Path("tests/fixtures/sample_report.pdf")
    output_pdf = tmp_path / "result.pdf"
    
    # הרצת התהליך המלא
    report_type, num_entries = process_report(
        input_pdf=input_pdf,
        output_path=output_pdf,
        report_type="auto"
    )
    
    # Assertions
    assert output_pdf.exists() # הפלט נוצר
    assert num_entries > 0     # נשלפו נתונים
    assert report_type in ["type_a", "type_b"] # הזיהוי האוטומטי עבד