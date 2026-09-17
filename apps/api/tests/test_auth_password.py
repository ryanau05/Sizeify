"""Tests for ``api.auth.password``.

Two layers:

* **Behavioural** (fast): correctness of ``hash`` / ``verify`` —
  uniqueness, success on the right password, failure on the wrong one,
  failure on a malformed hash without raising.
* **Benchmark** (``@pytest.mark.slow``): wall-clock assertion that
  ``verify`` lands under 500 ms on the dev machine, matching the
  TKT-P1-05 acceptance criterion. The 500 ms ceiling sits well above the
  ~250 ms target so a moderately slower CI runner still passes; if it
  fails, ``api.auth.password``'s calibration constants need to come down.
"""

from __future__ import annotations

import time

import anyio
import pytest

from api.auth import password
from api.auth import password as auth_password


def test_hash_is_unique_per_call() -> None:
    # Argon2 prepends a per-call random salt, so two hashes of the same
    # plaintext never collide.
    h1 = password.hash("correcthorse")
    h2 = password.hash("correcthorse")
    assert h1 != h2


def test_hash_returns_phc_format() -> None:
    # PHC string format begins with the algorithm identifier — protects
    # against accidental swap to a non-Argon2 hasher.
    h = password.hash("anything-goes")
    assert h.startswith("$argon2id$")


def test_verify_succeeds_for_correct_password() -> None:
    h = password.hash("hunter2hunter")
    assert password.verify("hunter2hunter", h) is True


def test_verify_fails_for_incorrect_password() -> None:
    h = password.hash("hunter2hunter")
    assert password.verify("hunter3hunter", h) is False


def test_verify_fails_on_malformed_hash() -> None:
    # Route handlers can pass arbitrary strings if a row was tampered with
    # or migrated incorrectly — we should never raise on bad input, just
    # return False so the route maps it to a generic 401.
    assert password.verify("anything", "not-a-real-hash") is False
    assert password.verify("anything", "") is False


def test_verify_fails_on_empty_password() -> None:
    h = password.hash("real-password-1")
    assert password.verify("", h) is False


@pytest.mark.slow
def test_verify_completes_under_500ms() -> None:
    # PRD §11 / CLAUDE.md backend rules: verify should land near 250 ms
    # on prod hardware. The 500 ms ceiling gives ~2x margin so a slow CI
    # runner passes; falling above it on dev hardware means the
    # calibration constants are too aggressive.
    pwd = "benchmark-pass-1"
    h = password.hash(pwd)

    # Warm-up call — the first verify after process start can include
    # one-time CFFI bindings setup we don't want in the measurement.
    password.verify(pwd, h)

    start = time.perf_counter()
    result = password.verify(pwd, h)
    elapsed_s = time.perf_counter() - start

    assert result is True
    assert elapsed_s < 0.5, (
        f"verify took {elapsed_s * 1000:.0f} ms (>500 ms ceiling) — "
        "lower MEMORY_COST_KIB / TIME_COST in api/auth/password.py"
    )


def test_needs_rehash_is_false_for_a_current_hash() -> None:
    assert password.needs_rehash(password.hash("current-params-1")) is False


def test_needs_rehash_is_true_for_weaker_parameters() -> None:
    """The upgrade path that makes re-calibrating ``MEMORY_COST_KIB`` mean
    anything for accounts that already exist."""
    from argon2 import PasswordHasher, Type

    weaker = PasswordHasher(
        time_cost=1,
        memory_cost=8,
        parallelism=1,
        hash_len=password.HASH_LEN_BYTES,
        salt_len=password.SALT_LEN_BYTES,
        type=Type.ID,
    ).hash("old-params-1")

    assert password.needs_rehash(weaker) is True
    # And the old hash still verifies, so the user is not locked out meanwhile.
    assert password.verify("old-params-1", weaker) is True


def test_needs_rehash_is_false_for_an_unparseable_hash() -> None:
    """Migration 0003's locked sentinel among them: re-hashing something that
    never verified would be meaningless."""
    assert password.needs_rehash("!locked-no-password-set") is False


async def test_concurrent_hashing_is_bounded() -> None:
    """Argon2 concurrency is a memory question, not a CPU one.

    Each operation holds ``MEMORY_COST_KIB`` (128 MiB) for its duration, and
    anyio's default thread pool is 40 wide — so unbounded hashing peaks around
    5 GiB resident. One account can reach that alone: ``DELETE /me`` verifies a
    password and sits behind a 120/minute budget, so a single valid token
    saturates the pool and OOM-kills a small container.

    Asserts the ceiling actually binds, rather than that the limiter object
    exists.
    """
    in_flight = 0
    peak = 0
    real_verify = auth_password.verify

    def counting_verify(password: str, hash_: str) -> bool:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            return real_verify(password, hash_)
        finally:
            in_flight -= 1

    hashed = auth_password.hash("concurrency-probe")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(auth_password, "verify", counting_verify)
        async with anyio.create_task_group() as tg:
            for _ in range(auth_password.MAX_CONCURRENT_HASHES * 3):
                tg.start_soon(auth_password.verify_async, "concurrency-probe", hashed)

    assert peak > 1, "nothing ran concurrently — the test is not exercising the bound"
    assert peak <= auth_password.MAX_CONCURRENT_HASHES, (
        f"{peak} argon2 operations ran at once, "
        f"~{peak * auth_password.MEMORY_COST_KIB // 1024} MiB resident"
    )
