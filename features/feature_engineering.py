"""
Point-in-time-safe feature computation (PROJECT_SPEC.md Section 5,
categories A-E: price/trend, volatility, volume, relative-to-STI,
interest-rate/macro).

NOT YET IMPLEMENTED. This stub exists to fix the core safety contract
before any feature is written:

    compute_features(security_id, as_of) must only use raw data with an
    availability date <= as_of. There must be no code path in this module
    that can read data "from the future" relative to as_of.

This module must not import anything from labeling/ - features and
labels are kept structurally separate so a feature can never accidentally
have access to its own label (PROJECT_SPEC.md Section 8.4).
"""


def compute_features(security_id: int, as_of) -> None:
    """
    Compute all v1 features for one security as of a given cutoff date.
    Not yet implemented.
    """
    raise NotImplementedError("Feature engineering is not implemented yet.")
