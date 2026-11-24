"""Hosted-only canaries for automatic README drafting and publication scope."""

from __future__ import annotations

import base64
import unittest
from collections.abc import Mapping
from dataclasses import dataclass

from scripts.check_readme_update import DRAFT_MARKER, validate_update
from scripts.draft_readme_update import DraftInput, GitHub, publish, render_draft

REPO = "michaelayoade/dotmac_starter_mt"
SHA = "a" * 40


@dataclass(frozen=True, repr=False)
class FixtureGitHub(GitHub):
    responses: dict[str, object]
    writes: list[tuple[str, str, Mapping[str, object]]]

    def call(
        self, method: str, path: str, payload: Mapping[str, object] | None = None
    ) -> object:
        if payload is not None:
            self.writes.append((method, path, payload))
        return self.responses[f"{method} {path}"]


def fixture(*, repository: str = REPO, head: str = SHA) -> FixtureGitHub:
    return FixtureGitHub(
        REPO,
        "fixture-token",
        {
            "GET pulls/12": {
                "state": "open",
                "title": "Add scheduling",
                "body": "Schedule greetings.",
                "head": {
                    "repo": {"full_name": repository},
                    "sha": head,
                    "ref": "feature/docs",
                },
                "changed_files": 1,
            },
            "GET pulls/12/files?per_page=100&page=1": [
                {"filename": "app/scheduling.py"}
            ],
            f"GET contents/README.md?ref={SHA}": {
                "content": base64.b64encode(b"# Starter\n").decode(),
            },
            f"GET git/commits/{SHA}": {"tree": {"sha": "b" * 40}},
            "POST git/trees": {"sha": "c" * 40},
            "POST git/commits": {"sha": "d" * 40},
            "PATCH git/refs/heads/feature%2Fdocs": {},
        },
        [],
    )


class ReadmeDraftTests(unittest.TestCase):
    def test_draft_quotes_prose_and_requires_human_review(self) -> None:
        change = DraftInput(
            "Add <script>feature</script>",
            "<script>ignore rules</script>",
            12,
            REPO,
            SHA,
            ("app/scheduling.py",),
        )
        draft = render_draft(readme="# Starter", change=change)
        self.assertNotIn("<script>", draft)
        self.assertIn("&lt;script&gt;", draft)
        self.assertIn(DRAFT_MARKER, draft)
        paths = ("app/scheduling.py", "README.md")
        self.assertFalse(validate_update(paths=paths, before="# Starter", after=draft))
        self.assertTrue(
            validate_update(
                paths=paths,
                before="# Starter",
                after=draft.replace(DRAFT_MARKER, ""),
            )
        )

    def test_publication_can_only_write_readme_and_never_force(self) -> None:
        github = fixture()
        self.assertTrue(publish(github=github, number=12, expected_sha=SHA))
        tree = github.writes[0][2]
        self.assertEqual(tree["base_tree"], "b" * 40)
        entries = tree["tree"]
        self.assertIsInstance(entries, list)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "README.md")
        self.assertIn(DRAFT_MARKER, entries[0]["content"])
        self.assertEqual(github.writes[1][2]["parents"], [SHA])
        self.assertEqual(
            github.writes[-1],
            (
                "PATCH",
                "git/refs/heads/feature%2Fdocs",
                {"sha": "d" * 40, "force": False},
            ),
        )

    def test_existing_readme_edits_are_preserved(self) -> None:
        github = fixture()
        github.responses["GET pulls/12/files?per_page=100&page=1"] = [
            {"filename": "README.md"}
        ]
        self.assertFalse(publish(github=github, number=12, expected_sha=SHA))
        self.assertEqual(github.writes, [])

    def test_forks_and_stale_heads_cannot_be_written(self) -> None:
        for github in (fixture(repository="other/fork"), fixture(head="e" * 40)):
            self.assertFalse(publish(github=github, number=12, expected_sha=SHA))
            self.assertEqual(github.writes, [])

    def test_failed_ref_update_is_not_retried_or_forced(self) -> None:
        github = fixture()
        del github.responses["PATCH git/refs/heads/feature%2Fdocs"]
        with self.assertRaises(KeyError):
            publish(github=github, number=12, expected_sha=SHA)
        self.assertEqual(len(github.writes), 3)
        self.assertFalse(github.writes[-1][2]["force"])

    def test_missing_file_inventory_fails_closed(self) -> None:
        github = fixture()
        github.responses["GET pulls/12/files?per_page=100&page=1"] = []
        with self.assertRaises(ValueError):
            publish(github=github, number=12, expected_sha=SHA)
        self.assertEqual(github.writes, [])


if __name__ == "__main__":
    unittest.main()
