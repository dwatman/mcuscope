"""The loader and PUT /config/storage share one set of storage bounds (SPEC 3.3), so a value the
file holds never makes a save of another storage field fail."""

from __future__ import annotations

from fastapi.testclient import TestClient

from mcuscope.config import load_config
from mcuscope.server import create_app


def test_a_saved_value_past_the_old_api_bound_can_be_sent_back(tmp_path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        f'[storage]\ndb_path = "{(tmp_path / "cap.db").as_posix()}"\nretention_days = 5000\n'
        "min_sessions = 5000\nmax_db_bytes = 8796093022208\n",
        encoding="utf-8", newline="\n",
    )
    warnings: list[str] = []
    assert load_config(cfg_file, warnings=warnings).storage.retention_days == 5000
    assert warnings == []   # the loader takes all three as written
    app = create_app(load_config(cfg_file), config_path=cfg_file)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)) as c:
        storage = c.get("/config").json()["storage"]
        r = c.put("/config/storage", json={**storage, "auto_session": False})
        assert r.status_code == 200, r.text
        saved = load_config(cfg_file).storage
        assert (saved.retention_days, saved.min_sessions, saved.auto_session) == (
            5000, 5000, False)
        # Positive control: a value past the shared bound is still refused.
        r = c.put("/config/storage", json={**storage, "retention_days": 2**63})
        assert r.status_code == 422

