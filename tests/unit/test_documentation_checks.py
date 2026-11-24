"""Hosted sensitivity canaries for documentation checks, without network calls."""

from __future__ import annotations

import unittest

from scripts.check_documentation import FEATURE_MARKER, FEATURE_SECTIONS, check_page


class DocumentationChecks(unittest.TestCase):
    def test_valid_local_links_and_anchors(self) -> None:
        pages = {
            "docs/a.md": "[Go](b.md#usage)\n![Image](../static/a.png)",
            "docs/b.md": "# Usage\n",
        }
        tracked = frozenset((*pages, "static/a.png"))
        self.assertEqual(
            check_page(
                path="docs/a.md",
                source=pages["docs/a.md"],
                tracked=tracked,
                read=pages.__getitem__,
            ),
            (),
        )

    def test_missing_path_and_fragment_are_rejected(self) -> None:
        for destination in ("missing.md", "b.md#missing"):
            with self.subTest(destination=destination):
                result = check_page(
                    path="docs/a.md",
                    source=f"[Go]({destination})",
                    tracked=frozenset({"docs/b.md"}),
                    read=lambda _: "# Usage",
                )
                self.assertTrue(result)

    def test_code_samples_and_external_urls_are_not_crawled(self) -> None:
        source = (
            "```markdown\n[Example](missing.md)\n```\n[Web](https://example.test/a)"
        )
        self.assertEqual(
            check_page(
                path="README.md", source=source, tracked=frozenset(), read=lambda _: ""
            ),
            (),
        )

    def test_removed_targets_are_checked_in_unchanged_pages(self) -> None:
        result = check_page(
            path="docs/a.md",
            source="[Go](old.md) [Old debt](other.md)",
            tracked=frozenset(),
            read=lambda _: "",
            deleted_only=frozenset({"docs/old.md"}),
        )
        self.assertEqual(len(result), 1)
        self.assertIn("old.md", result[0].reason)

    def test_feature_sections_must_be_completed(self) -> None:
        source = (
            FEATURE_MARKER
            + "\n"
            + "\n".join(f"## {h}\nDocumented." for h in FEATURE_SECTIONS)
        )
        self.assertEqual(
            check_page(
                path="docs/guides/feature.md",
                source=source,
                tracked=frozenset(),
                read=lambda _: "",
            ),
            (),
        )
        result = check_page(
            path="docs/guides/feature.md",
            source=source.replace("Documented.", "TODO: Write this", 1),
            tracked=frozenset(),
            read=lambda _: "",
        )
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
