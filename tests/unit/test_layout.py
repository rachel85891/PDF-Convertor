from pathlib import Path


def test_project_root_layout():
    root = Path(__file__).resolve().parents[2]
    assert (root / "src").exists()
    assert (root / "docs").exists()
    assert (root / "tests").exists()
    assert (root / "pyproject.toml").exists()
