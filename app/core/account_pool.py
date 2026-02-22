"""Account pool for multi-cookie parallel image generation."""
import asyncio
import random
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

from app.core.browser import CookieManager
from app.core.generator import ImageGenerator


@dataclass
class AccountLease:
    """Leased account runtime context."""

    account_id: str
    generator: ImageGenerator


@dataclass
class AccountState:
    """Runtime state for one cookies account."""

    account_id: str
    cookies_path: Path
    cookie_manager: CookieManager
    generator: ImageGenerator
    semaphore: asyncio.Semaphore
    active_tasks: int = 0
    cooldown_until: float | None = None
    last_error: str | None = None
    enabled: bool = True

    def in_cooldown(self) -> bool:
        return bool(self.cooldown_until and self.cooldown_until > time.time())


class AccountPool:
    """Manages cookie accounts and account-level concurrency."""

    def __init__(
        self,
        account_sources: list[tuple[str, Path]],
        proxy: str | None = None,
        per_account_concurrent: int = 1,
    ):
        if not account_sources:
            raise ValueError("At least one account source is required")

        if per_account_concurrent < 1:
            raise ValueError("per_account_concurrent must be >= 1")

        self._proxy = proxy
        self._per_account_concurrent = per_account_concurrent
        self._accounts: dict[str, AccountState] = {}
        for account_id, cookies_path in account_sources:
            self._accounts[account_id] = self._build_account_state(account_id, cookies_path)

    def _build_account_state(self, account_id: str, cookies_path: Path) -> AccountState:
        cookie_manager = CookieManager(
            cookies_path,
            prefer_configured_path=True,
        )
        generator = ImageGenerator(cookie_manager, self._proxy)
        return AccountState(
            account_id=account_id,
            cookies_path=cookies_path,
            cookie_manager=cookie_manager,
            generator=generator,
            semaphore=asyncio.Semaphore(self._per_account_concurrent),
        )

    async def acquire(self) -> AccountLease:
        """Acquire one available account, randomly selected from least-active accounts."""
        now = time.time()

        candidates = [
            acc
            for acc in self._accounts.values()
            if acc.enabled
            and (acc.cooldown_until is None or acc.cooldown_until <= now)
            and not acc.semaphore.locked()
        ]

        if not candidates:
            if any(acc.in_cooldown() for acc in self._accounts.values() if acc.enabled):
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": {
                            "message": "All cookie accounts are temporarily unavailable",
                            "type": "service_error",
                            "code": "accounts_unavailable",
                        }
                    },
                )

            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "message": "All cookie accounts are busy",
                        "type": "server_error",
                        "code": "accounts_busy",
                    }
                },
            )

        # Find minimum active tasks count
        min_tasks = min(acc.active_tasks for acc in candidates)
        # Get all accounts with minimum active tasks
        least_active = [acc for acc in candidates if acc.active_tasks == min_tasks]
        # Randomly select one from least active accounts
        selected = random.choice(least_active)

        await selected.semaphore.acquire()
        selected.active_tasks += 1

        return AccountLease(
            account_id=selected.account_id,
            generator=selected.generator,
        )

    def release(self, lease: AccountLease):
        """Release a previously acquired account lease."""
        account = self._accounts.get(lease.account_id)
        if not account:
            return

        account.semaphore.release()
        account.active_tasks = max(0, account.active_tasks - 1)

    def mark_cooldown(self, account_id: str, seconds: int, reason: str | None = None):
        """Put account into cooldown window."""
        account = self._accounts.get(account_id)
        if not account:
            return

        account.cooldown_until = time.time() + max(1, seconds)
        account.last_error = reason

    def clear_cooldown(self, account_id: str):
        """Clear cooldown for account."""
        account = self._accounts.get(account_id)
        if not account:
            return

        account.cooldown_until = None
        account.last_error = None

    def get_cookie_manager(self, account_id: str) -> CookieManager | None:
        """Return cookie manager for account id."""
        account = self._accounts.get(account_id)
        return account.cookie_manager if account else None

    def has_account(self, account_id: str) -> bool:
        """Check account existence."""
        return account_id in self._accounts

    def add_or_update_account(self, account_id: str, cookies_path: Path) -> CookieManager:
        """Create or replace account runtime with specified cookies path."""
        state = self._build_account_state(account_id, cookies_path)
        self._accounts[account_id] = state
        return state.cookie_manager

    def stats(self) -> dict:
        """Get account status summary for health endpoint."""
        now = time.time()
        account_items = []
        available_count = 0

        for account in sorted(self._accounts.values(), key=lambda item: item.account_id):
            in_cooldown = bool(account.cooldown_until and account.cooldown_until > now)
            busy = account.semaphore.locked()
            available = account.enabled and not in_cooldown and not busy
            if available:
                available_count += 1

            identity = account.cookie_manager.get_account_identity()

            account_items.append(
                {
                    "account_id": account.account_id,
                    "email": identity.get("email") or None,
                    "identity_label": identity.get("label", "unknown"),
                    "identity_kind": identity.get("kind", "unknown"),
                    "enabled": account.enabled,
                    "active_tasks": account.active_tasks,
                    "in_cooldown": in_cooldown,
                    "cooldown_remaining": int(account.cooldown_until - now) if in_cooldown else 0,
                    "last_error": account.last_error,
                }
            )

        return {
            "accounts_total": len(self._accounts),
            "accounts_available": available_count,
            "accounts": account_items,
        }
