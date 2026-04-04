from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared rate-limiter instance.  Registered on the FastAPI app in main.py.
# Import this in any router that needs @limiter.limit() decorators.
limiter = Limiter(key_func=get_remote_address, default_limits=[])
