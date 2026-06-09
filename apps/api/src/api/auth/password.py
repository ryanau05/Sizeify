"""Argon2id password hashing — PRD §11 / CLAUDE.md backend rules.

Two functions, ``hash`` and ``verify``, both stateless from the caller's
point of view. A module-level ``PasswordHasher`` holds the calibrated
parameters; instantiating one per call would add measurable overhead
(the hasher caches its random-generator handle).

Calibration target
------------------
~250 ms per ``verify`` on prod-equivalent hardware. This is the same
ballpark Auth0 / OWASP recommend for interactive login — slow enough to
ruin offline brute-force economics, fast enough that legitimate users
don't notice the delay.

The argon2-cffi defaults (``t=3``, ``m=64 MiB``, ``p=4``) are roughly
right but tuned for low-spec hardware; we bump ``memory_cost`` to
128 MiB so prod CPUs land closer to the 250 ms target. Empirically on
this repo's dev hardware (Apple Silicon) verify takes ~75 ms; a 2-3×
slower prod CPU produces the intended ~150-250 ms range.

When the deployment hardware is known, re-measure with::

    python -c "import time; from argon2 import PasswordHasher; \
      ph = PasswordHasher(time_cost=3, memory_cost=131072, parallelism=4); \
      h = ph.hash('x'); start = time.perf_counter(); ph.verify(h, 'x'); \
      print(f'{(time.perf_counter()-start)*1000:.0f} ms')"

…and adjust ``MEMORY_COST_KIB`` so the result lands near 250 ms. The
benchmark test in ``tests/test_auth_password.py`` enforces a 500 ms
upper bound so a misconfigured production parameter set fails CI before
shipping.

Type
----
Argon2id (``Type.ID``) — the hybrid variant resistant to both side-
channel and time-memory tradeoff attacks. Argon2-cffi's default is
already Argon2id; we set it explicitly so a future library default
change can't silently downgrade us.

API shadowing
-------------
The functions are named ``hash`` and ``verify`` per the ticket — they
shadow the built-in ``hash`` and ``verify`` is not built-in. Importers
should prefer the qualified form (``from api.auth import password;
password.hash(...)``) over ``from api.auth.password import hash`` to
avoid local-scope confusion.
"""

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

# ---------------------------------------------------------------------------
# Calibrated parameters. Re-measure on prod hardware before deploying.
# ---------------------------------------------------------------------------

# Iterations. Each iteration linearly increases compute cost; ``3`` is the
# argon2-cffi default and OWASP's lower-bound recommendation for Argon2id.
TIME_COST = 3

# Memory cost in KiB. 131072 KiB = 128 MiB. Argon2's main GPU/ASIC
# resistance comes from this — attackers parallelize compute much more
# cheaply than memory.
MEMORY_COST_KIB = 131072

# Parallel lanes. Set to a typical small-server core count; argon2-cffi
# default. Higher values waste compute on single-core hosts.
PARALLELISM = 4

# Hash and salt sizes, in bytes. 32-byte hash is the argon2-cffi default
# and matches the underlying primitive's natural output. 16-byte salt is
# the RFC-recommended minimum.
HASH_LEN_BYTES = 32
SALT_LEN_BYTES = 16


# Module-level singleton — reused across calls so the underlying
# random-number generator stays warm and the parameter dict isn't rebuilt
# on every hash.
_hasher = PasswordHasher(
    time_cost=TIME_COST,
    memory_cost=MEMORY_COST_KIB,
    parallelism=PARALLELISM,
    hash_len=HASH_LEN_BYTES,
    salt_len=SALT_LEN_BYTES,
    type=Type.ID,
)


def hash(password: str) -> str:
    """Hash ``password`` with a fresh random salt.

    Returns an Argon2 PHC-format string (algorithm + parameters + salt +
    hash, all in one). Callers persist this opaque string to
    ``user.password_hash``; nothing else needs to know its internal
    structure — ``verify`` parses it back out.

    Two calls with the same password produce different hashes because
    the salt is random per call.
    """
    return _hasher.hash(password)


def verify(password: str, hash: str) -> bool:
    """Return ``True`` iff ``password`` produced ``hash``.

    Returns ``False`` (does not raise) when:

    * The password doesn't match (``VerifyMismatchError``).
    * The hash is malformed or not an Argon2 hash at all
      (``InvalidHashError``).
    * The underlying primitive fails verification for any other reason
      (``VerificationError``).

    Swallowing exceptions in favor of a bool keeps the call site clean
    at the auth route — the route maps ``False`` to a generic 401 with
    no info leak about which failure mode hit.

    Note: argon2-cffi's ``PasswordHasher.verify`` takes ``(hash,
    password)``; the argument order here is reversed to match the
    ticket's signature.
    """
    try:
        _hasher.verify(hash, password)
    except (VerificationError, InvalidHashError):
        return False
    return True
