"""tools/check_dist.py's artifact check, which CI and the release workflow run on what they build.

Driven on synthetic artifacts built from this tree's web UI, so each refusal is shown against
an otherwise complete wheel and sdist.
"""

from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

HOST = Path(__file__).resolve().parents[1]
TOOL = HOST.parent / "tools" / "check_dist.py"
pytestmark = pytest.mark.skipif(not TOOL.exists(), reason="tools/ is not in this tree (sdist)")


def _tool():
    spec = importlib.util.spec_from_file_location("check_dist", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _webui() -> dict[str, bytes]:
    root = HOST / "mcuscope" / "webui"
    return {
        "mcuscope/webui/" + p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    }


def _build(dist: Path, wheel: dict[str, bytes], sdist: dict[str, bytes]) -> None:
    dist.mkdir()
    with zipfile.ZipFile(dist / "mcuscope-0-py3-none-any.whl", "w") as zf:
        for name, data in wheel.items():
            zf.writestr(name, data)
    with tarfile.open(dist / "mcuscope-0.tar.gz", "w:gz") as tf:
        for name, data in sdist.items():
            info = tarfile.TarInfo(f"mcuscope-0/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def test_complete_artifacts_pass(tmp_path):
    files = _webui()
    _build(tmp_path / "dist", files, files)
    assert _tool().check_artifacts(tmp_path / "dist", HOST) == []


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
def test_a_missing_webui_file_is_named(tmp_path, artifact):
    files = _webui()
    short = {k: v for k, v in files.items() if k != "mcuscope/webui/app.js"}
    _build(tmp_path / "dist", short if artifact == "wheel" else files,
           short if artifact == "sdist" else files)
    assert _tool().check_artifacts(tmp_path / "dist", HOST) == [
        f"{artifact} is missing mcuscope/webui/app.js"
    ]


@pytest.mark.parametrize("artifact", ["wheel", "sdist"])
def test_an_emptied_webui_file_is_named(tmp_path, artifact):
    files = _webui()
    emptied = dict(files, **{"mcuscope/webui/style.css": b""})
    _build(tmp_path / "dist", emptied if artifact == "wheel" else files,
           emptied if artifact == "sdist" else files)
    assert _tool().check_artifacts(tmp_path / "dist", HOST) == [
        f"{artifact} ships mcuscope/webui/style.css as an empty file"
    ]


def test_two_wheels_are_refused(tmp_path):
    files = _webui()
    _build(tmp_path / "dist", files, files)
    (tmp_path / "dist" / "mcuscope-1-py3-none-any.whl").write_bytes(
        (tmp_path / "dist" / "mcuscope-0-py3-none-any.whl").read_bytes())
    failures = _tool().check_artifacts(tmp_path / "dist", HOST)
    assert failures[0].startswith("expected exactly 1 wheel in ") and failures[0].endswith(
        "found 2"), failures


def test_a_source_tree_without_the_webui_is_not_a_vacuous_pass(tmp_path):
    (tmp_path / "src" / "mcuscope" / "webui").mkdir(parents=True)
    _build(tmp_path / "dist", {}, {})
    failures = _tool().check_artifacts(tmp_path / "dist", tmp_path / "src")
    assert "source tree is missing sentinel mcuscope/webui/index.html" in failures
    assert "only 0 webui files found in the source tree" in failures
