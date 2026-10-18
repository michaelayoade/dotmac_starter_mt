"""Catalog fact shape characterizations; no SQL/runtime mocks."""

from dataclasses import FrozenInstanceError

import pytest
from dotmac_learning.catalog_contracts import CatalogPart, CourseStructure


def test_catalog_collections_are_immutable_and_no_target_for_empty_structure():
    part = CatalogPart("", ())
    structure = CourseStructure((part,), (), None, False)
    assert structure.parts == (part,)
    assert structure.parts[0].chapters == ()
    assert structure.continue_target is None
    with pytest.raises(FrozenInstanceError):
        structure.locked = True
