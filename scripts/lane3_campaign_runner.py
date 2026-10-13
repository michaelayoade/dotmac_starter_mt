#!/usr/bin/env python3
"""Fixed nonroot source qualification followed by the pinned existing B8 matrix."""

import copy
import hashlib
import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path

DEPLOYMENT = None


def configure(deployment):
    """Install reviewed protected inputs; synthetic callers inject the same shape.

    The launcher must verify ownership, permissions, deployment digest and source
    acceptance before calling. These values are bindings, never authorization.
    """
    global DEPLOYMENT
    if DEPLOYMENT is not None:
        raise RuntimeError("deployment-already-configured")
    if type(deployment) is not dict or set(deployment) != {"pins", "code_directory"}:
        raise ValueError("deployment-schema")
    candidate = copy.deepcopy(deployment)
    for key in {"code_directory"}:
        value = candidate[key]
        if type(value) is not str or not Path(value).is_absolute() or "\x00" in value:
            raise ValueError("deployment-path")
        candidate[key] = Path(value)
    pins = candidate["pins"]
    if type(pins) is not dict or set(pins) != {
        "b8-source-qualification.py",
        "b8-proof-matrix.py",
    }:
        raise ValueError("deployment-pins")
    if any(
        type(pin) is not str
        or len(pin) != 64
        or any(c not in "0123456789abcdef" for c in pin)
        for pin in pins.values()
    ):
        raise ValueError("deployment-pins")
    candidate["pins"] = dict(pins)
    DEPLOYMENT = candidate


def deployment_inputs():
    if DEPLOYMENT is None:
        raise RuntimeError("protected-deployment-required")
    return copy.deepcopy(DEPLOYMENT)


def pinned_modules():
    base = deployment_inputs()["code_directory"]
    for directory in (base, *base.parents):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("code")
    verified = {}
    for name, expected in deployment_inputs()["pins"].items():
        fd = os.open(base / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_nlink != 1
                or info.st_mode & 0o022
                or not 0 < info.st_size <= 2_000_000
            ):
                raise ValueError("code")
            data = os.read(fd, info.st_size + 1)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("code")
            verified[name] = data
        finally:
            os.close(fd)
    # Both hashes pass before either module runs. Execute the checked bytes,
    # avoiding a second loader read between the hash and module execution.
    modules = []
    for name, module_name in (
        ("b8-proof-matrix.py", "_dotmac_b8_matrix"),
        ("b8-source-qualification.py", "_dotmac_b8_source_qualification"),
    ):
        spec = importlib.util.spec_from_file_location(module_name, base / name)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        exec(compile(verified[name], str(base / name), "exec"), module.__dict__)  # noqa: S102
        modules.append(module)
    return modules


def read_private(directory_fd, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or not 0 < info.st_size <= 1_000_000
        ):
            raise ValueError("input")
        return json.loads(os.read(fd, info.st_size + 1))
    finally:
        os.close(fd)


class WrapperView:
    def __init__(self, work, deadline_ns):
        if type(deadline_ns) is not int or deadline_ns <= 0:
            raise ValueError("deadline")
        self.work, self.deadline_ns = work, deadline_ns

    def remaining(self):
        remaining_ns = self.deadline_ns - time.monotonic_ns()
        if remaining_ns <= 0:
            raise TimeoutError("deadline")
        return remaining_ns / 1_000_000_000


def load_deployment():
    """Only the fixed, root-protected deployment record is accepted by the CLI."""
    path = Path("/etc/dotmac-lane3-campaign/runner-deployment.json")
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("deployment-parent")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_gid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o444
            or not 0 < info.st_size <= 16384
        ):
            raise ValueError("deployment-file")

        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("deployment-duplicate")
                result[key] = value
            return result

        configure(json.loads(os.read(fd, info.st_size + 1), object_pairs_hook=pairs))
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    if os.geteuid() <= 0 or os.getuid() != os.geteuid() or len(sys.argv) != 2:
        raise ValueError("identity_or_input")
    if DEPLOYMENT is None:
        load_deployment()
    matrix, qualifier = pinned_modules()
    matrix_path = Path(sys.argv[1])
    if not matrix_path.is_absolute() or matrix_path.name != "matrix.json":
        raise ValueError("input")
    work = matrix_path.parent
    directory_fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("workspace")
        matrix.private_file(matrix_path, "input.permissions")
        cfg = read_private(directory_fd, "matrix.json")
        execution = read_private(directory_fd, "execution.json")
        if not isinstance(execution, dict) or set(execution) != {
            "execution_deadline_ns"
        }:
            raise ValueError("deadline")
        view = WrapperView(work, execution["execution_deadline_ns"])
        view.remaining()
        matrix.validate(cfg)
        material = json.dumps(
            cfg["material"], sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        fd = os.open(
            "material.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError("material")
            view_bytes = memoryview(material)
            while view_bytes:
                count = os.write(fd, view_bytes)
                if count <= 0:
                    raise OSError("material")
                view_bytes = view_bytes[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if qualifier.qualify_session(cfg, view, matrix) is not True:
            raise ValueError("qualification")
        view.remaining()
        # Preserve argv and reuse the original matrix's result/classification.
        result = matrix.main()
        if type(result) is not int or result not in (0, 1, 2):
            raise ValueError("result")
        return result
    finally:
        os.close(directory_fd)


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:
        print(
            json.dumps(
                {
                    "status": "indeterminate",
                    "reason": "qualified_runner_unproved",
                    "private_diagnostics_suppressed": True,
                },
                sort_keys=True,
            )
        )
        result = 2
    raise SystemExit(result)
