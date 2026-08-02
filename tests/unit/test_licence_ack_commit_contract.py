"""Regression guard: an acknowledgement escapes ONLY if its projection committed.

WS8 defines `applied` as a **committed** local projection, not a successful
service call — the vendor control plane advances a delivery to `active` purely
on the strength of this ack, so an ack that outlives a rolled-back transaction
would tell the vendor a deployment is licensed when it holds no grants.

The risk is structural rather than obvious: `dotmac_kernel.db.get_db` commits
in its dependency teardown, i.e. AFTER the route handler has already returned
the response body. This pins the behaviour that makes the contract hold — a
commit failure must surface as a server error, never as a 200 carrying an
`applied` acknowledgement.

Scoped to the seam, not the whole receiver: the projection logic itself is
covered in `test_licensing_receiver.py`.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


class _CommitFailingSession:
    """Stands in for a session whose COMMIT fails (deadlock, disk, network) —
    the case where every write in the request is lost after the handler has
    already produced its acknowledgement."""

    def __init__(self) -> None:
        self.rolled_back = False

    def commit(self) -> None:
        raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        pass


@pytest.fixture
def failing_session() -> _CommitFailingSession:
    return _CommitFailingSession()


@pytest.fixture
def ack_app(failing_session: _CommitFailingSession) -> FastAPI:
    """A minimal app whose dependency mirrors `dotmac_kernel.db.get_db`'s
    ordering: yield → commit → rollback-on-error → close."""

    def get_db() -> Generator[_CommitFailingSession, None, None]:
        db = failing_session
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app = FastAPI()

    @app.post("/licences/apply")
    def apply(db: _CommitFailingSession = Depends(get_db)) -> dict[str, object]:
        # The handler returns an ack exactly as the real receiver route does.
        return {
            "acknowledgement": {
                "licence_id": "lic-1",
                "licence_version": 1,
                "digest": "sha256:abcd",
                "status": "applied",
            },
            "applied": True,
        }

    return app


def test_failed_commit_does_not_return_an_applied_acknowledgement(
    ack_app: FastAPI, failing_session: _CommitFailingSession
) -> None:
    with TestClient(ack_app, raise_server_exceptions=False) as client:
        response = client.post("/licences/apply")

    # The projection did not commit, so the caller must not be told it applied.
    assert response.status_code == 500
    assert "applied" not in response.text
    assert failing_session.rolled_back is True


def test_successful_commit_does_return_the_acknowledgement() -> None:
    """The positive control — otherwise the guard above could pass simply
    because the route never works."""
    committed: list[bool] = []

    class _OkSession:
        def commit(self) -> None:
            committed.append(True)

        def rollback(self) -> None:  # pragma: no cover — not reached
            raise AssertionError("must not roll back on success")

        def close(self) -> None:
            pass

    def get_db() -> Generator[_OkSession, None, None]:
        db = _OkSession()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    app = FastAPI()

    @app.post("/licences/apply")
    def apply(db: _OkSession = Depends(get_db)) -> dict[str, object]:
        return {"acknowledgement": {"status": "applied"}, "applied": True}

    with TestClient(app) as client:
        response = client.post("/licences/apply")

    assert response.status_code == 200
    assert response.json()["acknowledgement"]["status"] == "applied"
    assert committed == [True]
