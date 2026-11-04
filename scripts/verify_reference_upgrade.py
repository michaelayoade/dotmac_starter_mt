#!/usr/bin/env python3
"""GitHub-hosted final-image install and source-baseline upgrade qualification.

No workstation or named test host is supported. All database state belongs to
one disposable Docker network. No package is published and no host is deployed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID


def hosted_identity(environment: Mapping[str, str]) -> str:
    if (
        environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not re.fullmatch(r"[0-9]+", environment.get("GITHUB_RUN_ID", ""))
        or not re.fullmatch(r"[0-9]+", environment.get("GITHUB_RUN_ATTEMPT", ""))
    ):
        raise ValueError("Disposable qualification requires a GitHub-hosted run")
    return environment["GITHUB_RUN_ID"] + "-" + environment["GITHUB_RUN_ATTEMPT"]


def load_baseline(path: Path) -> str:
    value = json.loads(path.read_text())
    if value.get("kind") != "source-compatibility" or not re.fullmatch(
        r"[0-9a-f]{40}", value.get("source_sha", "")
    ):
        raise ValueError("Baseline must identify an immutable source compatibility pin")
    return value["source_sha"]


class Qualification:
    def __init__(self, candidate: str, source: Path, identity: str) -> None:
        self.candidate = candidate
        self.source = source
        self.candidate_source = source
        self.prefix = f"reference-upgrade-{identity}-{secrets.token_hex(4)}"
        self.db = self.prefix + "-db"
        self.app = self.prefix + "-app"
        self.baseline_image = self.prefix + ":baseline"
        self.root_host = "upgrade.example.com"
        self.environment = {
            **os.environ,
            "ENVIRONMENT": "prod",
            "PLATFORM_ROOT_DOMAIN": self.root_host,
            "TRUSTED_HOSTS": self.root_host + ",*." + self.root_host,
            "JWT_SECRET": secrets.token_urlsafe(48),
            "SESSION_HASH_SECRET": secrets.token_urlsafe(48),
            "CSRF_SECRET": secrets.token_urlsafe(48),
            "QUALIFICATION_PASSWORD": secrets.token_urlsafe(24),
        }
        self.private_values = [
            self.environment[key]
            for key in (
                "JWT_SECRET",
                "SESSION_HASH_SECRET",
                "CSRF_SECRET",
                "QUALIFICATION_PASSWORD",
            )
        ]
        self.receipt: dict = {"status": "failed", "checks": []}
        self.port = ""

    def run(self, *args: str, stdin: str | None = None, env=None) -> str:
        result = subprocess.run(
            args,
            input=stdin,
            text=True,
            capture_output=True,
            env=env or self.environment,
            timeout=1800,
        )
        if result.returncode:
            detail = (result.stdout + result.stderr)[-6000:]
            for value in self.private_values:
                detail = detail.replace(value, "[redacted]")
            print(detail, flush=True)
            raise RuntimeError(f"Qualification command failed: {args[0]}")
        return result.stdout.strip()

    def check(self, condition: bool, name: str) -> None:
        if not condition:
            raise RuntimeError(f"Qualification assertion failed: {name}")
        self.receipt["checks"].append(name)

    def image(self, image: str, *command: str, stdin=None, bootstrap=False) -> str:
        environment = dict(self.environment)
        if bootstrap:
            environment["PLATFORM_DATABASE_URL"] = environment["MIGRATION_DATABASE_URL"]
        arguments = ["docker", "run", "--rm", "-i", "--network", self.prefix]
        for name in self.runtime_names():
            arguments.extend(("--env", name))
        if bootstrap:
            bootstrap_source = (
                self.candidate_source if image == self.candidate else self.source
            )
            bootstrap_script = bootstrap_source / "scripts/create_platform_admin.py"
            arguments.extend(
                (
                    "--mount",
                    f"type=bind,src={bootstrap_script},"
                    "dst=/qualification/create_platform_admin.py,readonly",
                )
            )
        return self.run(*arguments, image, *command, stdin=stdin, env=environment)

    def runtime_names(self) -> tuple[str, ...]:
        return (
            "ENVIRONMENT",
            "PLATFORM_ROOT_DOMAIN",
            "TRUSTED_HOSTS",
            "JWT_SECRET",
            "SESSION_HASH_SECRET",
            "CSRF_SECRET",
            "DATABASE_URL",
            "PLATFORM_DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "QUALIFICATION_PASSWORD",
        )

    def sql(
        self, statement: str, *, role="postgres", database="postgres", variables=None
    ) -> str:
        parameters = []
        for name, value in (variables or {}).items():
            parameters.extend(("--set", f"{name}={value}"))
        return self.run(
            "docker",
            "exec",
            "-i",
            self.db,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            role,
            "-d",
            database,
            "-At",
            *parameters,
            stdin=statement,
        )

    def wait_database(self) -> None:
        for _ in range(60):
            try:
                self.run("docker", "exec", self.db, "pg_isready", "-U", "postgres")
                return
            except RuntimeError:
                time.sleep(1)
        raise RuntimeError("Disposable PostgreSQL did not start")

    def database(self, name: str) -> None:
        self.sql(
            'CREATE DATABASE :"database" OWNER app_admin;', variables={"database": name}
        )
        self.database_name = name
        for key, role in (
            ("DATABASE_URL", "app_user"),
            ("PLATFORM_DATABASE_URL", "platform_api"),
            ("MIGRATION_DATABASE_URL", "app_admin"),
        ):
            self.environment[key] = (
                f"postgresql+psycopg://{role}@{self.db}/{name}?connect_timeout=2"
            )

    def migrate(self, image: str) -> list[str]:
        self.image(image, "alembic", "upgrade", "heads")
        # Unlike `upgrade`, graph inspection does not run env.py. Install BOTH
        # declarations from the owning assembly before reading its actual heads.
        expected = set(
            json.loads(
                self.image(
                    image,
                    "python",
                    "-",
                    stdin="""
