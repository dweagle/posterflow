import threading
import time

from slowapi import Limiter
from slowapi.util import get_remote_address

# Inbound: shared rate-limiter instance.  Registered on the FastAPI app in main.py.
# Import this in any router that needs @limiter.limit() decorators.
limiter = Limiter(key_func=get_remote_address, default_limits=[])


class TokenBucket:
    """Thread-safe rate limiter governing request *starts*, so N callers share one ceiling."""

    def __init__(self, rate_per_second: float, burst: int) -> None:
        self._rate = max(0.1, float(rate_per_second))
        self._capacity = float(max(1, burst))
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                # Refill for elapsed time, then take a token if one is available.
                self._tokens = min(self._capacity, self._tokens + (now - self._updated_at) * self._rate)
                self._updated_at = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)  # sleep outside the lock so other threads can still refill/take


# Outbound: TMDB limits by IP (~40-50 req/s), not by API key, so every TMDB caller in this
# process shares one bucket.  Well under the ceiling so we're never the reason a run gets 429'd.
# Covers api.themoviedb.org only — image.tmdb.org is a CDN and isn't limited the same way.
TMDB_REQUESTS_PER_SECOND = 20.0
TMDB_BURST = 10
tmdb_bucket = TokenBucket(TMDB_REQUESTS_PER_SECOND, TMDB_BURST)
