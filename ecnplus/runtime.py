"""Read local deployment settings without replacing platform environment values."""

import os
import json
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def env_values(path):
    values = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if separator and key and not key.startswith('#'):
                if value.startswith('"'):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        value = value.strip('"')
                elif len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
                values[key] = value
    return values


def load_env(path=None):
    # Render supplies secrets directly; never load a checkout's .env there.
    if not os.environ.get('RENDER'):
        for key, value in env_values(path or PROJECT / '.env').items():
            os.environ.setdefault(key, value)


def update_env(values, path=None):
    path = Path(path or PROJECT / '.env')
    before = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    lines = [line for line in before if line.partition('=')[0].strip() not in values]
    lines.extend(key + '=' + json.dumps(value, ensure_ascii=False) for key, value in values.items())
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.chmod(temporary, 0o600)
            stream.write('\n'.join(lines) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)

