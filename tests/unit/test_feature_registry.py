from dotmac_kernel.features import FeatureManifest, load_manifests, mount_features
from fastapi import APIRouter, FastAPI


def test_load_manifests_reads_feature_attribute():
    manifests = load_manifests(["app.features.parties"])
    assert manifests[0].name == "parties"
    assert manifests[0].routers


def test_mount_features_skips_disabled():
    r = APIRouter()

    @r.get("/x")
    def x():
        return {}

    app = FastAPI()
    manifest = FeatureManifest(name="demo", routers=[r], core=False)
    mount_features_from = [manifest]
    mount_features(
        app, manifests=mount_features_from, disabled={"demo"}, web_enabled=True
    )
    assert all(getattr(route, "path", "") != "/x" for route in app.routes)


def test_mount_features_mounts_enabled():
    r = APIRouter()

    @r.get("/y")
    def y():
        return {}

    app = FastAPI()
    mount_features(
        app,
        manifests=[FeatureManifest(name="demo", routers=[r])],
        disabled=set(),
        web_enabled=True,
    )
    assert any(getattr(route, "path", "") == "/y" for route in app.routes)


def test_mount_features_mounts_web_routers_when_web_enabled():
    """F1: `web_routers` mount alongside `routers` when `web_enabled=True`."""
    api = APIRouter()
    web = APIRouter()

    @api.get("/demo-api")
    def demo_api():
        return {}

    @web.get("/demo-web")
    def demo_web():
        return {}

    app = FastAPI()
    manifest = FeatureManifest(name="demo", routers=[api], web_routers=[web])
    mount_features(app, manifests=[manifest], disabled=set(), web_enabled=True)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/demo-api" in paths
    assert "/demo-web" in paths


def test_mount_features_skips_web_routers_when_web_disabled():
    """F1 pin: `web_enabled=False` mounts `routers` but never `web_routers`,
    even for an otherwise-enabled feature."""
    api = APIRouter()
    web = APIRouter()

    @api.get("/demo-api-2")
    def demo_api():
        return {}

    @web.get("/demo-web-2")
    def demo_web():
        return {}

    app = FastAPI()
    manifest = FeatureManifest(name="demo", routers=[api], web_routers=[web])
    mount_features(app, manifests=[manifest], disabled=set(), web_enabled=False)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/demo-api-2" in paths
    assert "/demo-web-2" not in paths


def test_mount_features_skips_web_routers_for_disabled_feature_regardless():
    """A feature disabled via `disabled` mounts neither group, whether or not
    `web_enabled` is True — the two switches are independent (F1)."""
    api = APIRouter()
    web = APIRouter()

    @api.get("/demo-api-3")
    def demo_api():
        return {}

    @web.get("/demo-web-3")
    def demo_web():
        return {}

    app = FastAPI()
    manifest = FeatureManifest(
        name="demo", routers=[api], web_routers=[web], core=False
    )
    mount_features(app, manifests=[manifest], disabled={"demo"}, web_enabled=True)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/demo-api-3" not in paths
    assert "/demo-web-3" not in paths
