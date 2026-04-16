from __future__ import annotations

import os
from pathlib import Path


def _unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        quote = text[0]
        inner = text[1:-1]
        if quote == '"':
            return bytes(inner, 'utf-8').decode('unicode_escape')
        return inner
    return text


def load_project_env(*, override: bool = False, filename: str = '.env') -> Path | None:
    root = Path(__file__).resolve().parent.parent
    path = root / filename
    if not path.is_file():
        return None

    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _unquote(value)
    return path
