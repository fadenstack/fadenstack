from pathlib import Path
from unittest.mock import patch

from faden_agent.config import _default_state_path
from faden_agent.state_store import StateStore


def test_state_store_roundtrip(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.state.credential = "abc"
    store.state.node_id = "node-1"
    seq = store.next_seq()
    assert seq == 1
    store.set_workload("runtime-1", {"container_name": "c1"})
    store.remember_command_result("cmd-1", {"success": True, "result": {"ok": True}})

    store2 = StateStore(tmp_path / "state.json")
    assert store2.state.credential == "abc"
    assert store2.state.node_id == "node-1"
    assert store2.state.tx_seq == 1
    assert store2.workload("runtime-1") == {"container_name": "c1"}
    assert store2.get_command_result("cmd-1") == {"success": True, "result": {"ok": True}}


@patch("faden_agent.config.os.getuid", create=True, return_value=0)
@patch("faden_agent.config.sys")
def test_default_state_path_linux_root(mock_sys, _mock_uid) -> None:
    mock_sys.platform = "linux"
    assert _default_state_path() == "/var/lib/faden-agent/state.json"


@patch("faden_agent.config.Path.home", return_value=Path("/home/testuser"))
@patch("faden_agent.config.os.getuid", create=True, return_value=1000)
@patch("faden_agent.config.sys")
def test_default_state_path_linux_non_root(mock_sys, _mock_uid, _mock_home) -> None:
    mock_sys.platform = "linux"
    result = _default_state_path()
    expected = str(Path("/home/testuser/.local/share/faden-agent/state.json"))
    assert result == expected


@patch("faden_agent.config.sys")
@patch.dict("os.environ", {"PROGRAMDATA": r"C:\ProgramData"})
def test_default_state_path_windows(mock_sys) -> None:
    mock_sys.platform = "win32"
    result = _default_state_path()
    assert "faden-agent" in result
    assert result.endswith("state.json")
    assert "ProgramData" in result


def test_an_old_instance_never_overwrites_a_credential_another_process_wrote(tmp_path: Path, caplog) -> None:
    path = tmp_path / "state.json"
    old = StateStore(path)
    old.state.credential, old.state.node_id = "old-cred", "old-node"
    old.save()

    new = StateStore(path)  # the join process
    new.state.credential, new.state.node_id = "new-cred", "new-node"
    new.save()

    old.state.tx_seq = 42
    with caplog.at_level("WARNING"):
        old.flush_seq()
    assert "another process" in caplog.text

    on_disk = StateStore(path).state
    assert (on_disk.credential, on_disk.node_id) == ("new-cred", "new-node")
    assert on_disk.tx_seq == 42, "the rest of the state is still written"
    assert (old.state.credential, old.state.node_id) == ("new-cred", "new-node")


def test_an_instance_still_persists_its_own_credential_changes(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.state.credential, store.state.node_id = "c1", "n1"
    store.save()
    assert StateStore(path).state.credential == "c1"

    store.state.credential = None  # "clearing credential" after a 403
    store.save()
    assert StateStore(path).state.credential is None

    store.state.credential, store.state.node_id = "c2", "n2"  # re-enrolment
    store.save()
    reread = StateStore(path).state
    assert (reread.credential, reread.node_id) == ("c2", "n2")


def test_a_credential_cleared_after_loading_is_written_not_mistaken_for_foreign(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    first = StateStore(path)
    first.state.credential = "c1"
    first.save()
    second = StateStore(path)  # loads c1
    second.state.credential = None
    second.save()
    assert StateStore(path).state.credential is None
