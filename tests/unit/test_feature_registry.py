from fastapi import APIRouter, FastAPI

from app.core.features import FeatureManifest, load_manifests, mount_features


def test_load_manifests_reads_feature_attribute():
    manifests = load_manifests(["app.features.persons"])
    assert manifests[0].name == "persons"
    assert manifests[0].routers


def test_mount_features_skips_disabled():
    r = APIRouter()

    @r.get("/x")
    def x():
        return {}

    app = FastAPI()
    manifest = FeatureManifest(name="demo", routers=[r], core=False)
    mount_features_from = [manifest]
    mount_features(app, manifests=mount_features_from, disabled={"demo"})
    assert all(getattr(route, "path", "") != "/x" for route in app.routes)


def test_mount_features_mounts_enabled():
    r = APIRouter()

    @r.get("/y")
    def y():
        return {}

    app = FastAPI()
    mount_features(
        app, manifests=[FeatureManifest(name="demo", routers=[r])], disabled=set()
    )
    assert any(getattr(route, "path", "") == "/y" for route in app.routes)
