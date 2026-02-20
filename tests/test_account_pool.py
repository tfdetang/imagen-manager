"""Tests for multi-account cookie pool scheduling."""
import pytest
from fastapi import HTTPException

from app.core.account_pool import AccountPool


@pytest.mark.asyncio
async def test_account_pool_uses_different_accounts_when_busy(tmp_path):
    """Should schedule to another account when current one is busy."""
    account_a = tmp_path / "a.json"
    account_b = tmp_path / "b.json"
    account_a.write_text("[]")
    account_b.write_text("[]")

    pool = AccountPool(
        [("a", account_a), ("b", account_b)],
        per_account_concurrent=1,
    )

    lease1 = await pool.acquire()
    lease2 = await pool.acquire()

    assert lease1.account_id != lease2.account_id

    pool.release(lease1)
    pool.release(lease2)


@pytest.mark.asyncio
async def test_account_pool_skips_cooldown_accounts(tmp_path):
    """Should skip accounts in cooldown and pick healthy one."""
    account_a = tmp_path / "a.json"
    account_b = tmp_path / "b.json"
    account_a.write_text("[]")
    account_b.write_text("[]")

    pool = AccountPool(
        [("a", account_a), ("b", account_b)],
        per_account_concurrent=1,
    )
    pool.mark_cooldown("a", seconds=120, reason="cookies_expired")

    lease = await pool.acquire()
    assert lease.account_id == "b"

    pool.release(lease)


@pytest.mark.asyncio
async def test_account_pool_returns_503_when_all_cooldown(tmp_path):
    """Should return service unavailable if all accounts are cooling down."""
    account_a = tmp_path / "a.json"
    account_b = tmp_path / "b.json"
    account_a.write_text("[]")
    account_b.write_text("[]")

    pool = AccountPool(
        [("a", account_a), ("b", account_b)],
        per_account_concurrent=1,
    )
    pool.mark_cooldown("a", seconds=120, reason="cookies_expired")
    pool.mark_cooldown("b", seconds=120, reason="cookies_expired")

    with pytest.raises(HTTPException) as exc:
        await pool.acquire()

    assert exc.value.status_code == 503
    assert exc.value.detail["error"]["code"] == "accounts_unavailable"


def test_account_pool_add_or_update_account(tmp_path):
    """Should support adding new account after pool initialization."""
    account_a = tmp_path / "a.json"
    account_a.write_text("[]")

    pool = AccountPool(
        [("a", account_a)],
        per_account_concurrent=1,
    )

    account_b = tmp_path / "b.json"
    manager = pool.add_or_update_account("b", account_b)
    manager.save_cookies([])

    assert pool.has_account("b")
