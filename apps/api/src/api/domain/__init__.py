"""Pure domain logic. No I/O. Heavily unit-tested.

If a module here imports ``httpx``, ``sqlalchemy``, ``redis``, or anything
else that talks to the outside world, it has drifted into ``services/`` and
should move. The matching engine, fit profile builder, and stretch-coefficient
logic all live behind this fence so they are testable in isolation.
"""
