from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_env_file(path: str | os.PathLike[str]) -> Path | None:
    env_path = Path(path)
    if not env_path.is_file():
        return None

    load_dotenv(dotenv_path=env_path, override=False, interpolate=False)
    return env_path
