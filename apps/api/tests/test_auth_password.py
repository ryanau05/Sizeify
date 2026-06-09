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

import pytest

from api.auth import password


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
