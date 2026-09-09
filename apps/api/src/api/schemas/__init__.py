"""Pydantic v2 request/response models.

Route handlers must use these — CLAUDE.md backend rule: "No raw dicts in
routes. All request and response bodies are Pydantic models in
``schemas/``." Repository or domain layers are free to use plain
dataclasses / TypedDicts internally; the schemas are the wire contract.

Submodules track the URL surface they serve:

* ``auth`` — ``/auth/{signup,login,refresh}``
* ``closet`` — ``/closet/garments`` and ``/closet/garments/{id}/fit-signals``
* ``fit_profile`` — ``/closet/fit-profile``
* ``me`` — ``/me/export`` (GDPR/CCPA)

Domain enums (verdict, source, stretch, …) live in ``schemas.enums`` so
multiple submodules can reuse them without circular imports.
"""
