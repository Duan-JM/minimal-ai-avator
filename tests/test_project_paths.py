from pathlib import Path

from src.paths import BACKEND_DIR, CONFIG_FILE, DATA_DIR, MODELS_DIR, PROJECT_ROOT, STATIC_DIR


def test_project_paths_follow_split_layout():
    root = Path(__file__).resolve().parents[1]

    assert PROJECT_ROOT == root
    assert BACKEND_DIR == root / "backend"
    assert CONFIG_FILE == root / "backend" / "config.yml"
    assert STATIC_DIR == root / "frontend" / "static"
    assert DATA_DIR == root / "data"
    assert MODELS_DIR == root / "models"
    assert all(path.is_absolute() for path in (PROJECT_ROOT, BACKEND_DIR, CONFIG_FILE, STATIC_DIR, DATA_DIR, MODELS_DIR))
