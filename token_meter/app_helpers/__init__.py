"""Leaf helpers extracted from ``token_meter.app``.

Each module holds code that depends only on the standard library, other
Token Meter packages, and ``app_helpers.common``. ``token_meter.app`` imports
every name back, so ``meter.NAME`` keeps working. Code that tests patch on
``meter`` stays in ``app.py`` until those tests patch the owning module.
"""
