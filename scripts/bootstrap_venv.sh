#!/usr/bin/env sh
set -eu

PROJECT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON="$VENV_DIR/bin/python"
SOURCE_DIR="$PROJECT_ROOT/src"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating project-local virtual environment without ensurepip..."
    python3 -m venv --without-pip "$VENV_DIR"
fi

# Direct execution is intentional: Android/qroot can return false negatives
# when test -x or stat follows a virtual-environment Python symlink.
if ! "$PYTHON" --version >/dev/null 2>&1; then
    echo "ERROR: $PYTHON could not be executed." >&2
    exit 1
fi

SITE_PACKAGES=$(PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')

if PYTHONDONTWRITEBYTECODE=1 "$PYTHON" - <<'PY'
from importlib.metadata import PackageNotFoundError, version

expected = {
    "pip": "24.0",
    "setuptools": "68.1.2",
    "wheel": "0.42.0",
    "packaging": "24.0",
}

try:
    valid = all(version(name) == wanted for name, wanted in expected.items())
except PackageNotFoundError:
    valid = False

raise SystemExit(0 if valid else 1)
PY
then
    echo "Pinned local bootstrap packages are already available."
else
    if find "$SITE_PACKAGES" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
        echo "ERROR: $SITE_PACKAGES is not empty; refusing to overwrite it." >&2
        exit 1
    fi

    GLOBAL_PIP_ROOT=$(python3 -c 'from pathlib import Path; import pip; print(Path(pip.__file__).resolve().parent.parent)')
    PYTHONPATH="$GLOBAL_PIP_ROOT" "$PYTHON" -m pip install \
        --target "$SITE_PACKAGES" \
        --no-deps \
        --no-cache-dir \
        --disable-pip-version-check \
        --no-input \
        "pip==24.0" \
        "setuptools==68.1.2" \
        "wheel==0.42.0" \
        "packaging==24.0"
fi

PTH_FILE="$SITE_PACKAGES/jarvis_x_local.pth"

if [ -f "$PTH_FILE" ]; then
    CURRENT_SOURCE=$(cat "$PTH_FILE")
    if [ "$CURRENT_SOURCE" != "$SOURCE_DIR" ]; then
        echo "ERROR: $PTH_FILE points somewhere else; refusing to overwrite it." >&2
        exit 1
    fi
elif [ -e "$PTH_FILE" ]; then
    echo "ERROR: $PTH_FILE exists but is not a regular file." >&2
    exit 1
else
    printf '%s\n' "$SOURCE_DIR" > "$PTH_FILE"
fi

PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -c 'import jarvis_x; print(f"JARVIS X {jarvis_x.__version__} is ready.")'
