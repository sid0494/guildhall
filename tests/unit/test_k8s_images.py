"""The kind cluster runs the same third-party images as compose.

Dependabot updates the pins in compose.yaml only; this fails until the
Kubernetes manifests name the same version, so a bump reaches both
(docs/adr/0078).
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = re.compile(r"^\s*image:\s*(\S+)\s*$", re.MULTILINE)


def images(*files: Path) -> dict[str, str]:
    """Each image's full reference, keyed by its name without tag or digest."""
    found: dict[str, str] = {}
    for file in files:
        for reference in IMAGE.findall(file.read_text(encoding="utf-8")):
            found[reference.split("@")[0].rsplit(":", 1)[0]] = reference
    return found


COMPOSE = images(ROOT / "compose.yaml")
MANIFESTS = images(*sorted((ROOT / "k8s").rglob("*.yaml")))


@pytest.mark.parametrize("name", sorted(MANIFESTS.keys() & COMPOSE.keys()))
def test_manifest_image_matches_compose(name: str) -> None:
    assert MANIFESTS[name] == COMPOSE[name], (
        f"update {name} in k8s/ to match compose.yaml"
    )


def test_infrastructure_images_are_compared() -> None:
    """Guards the comparison itself: an unparsed manifest would compare nothing."""
    assert {"postgres", "redis", "motoserver/moto"} <= MANIFESTS.keys() & COMPOSE.keys()
