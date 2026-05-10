"""
Job queue manager for limiting concurrent sync operations.
Uses a thread pool executor to manage concurrent jobs.
"""
import gc
import ctypes
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional
from core.logging import log_debug
from core.config import settings


def _trim_process_memory() -> None:
    """Run GC and call malloc_trim(0) to return freed heap pages to the OS after large jobs."""
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

class JobQueueManager:
    """Manages concurrent job execution with a thread pool."""
    
    def __init__(self, max_concurrent: Optional[int] = None) -> None:
        if max_concurrent is None:
            max_concurrent = settings.max_concurrent_jobs
        self.max_concurrent = max_concurrent
        self.executor = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="job_")
        self.lock = threading.Lock()
        self.active_jobs = 0
        self.pending_jobs = 0
        self._is_shutdown = False

    def _create_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=self.max_concurrent, thread_name_prefix="job_")

    def _ensure_executor_ready(self) -> None:
        if not self._is_shutdown:
            return
        self.executor = self._create_executor()
        self._is_shutdown = False
        self.active_jobs = 0
        self.pending_jobs = 0
        
    def submit(self, job_func: Callable[..., Any], job_id: int, *args: Any, **kwargs: Any) -> Future[Any]:
        """Submit a job to the queue."""
        def wrapped_job() -> Any:
            """Wrapper that tracks active job count."""
            with self.lock:
                self.pending_jobs -= 1
                self.active_jobs += 1
            log_debug("QUEUE", f"Job {job_id} started ({self.active_jobs}/{self.max_concurrent} slots active)")
            
            try:
                return job_func(*args, **kwargs)
            finally:
                with self.lock:
                    self.active_jobs -= 1
                log_debug("QUEUE", f"Job {job_id} completed ({self.active_jobs}/{self.max_concurrent} slots active)")
                _trim_process_memory()

        # Submit inside lock to prevent race with shutdown()
        with self.lock:
            self._ensure_executor_ready()
            self.pending_jobs += 1
            queue_position = self.pending_jobs
            if queue_position > 1:
                log_debug("QUEUE", f"Job {job_id} waiting (position {queue_position} in queue, {self.active_jobs}/{self.max_concurrent} slots active)")
            future = self.executor.submit(wrapped_job)

        return future
        
    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        """Shutdown the executor."""
        with self.lock:
            if self._is_shutdown:
                return
            self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)
            self._is_shutdown = True

# Global job queue manager
job_queue = JobQueueManager()
