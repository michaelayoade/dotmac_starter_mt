"""Temporary, closed-target hosted bridge. No publication implementation lives here.

Only the existing protected-main release workflow owns builds, registry credentials,
verification, tags and records. The coordinator owns the named release freeze and
closes it only after verification/tag/record/green main. A prior dispatch requires
operator reconciliation; this bridge never retries a POST or repairs a release.
"""

import argparse
import base64
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "michaelayoade/dotmac_starter_mt"
WORKFLOW = "release-module.yml"
TARGETS = {
    "dotmac-learning": "0.1.0a1",
    "dotmac-fulfillment": "0.1.0a2",
    "dotmac-template-studio": "0.2.0a6",
}
CANONICAL = {
    ".github/workflows/ci.yml": "CI",
    ".github/workflows/engineering-standards.yml": "Engineering standards",
    ".github/workflows/deployment-render-check.yml": "Deployment render check",
    ".github/workflows/lane3-handoff-integration.yml": "Lane 3 handoff integration",
}
WORKFLOW_IDS = dict(
    zip(CANONICAL, (315161312, 326159777, 351390009, 380752908), strict=False)
)
REQUEST = Path("ops/dispatch_bridge_temporary/request.json")
MAX_PAGES = 100
MAX_BYTES = 8 * 1024 * 1024


class Refusal(Exception):
    """Public fixed refusal codes only; never an API body or token."""


def require(condition, code):
    if not condition:
        raise Refusal(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refusal("api_redirect_refused")


class API:
    def __init__(self, token):
        self._token = token
        self._opener = urllib.request.build_opener(NoRedirect())

    def _request(self, route, *, payload=None, optional=False):
        # Routes are internal, fixed-repository resources; no user URL is accepted.
        valid = re.fullmatch(
            r"/(?:branches/main|commits/[0-9a-f]{40}/(?:check-runs|statuses)|"
            r"actions/runs|actions/workflows/release-module\.yml/(?:runs|dispatches)|"
            r"git/ref/tags/dotmac-(?:learning|fulfillment|template-studio)-v[0-9.a]+|"
            r"contents/(?:\.github/release-modules\.json|packages/dotmac-"
            r"(?:learning|fulfillment|template-studio)/pyproject\.toml))"
            r"(?:\?[A-Za-z0-9_=&.-]+)?",
            route,
        )
        require(valid is not None, "api_route_refused")
        require(
            (payload is not None)
            == (route == f"/actions/workflows/{WORKFLOW}/dispatches"),
            "api_write_refused",
        )
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}{route}",
            data=data,
            method="GET" if data is None else "POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener.open(req, timeout=20) as response:
                if payload is not None:
                    require(response.status == 204, "dispatch_outcome_reconcile")
                    return None
                require(response.status == 200, "api_status_refused")
                raw = response.read(MAX_BYTES + 1)
                require(len(raw) <= MAX_BYTES, "api_size_refused")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if optional and exc.code == 404:
                return None
            raise Refusal("api_http_refused") from None
        except (OSError, ValueError, urllib.error.URLError):
            raise Refusal(
                "api_indeterminate_reconcile" if payload else "api_indeterminate"
            ) from None

    def get(self, route, *, optional=False):
        return self._request(route, optional=optional)

    def pages_all(self, route, key):
        result = []
        separator = "&" if "?" in route else "?"
        for page in range(1, MAX_PAGES + 1):
            body = self.get(f"{route}{separator}per_page=100&page={page}")
            rows = body if key is None else body.get(key)
            require(isinstance(rows, list) and len(rows) <= 100, "api_page_refused")
            result.extend(rows)
            if len(rows) < 100:
                # Actions search is capped at 1000 results. Refuse any truncated
                # population rather than infer absence from its visible prefix.
                if isinstance(body, dict) and "total_count" in body:
                    require(
                        body["total_count"] == len(result), "api_population_incomplete"
                    )
                return result
        raise Refusal("api_pagination_bound")

    def dispatch(self, payload):
        # Deliberately one POST, with no retry, wait, cancellation or rerun path.
        self._request(f"/actions/workflows/{WORKFLOW}/dispatches", payload=payload)


