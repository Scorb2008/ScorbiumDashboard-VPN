from pathlib import Path
from .log import log


def get_env():
    start_path = Path(__file__).resolve().parent

    current_path = start_path

    while current_path != current_path.parent:
        env_path = current_path / ".env"

        if env_path.exists():
            print("✅ Found .env file")
            return env_path

        current_path = current_path.parent

    return None


env_file = None
try:
    env_file = get_env()
except Exception as e:
    log.error(e)
