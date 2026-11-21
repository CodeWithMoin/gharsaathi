"""Shopping domain primitives and services.

The local basket is intentionally separate from a provider cart.  A provider
adapter may price a basket only after its schema and authorisation flow have
been verified; no checkout capability lives in this package.
"""
