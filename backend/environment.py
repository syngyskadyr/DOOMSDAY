"""Explicit backend-owned .env loading; the ML package never reads files.

Only the repository's .env is considered by default. No parent-directory search,
shell execution, or variable interpolation is performed.
"""
import io
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT / ".env"


class EnvironmentFileError(ValueError):
    """Safe startup error which never includes file contents or secret values."""


def load_environment(env_file=None, *, enabled=True, environ=None):
    """Load defaults into the process environment, preserving existing values.

    None selects the optional repository .env. An explicit path must exist and
    be readable. Passing enabled=False performs no file reads or mutations.
    A custom mapping is supported for isolated callers/tests. Return the loaded
    path or None; values are deliberately never logged or returned.
    """
    if not enabled:
        return None
    target = os.environ if environ is None else environ
    path = DEFAULT_ENV_FILE if env_file is None else Path(env_file)
    try:
        if env_file is None and not path.exists():
            return None
        if not path.is_file():
            raise EnvironmentFileError("Файл окружения не найден или не является обычным файлом.")
        text = path.read_text(encoding="utf-8-sig")
    except EnvironmentFileError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise EnvironmentFileError("Не удалось прочитать файл окружения в UTF-8.") from None
    try:
        from dotenv import dotenv_values
        from dotenv.parser import parse_stream
    except ImportError:
        raise EnvironmentFileError("Для загрузки .env установите зависимости из requirements-backend.txt.") from None
    # python-dotenv's public loader skips malformed statements with a warning.
    # Its pinned parser lets startup reject them before loading partial secrets,
    # without ever sending the original statement to a logger or exception.
    for binding in parse_stream(io.StringIO(text)):
        if binding.error:
            raise EnvironmentFileError(
                f"Некорректный синтаксис файла окружения в строке {binding.original.line}."
            )
    # Parse the full file before changing any environment value. Quoting and
    # escapes follow python-dotenv, while ${...} and $... remain literal.
    values = dotenv_values(stream=io.StringIO(text), interpolate=False)
    for key, value in values.items():
        if not key or "=" in key or "\0" in key or (value is not None and "\0" in value):
            raise EnvironmentFileError("Файл окружения содержит недопустимое имя или значение переменной.")
    for key, value in values.items():
        if value is not None:
            target.setdefault(key, value)
    return path