import json
from alembic.config import Config
from alembic.script import ScriptDirectory
from dotmac_kernel.planes import install_module_plane_selections
from dotmac_kernel.prerequisites import install_prerequisite_bindings
from app.assembly import assembly
from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS
install_module_plane_selections(assembly.module_planes)
install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
print(json.dumps(sorted(ScriptDirectory.from_config(Config('alembic.ini')).get_heads())))
""",
                )
            )
        )
        actual = self.sql(
            "SELECT version_num FROM alembic_version ORDER BY version_num;",
            role="app_admin",
            database=self.database_name,
        ).splitlines()
        self.check(
            bool(expected) and set(actual) == expected, "all composed heads applied"
        )
        return actual

    def bootstrap(self, image: str) -> None:
        self.image(
            image,
            "python",
            "-",
            bootstrap=True,
            stdin="""
import os, sys
sys.path.insert(0, '/qualification')
from create_platform_admin import upsert_admin
from dotmac_kernel.db import platform_session
with platform_session() as db:
    upsert_admin(db, email='operator@example.com',
                 password=os.environ['QUALIFICATION_PASSWORD'])
""",
        )

    def start(self, image: str, *, ready: bool) -> None:
        arguments = [
            "docker",
            "run",
            "-d",
            "--name",
            self.app,
            "--network",
            self.prefix,
            "-p",
            "127.0.0.1::8000",
        ]
        # Migration/maintenance credentials never reach the online container.
        for name in self.runtime_names():
            if name not in {"MIGRATION_DATABASE_URL", "QUALIFICATION_PASSWORD"}:
                arguments.extend(("--env", name))
        self.run(*arguments, image)
        self.port = self.run("docker", "port", self.app, "8000/tcp").split(":")[-1]
        self.wait_http("/health/ready" if ready else "/health", 200)

    def stop(self) -> None:
        self.run("docker", "rm", "-f", self.app)

    def request(self, method, path, *, host=None, token=None, payload=None):
        headers = {"Host": host or self.root_host, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            method=method,
            headers=headers,
            data=json.dumps(payload).encode() if payload is not None else None,
        )
        try:
            response = build_opener(ProxyHandler({})).open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.code, json.loads(body) if body else None

    def wait_http(self, path: str, status: int) -> None:
        for _ in range(60):
            try:
                if self.request("GET", path)[0] == status:
                    return
            except (URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(1)
        raise RuntimeError(f"HTTP readiness condition not reached: {path} {status}")

    def api(self, method, path, expected=200, **kwargs):
        status, body = self.request(method, path, **kwargs)
        self.check(status == expected, f"{method} {path} returns {expected}")
        return body

    def login(self, host: str, email: str, *, platform=False) -> str:
        path = "/platform/auth/login" if platform else "/auth/login"
        return self.api(
            "POST",
            path,
            host=host,
            payload={
                "email": email,
                "password": self.environment["QUALIFICATION_PASSWORD"],
            },
        )["access_token"]

    def seed(self) -> dict:
        operator = self.login(self.root_host, "operator@example.com", platform=True)
        subjects = {}
        for slug in ("alpha", "beta"):
            host = slug + "." + self.root_host
            email = f"owner@{slug}.example.com"
            tenant = self.api(
                "POST",
                "/platform/tenants",
                201,
                token=operator,
                payload={
                    "slug": slug,
                    "name": f"Upgrade {slug}",
                    "owner_email": email,
                    "owner_password": self.environment["QUALIFICATION_PASSWORD"],
                },
            )
            token = self.login(host, email)
            party = self.api(
                "POST",
                "/parties/people",
                201,
                host=host,
                token=token,
                payload={
                    "email": f"contact@{slug}.example.com",
                    "first_name": "Retained",
                    "last_name": slug,
                },
            )
            subjects[slug] = {
                "host": host,
                "email": email,
                "token": token,
                "tenant_id": tenant["id"],
                "party": party,
            }
        return subjects

    def verify(self, subjects: dict, *, retained_sessions=False) -> None:
        for slug, subject in subjects.items():
            host = subject["host"]
            token = self.login(host, subject["email"])
            own = subject["party"]
            observed = self.api("GET", f"/parties/{own['id']}", host=host, token=token)
            self.check(observed == own, "party fields retained exactly")
            if retained_sessions:
                self.api(
                    "GET", f"/parties/{own['id']}", host=host, token=subject["token"]
                )
            other = subjects["beta" if slug == "alpha" else "alpha"]
            foreign_path = f"/parties/{other['party']['id']}"
            self.api("GET", foreign_path, 404, host=host, token=token)
            self.api("DELETE", foreign_path, 404, host=host, token=token)
            self.api("GET", "/parties", 401, host=host, token=other["token"])
            visible = self.api("GET", "/parties", host=host, token=token)
            self.check(
                other["party"]["id"] not in {p["id"] for p in visible},
                "foreign party absent from listing",
            )
            tenant_id = str(UUID(subject["tenant_id"]))
            own_id = str(UUID(own["id"]))
            other_id = str(UUID(other["party"]["id"]))
            rows = self.sql(
                "BEGIN; SELECT set_config('app.current_tenant', :'tenant_id', true); "
                "SELECT count(*) FILTER (WHERE id=:'own_id'::uuid) = 1 "
                "AND count(*) FILTER (WHERE id=:'other_id'::uuid) = 0 "
                "FROM parties; ROLLBACK;",
                role="app_user",
                database=self.database_name,
                variables={
                    "tenant_id": tenant_id,
                    "own_id": own_id,
                    "other_id": other_id,
                },
            ).splitlines()
            self.check("t" in rows, "RLS shows own party and hides the foreign party")

    def cleanup(self) -> bool:
        # Names are generated once for this run. No external resource is accepted.
        ok = True
        for command in (
            ("docker", "rm", "-f", "-v", self.app),
            ("docker", "rm", "-f", "-v", self.db),
            ("docker", "network", "rm", self.prefix),
        ):
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode and "No such" not in result.stderr:
                ok = False
        return ok

    def execute(self, baseline_sha: str) -> None:
        self.receipt.update(
            {
                "kind": "source-compatibility",
                "baseline_source_sha": baseline_sha,
                "candidate_source_sha": os.environ["GITHUB_SHA"],
                "pull_request_head": os.environ.get("QUALIFICATION_HEAD_SHA", ""),
                "pull_request_base": os.environ.get("QUALIFICATION_BASE_SHA", ""),
                "run_id": os.environ["GITHUB_RUN_ID"],
                "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            }
        )
        print("Building the immutable pre-hardening source baseline", flush=True)
        self.run(
            "git", "merge-base", "--is-ancestor", baseline_sha, os.environ["GITHUB_SHA"]
        )
        self.check(
            baseline_sha != os.environ["GITHUB_SHA"], "distinct baseline revision"
        )
        with tempfile.TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as temporary:
            directory = Path(temporary)
            archive = directory / "baseline.tar"
            self.run("git", "archive", "--format=tar", "-o", str(archive), baseline_sha)
            tree = directory / "source"
            tree.mkdir()
            with tarfile.open(archive) as source_archive:
                source_archive.extractall(tree, filter="data")
            self.source = tree
            self.run(
                "docker",
                "build",
                "--build-arg",
                "APP_PORT=8000",
                "-t",
                self.baseline_image,
                str(tree),
            )
            self.run("docker", "network", "create", self.prefix)
            self.run(
                "docker",
                "run",
                "-d",
                "--name",
                self.db,
                "--network",
                self.prefix,
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "postgres:16",
            )
            self.wait_database()
            self.sql("""