def validate_request(packet):
    require(
        isinstance(packet, dict)
        and set(packet)
        == {
            "mode",
            "module",
            "version",
            "expected_main_sha",
            "checks",
            "workflow_runs",
            "qualified_skipped_contexts",
        },
        "request_shape",
    )
    require(packet["mode"] in ("preflight", "armed"), "request_mode")
    require(packet["module"] in TARGETS, "module_refused")
    require(packet["version"] == TARGETS[packet["module"]], "version_refused")
    for field in ("checks", "workflow_runs", "qualified_skipped_contexts"):
        require(isinstance(packet[field], list), "qualification_shape")
    if packet["mode"] == "preflight":
        require(
            packet["expected_main_sha"] is None
            and not packet["checks"]
            and not packet["workflow_runs"]
            and not packet["qualified_skipped_contexts"],
            "preflight_must_be_unarmed",
        )
    else:
        require(
            isinstance(packet["expected_main_sha"], str)
            and re.fullmatch("[0-9a-f]{40}", packet["expected_main_sha"]),
            "unqualified_main",
        )


def assert_context():
    require(os.environ.get("GITHUB_REPOSITORY") == REPO, "repository_refused")
    require(os.environ.get("GITHUB_EVENT_NAME") == "push", "event_refused")
    require(
        re.fullmatch(
            r"refs/heads/chore/module-release-dispatch-[A-Za-z0-9._-]+",
            os.environ.get("GITHUB_REF", ""),
        ),
        "branch_refused",
    )
    require(os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "retry_refused")


def assert_current_main(api, sha):
    branch = api.get("/branches/main")
    require(
        branch.get("protected") is True and branch.get("commit", {}).get("sha") == sha,
        "protected_current_main_refused",
    )


def trusted_content(api, path, sha):
    body = api.get(f"/contents/{path}?ref={sha}")
    require(
        body.get("encoding") == "base64" and isinstance(body.get("content"), str),
        "metadata_unreadable",
    )
    try:
        return base64.b64decode(body["content"], validate=False).decode("utf-8")
    except (ValueError, UnicodeError):
        raise Refusal("metadata_unreadable") from None


def indexed(rows, keys, code):
    require(all(isinstance(row, dict) and set(row) == set(keys) for row in rows), code)
    require(all(type(row["id"]) is int and row["id"] > 0 for row in rows), code)
    result = {row["id"]: row for row in rows}
    require(len(result) == len(rows) and bool(rows), code)
    return result


def qualified_push_main_runs(runs, sha):
    """Account for every push/main run before choosing qualification evidence.

    Same-SHA manual release dispatches and other-branch runs are outside main's
    push qualification population. They cannot substitute for its canonical runs.
    Unknown push/main workflow contracts require coordinator reconciliation even
    when successful and even before they have emitted any check run.
    """
    qualified = []
    for run in runs:
        require(run.get("head_sha") == sha, "workflow_query_head_changed")
        if run.get("event") != "push" or run.get("head_branch") != "main":
            continue
        path = run.get("path")
        require(path in CANONICAL, "unknown_push_main_workflow_contract")
        require(
            run.get("workflow_id") == WORKFLOW_IDS[path]
            and run.get("name") == CANONICAL[path],
            "unknown_push_main_workflow_contract",
        )
        qualified.append(run)
    return qualified


