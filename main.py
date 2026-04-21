from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

from factory import ReportProcessorFactory
from services.calculation_service import CalculationService
from services.variation_service import ReliableVariationService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attendance Report Variation System: parse PDF, apply deterministic variations, recalculate, and generate output."
    )
    parser.add_argument("input_pdf", help="Path to source attendance PDF file.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output PDF file path (.pdf). Default: output_pdfs/<auto_name>.pdf",
        default=None,
    )
    parser.add_argument(
        "--hourly-rate",
        help="Optional hourly rate used for recalculating total pay.",
        default=None,
    )
    parser.add_argument(
        "--report-type",
        choices=("auto", "type_a", "type_b"),
        default="auto",
        help="Force report type. Use 'auto' (default) for detection.",
    )
    return parser


def _default_output_path(input_pdf: Path, report_type: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path("output_pdfs")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"attendance_report_{report_type}_{timestamp}.pdf"


def _parse_hourly_rate(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid --hourly-rate value. Use numeric format like 32 or 32.50.") from exc
    if parsed < Decimal("0"):
        raise ValueError("Hourly rate cannot be negative.")
    return parsed


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    input_pdf = Path(args.input_pdf).expanduser().resolve()
    if not input_pdf.exists() or not input_pdf.is_file():
        print(f"Input file not found: {input_pdf}", file=sys.stderr)
        return 1

    hourly_rate: Decimal | None
    try:
        hourly_rate = _parse_hourly_rate(args.hourly_rate)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        if args.report_type == "auto":
            processor = ReportProcessorFactory.create(str(input_pdf))
        else:
            processor = ReportProcessorFactory.create_for_type(args.report_type)
    except ValueError as exc:
        print(f"Unknown report type: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Failed to inspect PDF header (file may be corrupted): {exc}", file=sys.stderr)
        return 3

    print(f"Detected report type: {processor.report_type}")

    try:
        parsed_report = processor.parser.parse(str(input_pdf))
    except Exception as exc:
        print(f"Failed to parse PDF (unknown format or corrupted file): {exc}", file=sys.stderr)
        return 4

    print(f"Parsed entries: {len(parsed_report.entries)}")

    variation_service = ReliableVariationService()
    varied_report = variation_service.apply_variations(parsed_report)

    calculation_service = CalculationService()
    final_report = calculation_service.recalculate(varied_report, hourly_rate=hourly_rate)

    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path(input_pdf, processor.report_type).resolve()
    if output_path.suffix.lower() != ".pdf":
        print("Output path must end with .pdf", file=sys.stderr)
        return 1

    try:
        processor.generator.generate(final_report, str(output_path))
    except RuntimeError as exc:
        print(f"Generation error: {exc}", file=sys.stderr)
        return 5
    except Exception as exc:
        print(f"Failed to generate output: {exc}", file=sys.stderr)
        return 6

    print(f"Generated PDF path: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
