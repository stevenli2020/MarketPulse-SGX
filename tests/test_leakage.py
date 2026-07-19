"""
Leakage-prevention tests (PROJECT_SPEC.md Section 8).

NOT YET IMPLEMENTED - skipped until features/feature_engineering.py and
labeling/labels.py have real logic to test against. Left in place now as
a placeholder so the test file - and the checks it is expected to contain
- are decided upfront, before the code they will guard exists.

Once implemented, this file should assert things such as:
  - no feature's availability date exceeds its cutoff (as_of) date
  - every label's label_computable_date equals observation_date plus the
    correct number of trading days
  - train/test date ranges used in modeling never overlap or invert
"""

import pytest


@pytest.mark.skip(reason="feature_engineering.py is not implemented yet")
def test_features_never_use_future_data():
    pass


@pytest.mark.skip(reason="labels.py is not implemented yet")
def test_label_computable_date_is_correct():
    pass
