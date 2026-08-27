"""
Shared Limiter instance. Lives here, not in main.py, specifically so route
modules (like auth.py) can import it to decorate individual endpoints with
stricter limits — importing directly from main.py would create a circular
import, since main.py imports every router module.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
