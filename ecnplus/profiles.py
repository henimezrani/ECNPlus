"""Local profile locations and one-time migration from the single-user app."""

import os
import shutil
import sys
from pathlib import Path

PROFILES = {
    'yasmine': 'Yasmine',
    'houssein': 'Houssein',
    'autre': 'Autre',
    'thabet': 'Thabet',
}


def default_profiles_root():
    override = os.environ.get('ECN_COMPANION_DATA_HOME')
    if override:
        return Path(override).expanduser().resolve() / 'profiles'
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'ECN Companion' / 'profiles'


def initialize_profiles(root, legacy_data, legacy_settings, legacy_credentials):
    """Create isolated profile folders and preserve old single-user files in Yasmine."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    legacy_data = Path(legacy_data)
    legacy_settings = Path(legacy_settings)
    legacy_credentials = Path(legacy_credentials)
    profiles = {}

    for slug, name in PROFILES.items():
        directory = root / slug
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        data_dir = directory / 'data'
        settings_path = directory / 'readings.json'
        credential_path = directory / '.env'

        if slug == 'yasmine' and (not data_dir.exists() or not any(data_dir.iterdir())) and legacy_data.is_dir():
            shutil.copytree(legacy_data, data_dir, dirs_exist_ok=True)
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        if not settings_path.exists() and legacy_settings.is_file():
            shutil.copy2(legacy_settings, settings_path)
        if slug == 'yasmine' and not credential_path.exists():
            old_credential = legacy_credentials / '.env'
            if old_credential.is_file():
                shutil.copy2(old_credential, credential_path)

        profiles[slug] = {
            'slug': slug,
            'name': name,
            'directory': directory,
            'data_dir': data_dir,
            'settings_path': settings_path,
            'credential_dir': directory,
        }
    return profiles
