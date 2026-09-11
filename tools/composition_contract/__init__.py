"""The `dimensional-composition` contract — relocated out of `tests/architecture/`
(which is a test collection, not a library) so it can be consumed by another
repository's CI without vendoring. See `composition_schema.py` for the
contract itself; this repository's own architecture tests
(`tests/architecture/test_composition_schema.py`) import it from here, on
the same footing any future consumer would.
"""
