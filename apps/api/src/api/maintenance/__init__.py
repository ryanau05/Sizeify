"""Operational tasks that run on a schedule rather than on a request.

Each module here exposes an async function doing the work and a ``main()``
that can be invoked as ``python -m api.maintenance.<name>``, so a task can
be driven by cron, a Kubernetes CronJob, or a one-off shell before there is
a job queue to own it.

Phase 8 introduces ``arq`` on Redis (CLAUDE.md, "Background tasks"). These
are written so that step is a matter of calling the same function from a
worker — the logic does not live in the entry point.
"""
