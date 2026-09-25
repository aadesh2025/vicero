"""Plan metering and gates (docs/18, ADR-088). Pure limits live in `app.core.plans`; this package
is the part that touches the database, so it sits beside the features that call it rather than in
`core`, which must not import models (tests/test_architecture.py).
"""
