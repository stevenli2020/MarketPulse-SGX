"""
Forward-looking label computation (direction_5d, direction_10d).

NOT YET IMPLEMENTED. This stub exists to fix the target table (labels)
and the intended definition before any label is computed:

    direction_Nd = 1 if close(t + N trading days) > close(t) else 0
    label_computable_date = observation_date + N trading days

This module must not import anything from features/ - labels and features
are kept structurally separate (PROJECT_SPEC.md Section 8.4), so labels can
be computed and audited independently of whatever features exist at the
time.
"""


def compute_labels(security_id: int, horizon_days: int) -> None:
    """Compute forward-looking labels for one security/horizon. Not yet implemented."""
    raise NotImplementedError("Label computation is not implemented yet.")
