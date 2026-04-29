from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from attendance_report.parsing.classifier import ReportProcessorFactory
from attendance_report.transformation.calculation_service import CalculationService
from attendance_report.transformation.transformation_service import (
    TransformationService,
    TypeATransformationStrategy,
    TypeBTransformationStrategy,
    ValidatingStrategyDecorator,
)


def process_report(
    input_pdf: Path,
    output_path: Path,
    report_type: str = "auto",
    hourly_rate: Decimal | None = None,
) -> tuple[str, int]:
    if report_type == "auto":
        processor = ReportProcessorFactory.create(str(input_pdf))
    else:
        processor = ReportProcessorFactory.create_for_type(report_type)

    parsed_report = processor.parser.parse(str(input_pdf))
    # Recovery path: if selected parser produced no rows, try the other known type.
    if not parsed_report.entries:
        for alt_type in ("type_a", "type_b"):
            if alt_type == processor.report_type:
                continue
            try:
                alt_processor = ReportProcessorFactory.create_for_type(alt_type)
                alt_report = alt_processor.parser.parse(str(input_pdf))
            except Exception:
                continue
            if alt_report.entries:
                processor = alt_processor
                parsed_report = alt_report
                break
    parsed_report.source_pdf_path = str(input_pdf)

    registry = {
        "type_a": ValidatingStrategyDecorator(TypeATransformationStrategy()),
        "type_b": ValidatingStrategyDecorator(TypeBTransformationStrategy()),
    }
    transformation_service = TransformationService(strategy_registry=registry)
    varied_report = transformation_service.transform(parsed_report, processor.report_type)

    calculation_service = CalculationService()
    final_report = calculation_service.recalculate(varied_report, hourly_rate=hourly_rate)

    processor.generator.generate(final_report, str(output_path))
    return processor.report_type, len(final_report.entries)
