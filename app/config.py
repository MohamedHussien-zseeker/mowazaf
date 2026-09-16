# Re-export settings from the canonical config module at project root.
# This file exists because app/ uses relative imports from .config.
from config import settings  # noqa: F401, E402
