from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from core.entities import AttendanceReport
from core.interfaces import BaseGenerator

try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:  # pragma: no cover - optional dependency fallback
    Environment = None  # type: ignore[assignment]
    FileSystemLoader = None  # type: ignore[assignment]
    select_autoescape = None  # type: ignore[assignment]

try:
    from weasyprint import HTML
except ImportError:  # pragma: no cover - optional dependency fallback
    HTML = None  # type: ignore[assignment]


class JinjaHtmlGenerator(BaseGenerator):
    """Render attendance report HTML and optionally convert it to PDF."""

    def __init__(self, template_name: str) -> None:
        self.template_name = template_name
        self._templates_dir = Path(__file__).resolve().parent / "templates"
        self._project_root = Path(__file__).resolve().parent.parent

    def generate(self, report: AttendanceReport, output_path: str) -> None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        rendered = self.render_html(report)
        suffix = output.suffix.lower()
        if suffix == ".pdf":
            self._write_pdf(rendered_html=rendered, output_path=output)
            return

        output.write_text(rendered, encoding="utf-8")

    def render_html(self, report: AttendanceReport) -> str:
        if Environment is not None:
            env = Environment(
                loader=FileSystemLoader(str(self._templates_dir)),
                autoescape=select_autoescape(enabled_extensions=("html", "xml")),
            )
            template = env.get_template(self.template_name)
            return template.render(**self._build_context(report))

        return self._build_fallback_html(report)

    def _build_context(self, report: AttendanceReport) -> dict[str, object]:
        regular_hours_by_row: dict[int, str] = {}
        monthly_ot_125 = Decimal("0")
        monthly_ot_150 = Decimal("0")

        for idx, entry in enumerate(report.entries):
            day_total = self._to_decimal(entry.total_hours)
            ot_125 = self._to_decimal(entry.overtime_125)
            ot_150 = self._to_decimal(entry.overtime_150)
            regular = max(day_total - ot_125 - ot_150, Decimal("0"))
            regular_hours_by_row[idx] = f"{regular:.2f}"
            monthly_ot_125 += ot_125
            monthly_ot_150 += ot_150

        monthly_regular_hours = max(report.totals.total_hours - monthly_ot_125 - monthly_ot_150, Decimal("0"))
        hourly_rate_display = "0.00"
        if report.totals.total_hours > Decimal("0") and report.totals.total_pay > Decimal("0"):
            rate = report.totals.total_pay / report.totals.total_hours
            hourly_rate_display = f"{rate.quantize(Decimal('0.01')):.2f}"

        return {
            "report": report,
            "entries": report.entries,
            "metadata": report.employee_metadata,
            "totals": report.totals,
            "regular_hours_by_row": regular_hours_by_row,
            "monthly_regular_hours": monthly_regular_hours,
            "monthly_ot_125": monthly_ot_125,
            "monthly_ot_150": monthly_ot_150,
            "hourly_rate_display": hourly_rate_display,
            "font_css": self._build_font_css(),
        }

    @staticmethod
    def _to_decimal(value: str) -> Decimal:
        cleaned = value.strip().replace(",", ".")
        if not cleaned:
            return Decimal("0")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return Decimal("0")

    @staticmethod
    def _write_pdf(rendered_html: str, output_path: Path) -> None:
        if HTML is None:
            raise RuntimeError("WeasyPrint is required for PDF output. Install with: pip install weasyprint")
        HTML(string=rendered_html).write_pdf(str(output_path))

    def _build_font_css(self) -> str:
        embedded_font = self._discover_hebrew_font_file()
        if embedded_font is None:
            return "body { font-family: 'Noto Sans Hebrew', 'Arial', 'Rubik', sans-serif; }"

        font_uri = embedded_font.as_uri()
        return (
            "@font-face {"
            "font-family: 'ReportHebrew';"
            f"src: url('{font_uri}') format('truetype');"
            "font-weight: normal;"
            "font-style: normal;"
            "}"
            "body { font-family: 'ReportHebrew', 'Noto Sans Hebrew', 'Arial', sans-serif; }"
        )

    def _discover_hebrew_font_file(self) -> Path | None:
        candidates: Iterable[Path] = (
            self._project_root / "assets" / "fonts" / "NotoSansHebrew-Regular.ttf",
            self._project_root / "assets" / "fonts" / "Rubik-Regular.ttf",
            self._project_root / "assets" / "fonts" / "Assistant-Regular.ttf",
        )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _build_fallback_html(report: AttendanceReport) -> str:
        rows = []
        for entry in report.entries:
            rows.append(
                (
                    "<tr>"
                    f"<td>{entry.date}</td>"
                    f"<td>{entry.day}</td>"
                    f"<td>{entry.location}</td>"
                    f"<td>{entry.entry_time}</td>"
                    f"<td>{entry.exit_time}</td>"
                    f"<td>{entry.break_duration}</td>"
                    f"<td>{entry.total_hours}</td>"
                    f"<td>{entry.overtime_125}</td>"
                    f"<td>{entry.overtime_150}</td>"
                    "</tr>"
                )
            )

        rows_html = "\n".join(rows)
        return (
            "<!doctype html>"
            "<html lang='he' dir='rtl'>"
            "<head><meta charset='utf-8'><title>Attendance Report</title></head>"
            "<body>"
            "<table border='1' cellpadding='4' cellspacing='0'>"
            "<thead><tr><th>תאריך</th><th>יום</th><th>מיקום</th><th>כניסה</th><th>יציאה</th>"
            "<th>הפסקה</th><th>סה\"כ</th><th>125%</th><th>150%</th></tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            "</table>"
            "</body>"
            "</html>"
        )


class TypeAGenerator(JinjaHtmlGenerator):
    def __init__(self) -> None:
        super().__init__(template_name="type_a.html.j2")


class TypeBGenerator(JinjaHtmlGenerator):
    def __init__(self) -> None:
        super().__init__(template_name="type_b.html.j2")
