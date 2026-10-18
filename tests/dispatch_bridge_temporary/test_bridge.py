"""Offline hosted canaries; tests are authored before the bridge implementation."""

import copy
import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "bridge", ROOT / "scripts/module_dispatch_bridge_temporary.py"
)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)
SHA = "a" * 40
ENV = {
    "GITHUB_REPOSITORY": bridge.REPO,
    "GITHUB_EVENT_NAME": "push",
    "GITHUB_REF": "refs/heads/chore/module-release-dispatch-test",
    "GITHUB_RUN_ATTEMPT": "1",
}


class FakeAPI:
    def __init__(self):
        self.posts = []
        self.branch = {"protected": True, "commit": {"sha": SHA}}
        self.runs = [
            {
                "id": i,
                "name": name,
                "path": path,
                "workflow_id": bridge.WORKFLOW_IDS[path],
                "head_sha": SHA,
                "event": "push",
                "head_branch": "main",
                "status": "completed",
                "conclusion": "success",
                "run_attempt": 1,
                "check_suite_id": i,
            }
            for i, (path, name) in enumerate(bridge.CANONICAL.items(), 1)
        ]
        self.checks = [
            {
                "id": i,
                "name": f"check-{i}",
                "status": "completed",
                "conclusion": "success",
                "check_suite": {"id": i},
                "app": {"slug": "github-actions"},
            }
            for i in range(1, 5)
        ]
        self.statuses = []
        self.prior = []
        self.tag = None
        self.pages = {}

    def get(self, route, *, optional=False):
        if route == "/branches/main":
            return copy.deepcopy(self.branch)
        if route.startswith("/git/ref/tags/"):
            return self.tag
        if route.startswith("/contents/"):
            import base64

            if ".github/release-modules.json" in route:
                data = json.dumps(
                    {
                        "modules": {
                            "dotmac-learning": {
                                "package_dir": "packages/dotmac-learning",
                                "tag_prefix": "dotmac-learning-v",
                            }
                        }
                    }
                )
            else:
                data = '[tool.poetry]\nname="dotmac-learning"\nversion="0.1.0a1"\n'
            return {
                "encoding": "base64",
                "content": base64.b64encode(data.encode()).decode(),
            }
        raise AssertionError(route)

    def pages_all(self, route, key):
        if route in self.pages:
            return copy.deepcopy(self.pages[route])
        if "/check-runs" in route:
            return copy.deepcopy(self.checks)
        if "/statuses" in route:
            return copy.deepcopy(self.statuses)
        if "release-module.yml/runs" in route:
            return copy.deepcopy(self.prior)
        if "/actions/runs" in route:
            return copy.deepcopy(self.runs)
        raise AssertionError(route)

    def dispatch(self, payload):
        self.posts.append(payload)


