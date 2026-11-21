"""Shopping domain primitives and services.

The local basket is intentionally separate from a provider cart. A provider
adapter may price or submit a basket only after its schema and authorisation
flow have been verified. Checkout orchestration is one-shot, approval-bound,
and provider-specific; generic basket APIs cannot place orders.
"""
