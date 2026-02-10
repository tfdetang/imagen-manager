"""Concurrency control using semaphore."""
import asyncio
from fastapi import HTTPException


class ConcurrencyManager:
    """Manages concurrent task execution with semaphore."""

    def __init__(self, max_concurrent: int = 5):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks = 0
        self._max_concurrent = max_concurrent

    async def acquire(self):
        """Acquire semaphore or raise 429 if at capacity."""
        if self._semaphore.locked():
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "message": "Too many concurrent requests",
                        "type": "server_error",
                        "code": "rate_limit_exceeded",
                    }
                },
            )

        await self._semaphore.acquire()
        self._active_tasks += 1

    def release(self):
        """Release semaphore."""
        self._semaphore.release()
        self._active_tasks -= 1

    @property
    def active_tasks(self) -> int:
        """Get current number of active tasks."""
        return self._active_tasks

    @property
    def max_concurrent(self) -> int:
        """Get maximum concurrent tasks."""
        return self._max_concurrent