def request(api):
    return {
        "mode": "armed",
        "module": "dotmac-learning",
        "version": "0.1.0a1",
        "expected_main_sha": SHA,
        "checks": [
            {"id": c["id"], "name": c["name"], "conclusion": c["conclusion"]}
            for c in api.checks
        ],
        "workflow_runs": [
            {k: r[k] for k in ("id", "name", "run_attempt")} for r in api.runs
        ],
        "qualified_skipped_contexts": [],
    }


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.api = FakeAPI()
        self.packet = request(self.api)

    def refuse(self):
        with self.assertRaises(bridge.Refusal):
            bridge.execute(self.api, self.packet, dispatch=True)
        self.assertEqual([], self.api.posts)

    def test_admit_exact_once(self):
        self.assertTrue(bridge.execute(self.api, self.packet, dispatch=True))
        self.assertEqual(
            [
                {
                    "ref": "main",
                    "inputs": {"module": "dotmac-learning", "version": "0.1.0a1"},
                }
            ],
            self.api.posts,
        )

    def test_preflight_unqualified_has_no_network_or_post(self):
        packet = dict(
            self.packet,
            mode="preflight",
            expected_main_sha=None,
            checks=[],
            workflow_runs=[],
            qualified_skipped_contexts=[],
        )
        self.assertFalse(bridge.execute(None, packet, dispatch=False))
        self.assertEqual([], self.api.posts)

    def test_each_negative_guard(self):
        mutations = [
            lambda: self.packet.update(expected_main_sha=None),
            lambda: self.api.branch["commit"].update(sha="b" * 40),
            lambda: self.api.branch.update(protected=False),
            lambda: self.packet.update(module="dotmac-kernel"),
            lambda: self.packet.update(version="0.1.0a2"),
            lambda: setattr(self.api, "tag", {"ref": "exists"}),
            lambda: self.api.prior.append(
                {"display_title": "Release module dotmac-learning 0.1.0a1"}
            ),
            lambda: os.environ.update(GITHUB_RUN_ATTEMPT="2"),
            lambda: self.api.checks.append(
                dict(self.api.checks[0], id=99, conclusion="failure")
            ),
            lambda: self.api.checks[0].update(status="in_progress", conclusion=None),
            lambda: self.api.statuses.append({"state": "failure"}),
            lambda: self.api.runs[0].update(conclusion="failure"),
            lambda: self.api.runs.append(
                dict(self.api.runs[0], id=99, status="queued")
            ),
            lambda: self.packet.update(checks=self.packet["checks"][:-1]),
            lambda: self.packet.update(workflow_runs=self.packet["workflow_runs"][:-1]),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.api = FakeAPI()
                self.packet = request(self.api)
                os.environ.update(ENV)
                mutate()
                self.refuse()

    def test_unknown_push_main_contract_refused_without_new_checks(self):
        for status, conclusion in (
            ("queued", None),
            ("completed", "failure"),
            ("completed", "success"),
        ):
            for changed in (
                {"path": ".github/workflows/unknown.yml"},
                {"workflow_id": 999999},
                {"name": "Unknown workflow"},
            ):
                with self.subTest(status=status, contract=changed):
                    self.api = FakeAPI()
                    self.packet = request(self.api)
                    unchanged_checks = copy.deepcopy(self.packet["checks"])
                    self.api.runs.append(
                        dict(
                            self.api.runs[0],
                            id=999,
                            status=status,
                            conclusion=conclusion,
                            **changed,
                        )
                    )
                    self.assertEqual(unchanged_checks, self.packet["checks"])
                    self.assertEqual(4, len(self.api.checks))
                    self.refuse()

    def test_other_branch_and_nonpush_runs_do_not_qualify_or_block(self):
        for event, branch in (
            ("push", "other-branch"),
            ("workflow_dispatch", "main"),
            ("pull_request", "main"),
        ):
            with self.subTest(event=event, branch=branch):
                self.api = FakeAPI()
                self.packet = request(self.api)
                self.api.runs.append(
                    dict(
                        self.api.runs[0],
                        id=999,
                        path=".github/workflows/release-module.yml",
                        name="Release module",
                        workflow_id=999,
                        event=event,
                        head_branch=branch,
                        status="queued",
                        conclusion=None,
                    )
                )
                self.assertTrue(bridge.execute(self.api, self.packet, dispatch=True))
                self.assertEqual(1, len(self.api.posts))

    def test_packet_run_cannot_be_replaced_with_other_branch_or_event(self):
        for changed in ({"event": "workflow_dispatch"}, {"head_branch": "other"}):
            with self.subTest(changed=changed):
                self.api = FakeAPI()
                self.packet = request(self.api)
                self.api.runs[0].update(changed)
                self.refuse()

    def test_unknown_run_population_guard_red_sensitivity(self):
        self.api.runs.append(
            dict(
                self.api.runs[0],
                id=999,
                path=".github/workflows/unknown.yml",
                name="Unknown workflow",
                workflow_id=999,
                status="queued",
                conclusion=None,
            )
        )
        # Restore the exact v1 defect temporarily, entirely in the fake API lane.
        with patch.object(
            bridge,
            "qualified_push_main_runs",
            side_effect=lambda runs, sha: [
                r for r in runs if r.get("path") in bridge.CANONICAL
            ],
        ):
            with self.assertRaises(AssertionError):
                self.refuse()

    def test_main_moves_during_guards(self):
        original = self.api.get
        count = 0

        def moving(route, **kwargs):
            nonlocal count
            if route == "/branches/main":
                count += 1
                if count >= 3:
                    self.api.branch["commit"]["sha"] = "b" * 40
            return original(route, **kwargs)

        self.api.get = moving
        self.refuse()

    def test_hidden_paginated_pending_is_refused(self):
        self.api.pages[f"/commits/{SHA}/check-runs?filter=all"] = [
            *self.api.checks,
            dict(self.api.checks[0], id=999, status="queued", conclusion=None),
        ]
        self.refuse()

    def test_skip_requires_successful_canonical_owner(self):
        self.api.checks[0]["conclusion"] = "skipped"
        self.packet = request(self.api)
        self.refuse()
        self.packet["qualified_skipped_contexts"] = ["check-1"]
        self.assertTrue(bridge.execute(self.api, self.packet, dispatch=False))
        self.api.checks[0]["check_suite"]["id"] = 987
        self.refuse()

    def test_hosted_red_sensitivity_detects_guard_violation(self):
        # A temporary fake guard violation admits stale main. The same refusal
        # assertion must turn RED; the hosted test succeeds only when it detects it.
        self.api.branch["commit"]["sha"] = "b" * 40
        with patch.object(bridge, "assert_current_main", return_value=None):
            with self.assertRaises(AssertionError):
                self.refuse()


class TransportTests(unittest.TestCase):
    def test_pagination_accumulates_and_fails_at_bound(self):
        api = bridge.API("never-output")
        calls = []

        def page(route, **kwargs):
            calls.append(route)
            return {"check_runs": [{"id": len(calls)}] * 100}

        with patch.object(api, "get", side_effect=page):
            with self.assertRaises(bridge.Refusal):
                api.pages_all(
                    "/commits/" + SHA + "/check-runs?filter=all", "check_runs"
                )
        self.assertEqual(bridge.MAX_PAGES, len(calls))

    def test_second_page_pending_is_visible(self):
        api = bridge.API("never-output")
        first = [{"id": i, "status": "completed"} for i in range(100)]
        hidden = {"id": 101, "status": "queued"}
        with patch.object(
            api,
            "get",
            side_effect=[
                {"total_count": 101, "check_runs": first},
                {"total_count": 101, "check_runs": [hidden]},
            ],
        ):
            rows = api.pages_all("/commits/" + SHA + "/check-runs", "check_runs")
        self.assertEqual(hidden, rows[-1])
        self.assertEqual(101, len(rows))

    def test_truncated_population_is_indeterminate(self):
        api = bridge.API("never-output")
        with (
            patch.object(
                api, "get", return_value={"total_count": 2000, "workflow_runs": []}
            ),
            self.assertRaises(bridge.Refusal),
        ):
            api.pages_all("/actions/runs", "workflow_runs")

    def test_ambiguous_post_is_never_retried_or_disclosed(self):
        api = bridge.API("secret-canary-never-output")
        with patch.object(
            api._opener,
            "open",
            side_effect=OSError("secret-canary-never-output raw response body"),
        ) as opened:
            with self.assertRaises(bridge.Refusal) as caught:
                api.dispatch(
                    {
                        "ref": "main",
                        "inputs": {"module": "dotmac-learning", "version": "0.1.0a1"},
                    }
                )
        self.assertEqual(1, opened.call_count)
        self.assertEqual("api_indeterminate_reconcile", str(caught.exception))

    def test_reject_foreign_routes_and_redirects(self):
        api = bridge.API("never-output")
        for route in (
            "https://evil.invalid",
            "//evil",
            "/../tokens",
            "/actions/workflows/evil/dispatches",
        ):
            with self.subTest(route=route), self.assertRaises(bridge.Refusal):
                api.get(route)
        with self.assertRaises(bridge.Refusal):
            bridge.NoRedirect().redirect_request(
                None, None, 302, None, None, "https://evil.invalid"
            )


if __name__ == "__main__":
    unittest.main()
