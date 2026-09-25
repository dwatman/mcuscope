"""contrib/config.example.toml loads clean and shows every section the loader reads."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import tomlkit

from mcuscope.config import Config, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "contrib" / "config.example.toml"


def test_the_example_config_loads_without_warnings():
    warnings: list[str] = []
    cfg = load_config(EXAMPLE, warnings=warnings)
    assert warnings == []
    # The file was read, not defaulted: defaults have no ports.
    assert [(p.alias, p.device) for p in cfg.ports] == [("board", "socket://127.0.0.1:9900")]


def test_the_example_config_shows_every_section():
    sections = {f.name for f in dataclasses.fields(Config)} - {"base_dir"}
    assert set(tomlkit.parse(EXAMPLE.read_text(encoding="utf-8"))) == sections


def test_a_misspelt_key_would_be_warned_about(tmp_path):
    # Positive control for the no-warnings assertion: the loader does warn on this file's kind.
    bad = tmp_path / "config.toml"
    bad.write_text(EXAMPLE.read_text(encoding="utf-8").replace("retention_days", "retension_days"),
                   encoding="utf-8", newline="\n")
    warnings: list[str] = []
    load_config(bad, warnings=warnings)
    assert any("retension_days" in w for w in warnings), warnings
