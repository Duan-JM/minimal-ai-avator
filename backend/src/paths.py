from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = PROJECT_ROOT / "models"
CONFIG_FILE = BACKEND_DIR / "config.yml"


def resolve_project_path(path: str | Path) -> Path:
    """Resolve project config paths relative to the repository root."""
    resolved = Path(path).expanduser()
    if resolved.is_absolute():
        return resolved
    return PROJECT_ROOT / resolved
