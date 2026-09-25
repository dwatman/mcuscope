"""Saving the ports list keeps what the model does not carry (SPEC 3.3.1, REVIEW class 45):
unknown keys and comments inside a [[ports]] table survive a save of that port."""

from __future__ import annotations

import tomlkit

from mcuscope.config import PortConfig, load_config, save_ports

SRC = """[server]
port = 8558

# the bench board
[[ports]]
alias = "board"   # main one
device = "/dev/ttyACM0"
# read by a newer version
future_port_key = 7
baud = 115200

[[ports]]
alias = "gone"
device = "/dev/ttyUSB9"

[[ports]]
alias = "other"
serial_number = "ABC"
baud = 9600
identify = false
"""


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def test_a_round_trip_keeps_unknown_port_keys_and_comments(tmp_path) -> None:
    path = tmp_path / "config.toml"
    _write(path, SRC)
    before = load_config(path).ports
    save_ports(path, before)
    text = path.read_text(encoding="utf-8")
    assert "future_port_key = 7" in text
    assert "# read by a newer version" in text and '"board"   # main one' in text
    assert "# the bench board" in text and "port = 8558" in text
    assert load_config(path).ports == before


def test_an_edit_keeps_the_table_while_removals_and_additions_apply(tmp_path) -> None:
    path = tmp_path / "config.toml"
    _write(path, SRC)
    ports = {pc.alias: pc for pc in load_config(path).ports}
    ports["board"].baud = 9600
    ports["other"].device, ports["other"].serial_number = "/dev/ttyACM1", None
    ports["other"].identify = True
    new = PortConfig(alias="new", device="COM3", eol="crlf")
    save_ports(path, [ports["other"], ports["board"], new])
    data = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()["ports"]
    assert [t["alias"] for t in data] == ["other", "board", "new"]   # the list's order
    other, board, added = data
    assert board == {"alias": "board", "device": "/dev/ttyACM0", "future_port_key": 7,
                     "baud": 9600, "autoconnect": True}
    # A cleared field and a default the writer leaves implicit both lose their key.
    assert other == {"alias": "other", "device": "/dev/ttyACM1", "baud": 9600,
                     "autoconnect": True}
    assert added == {"alias": "new", "device": "COM3", "baud": 115200, "autoconnect": True,
                     "eol": "crlf"}


def test_a_duplicate_alias_keeps_the_table_the_loader_read(tmp_path) -> None:
    path = tmp_path / "config.toml"
    _write(path, '[[ports]]\nalias = "b"\ndevice = "/dev/a"\nfirst = 1\n\n'
                 '[[ports]]\nalias = "b"\ndevice = "/dev/b"\nlast = 1\n')
    ports = load_config(path, warnings=[]).ports
    assert [pc.device for pc in ports] == ["/dev/b"]   # the loader keeps the last entry
    save_ports(path, ports)
    assert tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()["ports"] == [
        {"alias": "b", "device": "/dev/b", "last": 1, "baud": 115200, "autoconnect": True}
    ]


def test_an_empty_list_drops_the_key_and_a_non_table_shape_is_replaced(tmp_path) -> None:
    path = tmp_path / "config.toml"
    _write(path, SRC)
    save_ports(path, [])
    assert "ports" not in tomlkit.parse(path.read_text(encoding="utf-8"))
    _write(path, "ports = 3\n")
    save_ports(path, [PortConfig(alias="a", device="/dev/x")])
    assert tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()["ports"] == [
        {"alias": "a", "device": "/dev/x", "baud": 115200, "autoconnect": True}
    ]