def guard(api, packet):
    sha = packet["expected_main_sha"]
    module = packet["module"]
    version = packet["version"]
    assert_current_main(api, sha)
    allowlist = json.loads(trusted_content(api, ".github/release-modules.json", sha))
    row = allowlist.get("modules", {}).get(module, {})
    require(
        row.get("package_dir") == f"packages/{module}"
        and row.get("tag_prefix") == f"{module}-v",
        "trusted_allowlist_refused",
    )
    metadata = tomllib.loads(
        trusted_content(api, f"packages/{module}/pyproject.toml", sha)
    )
    project = metadata.get("tool", {}).get("poetry", metadata.get("project", {}))
    require(
        project.get("name") == module and project.get("version") == version,
        "trusted_version_refused",
    )
    checks = api.pages_all(f"/commits/{sha}/check-runs?filter=all", "check_runs")
    expected = indexed(
        packet["checks"], ("id", "name", "conclusion"), "check_packet_refused"
    )
    require(
        len(checks) == len(expected) and {c["id"] for c in checks} == set(expected),
        "complete_checks_changed",
    )
    skipped = packet["qualified_skipped_contexts"]
    require(
        all(isinstance(name, str) for name in skipped)
        and len(set(skipped)) == len(skipped),
        "skip_packet_refused",
    )
    runs = api.pages_all(f"/actions/runs?head_sha={sha}", "workflow_runs")
    canonical = qualified_push_main_runs(runs, sha)
    run_packet = indexed(
        packet["workflow_runs"],
        ("id", "name", "run_attempt"),
        "workflow_packet_refused",
    )
    require(
        len(canonical) == len(run_packet)
        and {r["id"] for r in canonical} == set(run_packet)
        and {r["path"] for r in canonical} == set(CANONICAL),
        "canonical_runs_changed",
    )
    suites = set()
    for run in canonical:
        evidence = run_packet[run["id"]]
        require(
            run.get("workflow_id") == WORKFLOW_IDS[run["path"]]
            and run.get("name") == CANONICAL[run["path"]] == evidence["name"]
            and run.get("head_sha") == sha
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and type(evidence["run_attempt"]) is int
            and run.get("run_attempt") == evidence["run_attempt"],
            "canonical_run_refused",
        )
        suites.add(run["check_suite_id"])
    actual_skipped = set()
    for check in checks:
        evidence = expected[check["id"]]
        require(
            check.get("name") == evidence["name"]
            and check.get("status") == "completed"
            and check.get("conclusion") == evidence["conclusion"],
            "check_changed",
        )
        if check["conclusion"] == "skipped":
            require(
                check["name"] in skipped
                and check.get("app", {}).get("slug") == "github-actions"
                and check.get("check_suite", {}).get("id") in suites,
                "skip_unqualified",
            )
            actual_skipped.add(check["name"])
        else:
            require(check["conclusion"] == "success", "check_not_success")
    require(actual_skipped == set(skipped), "skip_packet_changed")
    statuses = api.pages_all(f"/commits/{sha}/statuses", None)
    require(all(s.get("state") == "success" for s in statuses), "legacy_status_refused")
    require(
        api.get(f"/git/ref/tags/{module}-v{version}", optional=True) is None,
        "existing_tag_refused",
    )
    prior = api.pages_all(
        f"/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch", "workflow_runs"
    )
    require(
        not any(
            r.get("display_title") == f"Release module {module} {version}"
            for r in prior
        ),
        "prior_attempt_requires_reconciliation",
    )
    assert_current_main(api, sha)


def execute(api, packet, *, dispatch):
    assert_context()
    validate_request(packet)
    if packet["mode"] == "preflight":
        require(not dispatch, "preflight_cannot_dispatch")
        return False
    guard(api, packet)
    if dispatch:
        guard(api, packet)
        # The last read immediately before the only write is the protected tip.
        assert_current_main(api, packet["expected_main_sha"])
        api.dispatch(
            {
                "ref": "main",
                "inputs": {"module": packet["module"], "version": packet["version"]},
            }
        )
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dispatch", action="store_true")
    args = parser.parse_args()
    try:
        packet = json.loads(REQUEST.read_text())
        require(isinstance(packet, dict), "request_shape")
        api = API(os.environ["GITHUB_TOKEN"]) if packet.get("mode") == "armed" else None
        admitted = execute(api, packet, dispatch=args.dispatch)
        if not args.dispatch:
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
                output.write(f"dispatch={'true' if admitted else 'false'}\n")
        print(
            "dispatch_submitted_reconcile_run"
            if args.dispatch
            else "preflight_qualified"
            if admitted
            else "preflight_unarmed_dispatch_false"
        )
        return 0
    except (Refusal, KeyError, TypeError, ValueError, OSError) as exc:
        print(
            f"bridge_refused:{exc}"
            if isinstance(exc, Refusal)
            else "bridge_refused:invalid_or_indeterminate"
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
