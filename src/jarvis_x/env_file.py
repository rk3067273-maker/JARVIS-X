"""Minimal, non-executing loader for the workspace-local .env file."""

from __future__ import annotations

import ast
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from jarvis_x.core.errors import ConfigurationError

_ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_text(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines without shell expansion or secret output."""

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"Invalid .env syntax at line {line_number}.")

        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _ENVIRONMENT_KEY.fullmatch(key):
            raise ConfigurationError(f"Invalid .env key at line {line_number}.")
        if key in values:
            raise ConfigurationError(f"Duplicate .env key: {key}.")

        value = raw_value.strip()
        if value.startswith(("'", '"')):
            try:
                parsed = ast.literal_eval(value)
            except (SyntaxError, ValueError) as error:
                raise ConfigurationError(
                    f"Invalid quoted .env value at line {line_number}."
                ) from error
            if not isinstance(parsed, str):
                raise ConfigurationError(f"Invalid .env value at line {line_number}.")
            value = parsed
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()

        values[key] = value
    return values


def load_local_env(path: Path) -> dict[str, str]:
    """Load a non-symlinked, owner-only .env without mutating os.environ."""

    if path.is_symlink():
        raise ConfigurationError("The local .env must not be a symbolic link.")
    if not path.is_file():
        raise ConfigurationError("The workspace-local .env file is missing.")

    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions != 0o600:
        raise ConfigurationError("The local .env must use exact owner-only permissions (chmod 600).")

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError("The workspace-local .env could not be read safely.") from error
    return parse_env_text(content)


def merge_configuration_environment(
    local_values: Mapping[str, str],
    process_values: Mapping[str, str],
    *,
    allowed_keys: frozenset[str],
) -> dict[str, str]:
    """Merge recognized keys with explicit process environment precedence."""

    merged = {key: value for key, value in local_values.items() if key in allowed_keys}
    for key in allowed_keys:
        if key in process_values:
            merged[key] = process_values[key]
    return merged
