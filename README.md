# Attendance Report Variation System

Python CLI tool for parsing attendance PDFs, applying deterministic time variations, recalculating payroll totals, and generating HTML/PDF output.

## Features
- Parses multiple report formats with factory-based parser selection.
- Applies deterministic "reliable variations" to entry/exit times.
- Recalculates daily totals, overtime (`125%`, `150%`), and monthly totals.
- Generates RTL Hebrew reports as HTML and PDF.
- Supports UTF-8 output and Hebrew font configuration for PDF rendering.

## Installation

```bash
pip install pdfplumber Jinja2 WeasyPrint
```

## Hebrew / UTF-8 Notes
- HTML is written as UTF-8 (`<meta charset="UTF-8">` and file encoding `utf-8`).
- For best Hebrew rendering in PDF, place a Hebrew TTF font file in one of:
  - `assets/fonts/NotoSansHebrew-Regular.ttf`
  - `assets/fonts/Rubik-Regular.ttf`
  - `assets/fonts/Assistant-Regular.ttf`
- If no local font file is found, the system falls back to Hebrew-capable system fonts (`Arial`, `Noto Sans Hebrew`, `Rubik`).

## Usage

Basic usage:

```bash
python main.py "input_files_example/a_r_9.pdf"
```

Custom output path:

```bash
python main.py "input_files_example/n_r_5_n.pdf" --output "output_pdfs/report_n5.pdf"
```

Generate HTML instead of PDF:

```bash
python main.py "input_files_example/a_r_25.pdf" --output "output_pdfs/report_a25.html"
```

Override hourly rate for pay calculation:

```bash
python main.py "input_files_example/n_r_10_n.pdf" --hourly-rate 32.50
```

Force report type when auto-detection fails:

```bash
python main.py "input_files_example/a_r_9.pdf" --report-type type_a
```

## Processing Flow
1. `ReportProcessorFactory` detects report type.
2. Parser (`TypeAParser` / `TypeBParser`) extracts structured data.
3. `ReliableVariationService` applies deterministic time offsets.
4. `CalculationService` recalculates totals and overtime.
5. Generator renders Jinja template and exports HTML/PDF.

## Error Handling
- Unknown report type: clear error and non-zero exit.
- Corrupted/invalid PDF: parse/detection errors are reported with context.
- Missing WeasyPrint for PDF output: explicit runtime message.
