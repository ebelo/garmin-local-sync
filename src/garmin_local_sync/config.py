"""Stable defaults. No credentials or user-specific paths belong in source."""

import datetime as dt
from pathlib import Path

BASE = "https://personal.connectapi.garmin.com"
# Public API route, not a credential.
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"  # nosec B105
DEFAULT_ROOT = Path.home() / ".local/share/garmin-local-sync"
DEFAULT_KEY = Path.home() / ".config/garmin/connect-personal-api-key"
UTC = dt.UTC
SCHEMA_VERSION = 3