CREATE ROLE app_admin LOGIN BYPASSRLS NOSUPERUSER;
CREATE ROLE app_user LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE platform_api LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE outbox_dispatcher LOGIN NOSUPERUSER NOBYPASSRLS;
CREATE ROLE platform_outbox_dispatcher LOGIN NOSUPERUSER NOBYPASSRLS;
""")
            for label, image in (
                ("baseline", self.baseline_image),
                ("candidate", self.candidate),
            ):
                self.receipt[label + "_image_id"] = self.run(
                    "docker", "image", "inspect", "--format", "{{.Id}}", image
                )
            self.database("upgrade")
            self.receipt["baseline_heads"] = self.migrate(self.baseline_image)
            self.bootstrap(self.baseline_image)
            self.start(self.baseline_image, ready=False)
            subjects = self.seed()
            self.verify(subjects)
            self.stop()
            print(
                "Upgrading the retained database with the candidate image", flush=True
            )
            self.receipt["upgraded_heads"] = self.migrate(self.candidate)
            self.receipt["schema_heads_changed"] = (
                self.receipt["baseline_heads"] != self.receipt["upgraded_heads"]
            )
            self.start(self.candidate, ready=True)
            self.verify(subjects, retained_sessions=True)
            # New writes survive a candidate restart too.
            operator = self.login(self.root_host, "operator@example.com", platform=True)
            self.api(
                "POST",
                "/platform/tenants",
                201,
                token=operator,
                payload={
                    "slug": "after-upgrade",
                    "name": "New tenant after upgrade",
                    "owner_email": "owner@new.example.com",
                    "owner_password": self.environment["QUALIFICATION_PASSWORD"],
                },
            )
            self.stop()
            self.start(self.candidate, ready=True)
            self.login("after-upgrade." + self.root_host, "owner@new.example.com")
            self.verify(subjects, retained_sessions=True)
            self.run("docker", "stop", self.db)
            self.wait_http("/health/ready", 503)
            self.api("GET", "/health/live")
            self.run("docker", "start", self.db)
            self.wait_database()
            self.wait_http("/health/ready", 200)
            self.check(True, "readiness tracks database loss and recovery")
            self.stop()
            print("Installing the candidate on a second empty database", flush=True)
            self.database("fresh")
            self.receipt["fresh_heads"] = self.migrate(self.candidate)
            self.bootstrap(self.candidate)
            self.start(self.candidate, ready=True)
            self.verify(self.seed())
            self.check(
                self.receipt["fresh_heads"] == self.receipt["upgraded_heads"],
                "fresh and upgraded databases reach identical heads",
            )
            self.receipt["status"] = "passed"


def main() -> None:
    identity = hosted_identity(os.environ)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument(
        "--baseline", type=Path, default=Path(".github/reference-upgrade-baseline.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    qualification = Qualification(args.candidate_image, Path.cwd(), identity)
    try:
        qualification.execute(load_baseline(args.baseline))
    finally:
        clean = qualification.cleanup()
        qualification.receipt["cleanup"] = "passed" if clean else "failed"
        if not clean:
            qualification.receipt["status"] = "failed"
        args.output.write_text(json.dumps(qualification.receipt, indent=2) + "\n")
        if not clean:
            raise RuntimeError("Disposable qualification cleanup failed")


if __name__ == "__main__":
    main()
