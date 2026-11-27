"""The Mac service: its account, files, launchd job, upgrade and uninstall.

A stand-in runner plays dscl, dseditgroup, launchctl, chown and pkill, and
every path lives under a temporary root, so nothing here touches a real
account, job or system directory.
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path

import pytest

from faden_agent import darwin_service as ds
from faden_agent.darwin_service import Layout, Result

VERSION = "0.2.12"
#: The tests cannot chown, so "root" and the service account are this user: what is
#: checked is that root's code never reaches into the service's directory.
ME, MY_GROUP = os.getuid(), os.getgid()


def _svc() -> ds.Identity:
    return ds.Identity(ds.SERVICE_USER, ME, MY_GROUP)


class FakeMac:
    """A directory service and a launchd, as far as darwin_service uses them."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, str]] = {"_oahd": {"UniqueID": "441", "PrimaryGroupID": "441"}}
        self.groups: dict[str, dict[str, str]] = {"com.apple.access_remote_ae": {"PrimaryGroupID": "400"}}
        self.directory_names: set[str] = set()
        self.member_of: set[str] = set()
        self.calls: list[list[str]] = []
        self.fail_on: tuple[str, ...] | None = None
        self.unwritable = False
        # launchd
        self.loaded = False
        self.healthy_builds: set[str] | None = None  # None: every build is healthy
        self.layout: Layout | None = None
        self.pid = 0
        self.runs = 0
        self.last_exit: int | None = None
        self.version_out = f"faden-agent {VERSION}"
        self.bootstrap_fails_for: set[str] = set()
        self.search_fails = False

    # what darwin_service sees
    def run(self, argv, *, uid=None, gid=None) -> Result:  # noqa: ANN001
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if self.fail_on and tuple(argv[: len(self.fail_on)]) == self.fail_on:
            return Result(1, "", "eDSPermissionError")
        prog = argv[0]
        if prog == ds.DSCL:
            return self._dscl(argv[1:])
        if prog == ds.DSEDITGROUP:
            return Result(0 if argv[-1] in self.member_of else 1)
        if prog == ds.LAUNCHCTL:
            return self._launchctl(argv[1:])
        if prog in (ds.TOUCH, ds.TEST):
            return Result(1 if self.unwritable else 0)
        if prog == ds.ID:
            return Result(0, "_fadenagent everyone localaccounts com.apple.sharepoint.group.1 _lpoperator\n")
        if prog == ds.PGREP:
            return Result(1)
        if argv[1:] == ["--version"]:
            return Result(0, self.version_out + "\n")
        return Result(0)

    def _dscl(self, args: list[str]) -> Result:
        node, verb = args[0], args[1]
        if verb == "-read":
            collection, name = args[2].strip("/").split("/")
            table = self.users if collection == "Users" else self.groups
            if name not in table:
                return Result(56, "", "Data source (/Search) is not valid.")
            attr = args[3]
            value = name if attr == "RecordName" else table[name].get(attr)
            return Result(0, f"{attr}: {value}\n" if value is not None else "")
        if verb == "-search":
            if self.search_fails:
                return Result(70, "", "eDSNodeNotFound")
            collection, attr, value = args[2].strip("/"), args[3], args[4]
            if attr == "RecordName":
                return Result(0, f"{value}\t\tRecordName = (\n    {value}\n)\n" if value in self.directory_names else "")
            table = self.users if collection == "Users" else self.groups
            hits = [n for n, rec in table.items() if rec.get(attr) == value]
            return Result(0, "".join(f"{n}\t\t{attr} = (\n    {value}\n)\n" for n in hits))
        if verb in ("-create", "-delete"):
            assert node == "."
            collection, name = args[2].strip("/").split("/")
            table = self.users if collection == "Users" else self.groups
            if verb == "-delete":
                table.pop(name, None)
            else:
                rec = table.setdefault(name, {})
                if len(args) > 3:
                    rec[args[3]] = args[4]
            return Result(0)
        raise AssertionError(args)

    def _build_now(self) -> str | None:
        assert self.layout is not None
        try:
            return os.readlink(self.layout.current)
        except OSError:
            return None

    def _launchctl(self, args: list[str]) -> Result:
        verb = args[0]
        if verb == "print":
            if not self.loaded:
                return Result(113, "", "Could not find service")
            healthy = self.healthy_builds is None or self._build_now() in self.healthy_builds
            if not healthy:  # crash-looping: a new process each look, a failed exit
                self.runs += 1
                self.pid += 1
                self.last_exit = 1
            exit_text = "(never exited)" if self.last_exit is None else str(self.last_exit)
            return Result(0, f"system/{ds.LABEL} = {{\n\tpid = {self.pid}\n\truns = {self.runs}\n"
                             f"\tlast exit code = {exit_text}\n}}\n")
        if verb == "bootstrap":
            if self._build_now() in self.bootstrap_fails_for:
                return Result(5, "", "Bootstrap failed: 5: Input/output error")
            self.loaded, self.pid, self.runs, self.last_exit = True, 1000, 1, None
            return Result(0)
        if verb == "bootout":
            self.loaded = False
            return Result(0)
        return Result(0)

    def verbs(self, prog: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == prog]


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture()
def mac(tmp_path: Path) -> FakeMac:
    fake = FakeMac()
    fake.layout = Layout(tmp_path / "root", root_uid=ME)
    return fake


@pytest.fixture()
def layout(mac: FakeMac) -> Layout:
    assert mac.layout is not None
    return mac.layout


def _install_account(mac: FakeMac, layout: Layout, number: int = 452) -> None:
    mac.users[ds.SERVICE_USER] = {
        "UniqueID": str(number), "PrimaryGroupID": str(number), "UserShell": "/usr/bin/false",
        "IsHidden": "1", "NFSHomeDirectory": layout.real(layout.state_dir),
    }
    mac.groups[ds.SERVICE_USER] = {"PrimaryGroupID": str(number)}
    layout.marker.parent.mkdir(parents=True, exist_ok=True)
    layout.marker.write_text(f"name={ds.SERVICE_USER}\nid={number}\n")


# ── the account ───────────────────────────────────────────────────


class TestTheAccount:
    def test_a_new_account_is_hidden_unprivileged_and_takes_the_first_free_id(
        self, mac: FakeMac, layout: Layout
    ) -> None:
        mac.users["someone"] = {"UniqueID": "450"}
        mac.groups["something"] = {"PrimaryGroupID": "451"}
        identity = ds.ensure_account(mac, layout, VERSION, environ={})
        assert (identity.uid, identity.gid) == (452, 452)
        user = mac.users[ds.SERVICE_USER]
        assert user["UniqueID"] == user["PrimaryGroupID"] == "452"
        assert user["IsHidden"] == "1"
        assert user["UserShell"] == "/usr/bin/false"
        assert user["Password"] == "*"
        assert user["NFSHomeDirectory"] == "/var/db/fadenstack/agent"
        assert mac.groups[ds.SERVICE_USER]["PrimaryGroupID"] == "452"
        assert ds.read_marker(layout)["id"] == "452"
        # recorded, not refused: inherited through nested groups
        assert "com.apple.sharepoint.group.1" in identity.groups

    def test_the_marker_is_written_before_the_account_exists(self, mac: FakeMac, layout: Layout) -> None:
        seen: list[bool] = []
        original = mac._dscl

        def spy(args: list[str]) -> Result:
            if args[1] == "-create":
                seen.append(layout.marker.exists())
            return original(args)

        mac._dscl = spy  # type: ignore[method-assign]
        ds.ensure_account(mac, layout, VERSION, environ={})
        assert seen and all(seen)

    def test_a_fleet_range_is_honoured(self, mac: FakeMac, layout: Layout) -> None:
        identity = ds.ensure_account(mac, layout, VERSION, environ={ds.ID_RANGE_ENV: "420-425"})
        assert identity.uid == 420

    @pytest.mark.parametrize("bad", ["450", "500-510", "460-450", "100-120"])
    def test_a_malformed_range_is_refused(self, bad: str) -> None:
        with pytest.raises(ds.DarwinServiceError):
            ds.id_range({ds.ID_RANGE_ENV: bad})

    def test_a_full_range_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        for n in range(450, 500):
            mac.users[f"u{n}"] = {"UniqueID": str(n)}
        with pytest.raises(ds.DarwinServiceError, match="No UID/GID free"):
            ds.ensure_account(mac, layout, VERSION, environ={})
        assert ds.SERVICE_USER not in mac.users

    def test_an_account_fadenstack_did_not_create_is_never_used_or_changed(
        self, mac: FakeMac, layout: Layout
    ) -> None:
        mac.users[ds.SERVICE_USER] = {"UniqueID": "470", "PrimaryGroupID": "470"}
        with pytest.raises(ds.DarwinServiceError, match="did not create it"):
            ds.ensure_account(mac, layout, VERSION, environ={})
        assert ["-create"] not in [c[2:3] for c in mac.calls if c[0] == ds.DSCL]
        assert not any("-create" in c or "-delete" in c for c in mac.calls)

    def test_a_directory_account_of_that_name_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        mac.directory_names.add(ds.SERVICE_USER)
        with pytest.raises(ds.DarwinServiceError, match="directory account"):
            ds.ensure_account(mac, layout, VERSION, environ={})

    def test_an_upgrade_reuses_the_recorded_account(self, mac: FakeMac, layout: Layout) -> None:
        _install_account(mac, layout, 455)
        identity = ds.ensure_account(mac, layout, VERSION, environ={})
        assert identity.uid == 455
        assert not any("-create" in c for c in mac.calls)

    def test_an_account_whose_id_changed_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        _install_account(mac, layout, 455)
        mac.users[ds.SERVICE_USER]["UniqueID"] = "456"
        with pytest.raises(ds.DarwinServiceError, match="created it as 455"):
            ds.ensure_account(mac, layout, VERSION, environ={})

    def test_an_id_claimed_by_a_later_macos_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        _install_account(mac, layout, 455)
        mac.users["_newapple"] = {"UniqueID": "455"}
        with pytest.raises(ds.DarwinServiceError, match="also used by _newapple"):
            ds.ensure_account(mac, layout, VERSION, environ={})

    def test_an_account_with_a_login_shell_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        _install_account(mac, layout, 455)
        mac.users[ds.SERVICE_USER]["UserShell"] = "/bin/zsh"
        with pytest.raises(ds.DarwinServiceError, match="UserShell"):
            ds.ensure_account(mac, layout, VERSION, environ={})

    @pytest.mark.parametrize("group", ["admin", "staff", "com.apple.access_ssh"])
    def test_membership_of_a_privileged_group_is_refused(self, mac: FakeMac, layout: Layout, group: str) -> None:
        mac.member_of.add(group)
        with pytest.raises(ds.DarwinServiceError, match=group):
            ds.ensure_account(mac, layout, VERSION, environ={})

    def test_a_creation_that_fails_halfway_takes_back_what_it_made(self, mac: FakeMac, layout: Layout) -> None:
        mac.fail_on = (ds.DSCL, ".", "-create", f"/Users/{ds.SERVICE_USER}", "IsHidden")
        with pytest.raises(ds.DarwinServiceError):
            ds.ensure_account(mac, layout, VERSION, environ={})
        assert ds.SERVICE_USER not in mac.users
        assert ds.SERVICE_USER not in mac.groups
        assert not layout.marker.exists()

    def test_a_marker_left_without_an_account_is_replaced(self, mac: FakeMac, layout: Layout) -> None:
        layout.marker.parent.mkdir(parents=True)
        layout.marker.write_text("name=_fadenagent\nid=499\n")
        identity = ds.ensure_account(mac, layout, VERSION, environ={})
        assert identity.uid == 450
        assert ds.read_marker(layout)["id"] == "450"


# ── files and the job definition ──────────────────────────────────


def _chowns(mac: FakeMac) -> list[list[str]]:
    return [c for c in mac.calls if c[0] == ds.CHOWN]


class TestFiles:
    def test_a_fresh_state_dir_is_private_to_the_service_and_the_rest_is_roots(
        self, mac: FakeMac, layout: Layout
    ) -> None:
        ds.prepare_files(mac, layout, _svc())
        assert oct(layout.state_dir.stat().st_mode & 0o777) == "0o700"
        assert oct(layout.models.stat().st_mode & 0o777) == "0o700"
        assert oct(layout.library.stat().st_mode & 0o777) == "0o755"
        assert oct(layout.log_file.stat().st_mode & 0o777) == "0o640"
        owner = f"{ME}:{MY_GROUP}"
        chowns = _chowns(mac)
        # the two directories themselves, the models one first, and nothing recursive
        assert chowns.index([ds.CHOWN, "-h", owner, str(layout.models)]) < chowns.index(
            [ds.CHOWN, "-h", owner, str(layout.state_dir)]
        )
        assert all(c[1] == "-h" and "-R" not in c for c in chowns)
        assert not any(str(layout.library) in " ".join(c) for c in chowns)

    def test_an_existing_state_dir_is_only_checked(self, mac: FakeMac, layout: Layout) -> None:
        ds.prepare_files(mac, layout, _svc())
        (layout.state_dir / "state.json").write_text("{}")
        mac.calls.clear()
        ds.prepare_files(mac, layout, _svc())
        assert not any(str(layout.state_dir) in " ".join(c) for c in _chowns(mac))

    def test_a_hardlink_planted_in_the_state_dir_is_never_touched(self, mac: FakeMac, layout: Layout,
                                                                  tmp_path: Path) -> None:
        """macOS lets any user hardlink a root-owned file; a recursive chown would hand it over."""
        identity = _svc()
        ds.prepare_files(mac, layout, identity)
        clock = Clock()
        _install_account(mac, layout, 452)
        ds.activate(mac, layout, identity, _binary(tmp_path, b"build"), VERSION,
                    say=lambda _m: None, sleep=clock.sleep, clock=clock)
        binary = layout.builds / ds.current_build(layout) / "faden-agent"  # type: ignore[operator]
        os.link(binary, layout.state_dir / "x")
        os.link(layout.plist, layout.state_dir / "y")
        planted = (layout.state_dir / "x", layout.state_dir / "y")
        before = [(os.lstat(p).st_mode, os.lstat(p).st_uid) for p in planted]
        mac.calls.clear()
        ds.prepare_files(mac, layout, identity)
        ds.activate(mac, layout, identity, _binary(tmp_path, b"build"), VERSION,
                    say=lambda _m: None, sleep=clock.sleep, clock=clock)
        touched = " ".join(" ".join(c) for c in _chowns(mac))
        assert str(layout.state_dir) not in touched
        # the planted inodes keep their owner and mode (root's binary and plist, on a Mac)
        assert [(os.lstat(p).st_mode, os.lstat(p).st_uid) for p in planted] == before
        # and the files root uses now are not the planted inodes: rewrites replace, never write through
        assert os.lstat(layout.plist).st_ino != os.lstat(planted[1]).st_ino

    def test_a_state_dir_replaced_by_a_symlink_is_refused_and_not_followed(
        self, mac: FakeMac, layout: Layout, tmp_path: Path
    ) -> None:
        target = tmp_path / "elsewhere"
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
        layout.state_dir.parent.mkdir(parents=True)
        layout.state_dir.symlink_to(target)
        with pytest.raises(ds.DarwinServiceError, match="private directory"):
            ds.prepare_files(mac, layout, _svc())
        assert oct(target.stat().st_mode & 0o777) == "0o755"
        assert not any(str(layout.state_dir) in " ".join(c) for c in _chowns(mac))

    def test_a_models_symlink_inside_an_existing_state_dir_is_not_followed(
        self, mac: FakeMac, layout: Layout, tmp_path: Path
    ) -> None:
        ds.prepare_files(mac, layout, _svc())
        target = tmp_path / "root-dir"
        target.mkdir()
        os.chmod(target, 0o755)
        layout.models.rmdir()
        layout.models.symlink_to(target)
        ds.prepare_files(mac, layout, _svc())
        assert oct(target.stat().st_mode & 0o777) == "0o755"

    def test_a_state_dir_open_to_others_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        ds.prepare_files(mac, layout, _svc())
        os.chmod(layout.state_dir, 0o755)
        with pytest.raises(ds.DarwinServiceError, match="private directory"):
            ds.prepare_files(mac, layout, _svc())

    @pytest.mark.parametrize("plant", ["symlink", "hardlink"])
    def test_a_planted_log_file_is_refused(self, mac: FakeMac, layout: Layout, tmp_path: Path, plant: str) -> None:
        victim = tmp_path / "victim"
        victim.write_text("root's")
        layout.log_dir.mkdir(parents=True)
        os.chmod(layout.log_dir, 0o755)
        if plant == "symlink":
            layout.log_file.symlink_to(victim)
        else:
            os.link(victim, layout.log_file)
        with pytest.raises(ds.DarwinServiceError, match="plain log file"):
            ds.prepare_files(mac, layout, _svc())
        assert not any(str(layout.log_file) in " ".join(c) for c in _chowns(mac))

    def test_a_root_directory_others_can_write_is_refused(self, mac: FakeMac, layout: Layout) -> None:
        layout.library.mkdir(parents=True)
        os.chmod(layout.library, 0o775)
        with pytest.raises(ds.DarwinServiceError, match="owned by root and writable only by it"):
            ds.prepare_files(mac, layout, _svc())

    def test_a_root_directory_that_is_a_symlink_is_refused(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        (tmp_path / "real").mkdir()
        layout.library.parent.mkdir(parents=True)
        layout.library.symlink_to(tmp_path / "real")
        with pytest.raises(ds.DarwinServiceError, match="not a directory owned by root"):
            ds.prepare_files(mac, layout, _svc())

    def test_the_logs_are_rotated(self, mac: FakeMac, layout: Layout) -> None:
        ds.prepare_files(mac, layout, _svc())
        conf = layout.newsyslog.read_text()
        assert "/var/log/fadenstack/agent.log  _fadenagent:_fadenagent  640" in conf
        assert oct(layout.newsyslog.stat().st_mode & 0o777) == "0o644"

    def test_the_probe_is_made_and_removed_by_the_service(self, mac: FakeMac, layout: Layout) -> None:
        ds.prepare_files(mac, layout, _svc())
        mac.calls.clear()
        ds.probe_state_dir(mac, layout, _svc())
        assert [c[0] for c in mac.calls] == [ds.TOUCH, ds.TEST, ds.RM]

    def test_a_state_dir_the_service_cannot_write_stops_the_join(self, mac: FakeMac, layout: Layout) -> None:
        ds.prepare_files(mac, layout, _svc())
        mac.unwritable = True
        with pytest.raises(ds.DarwinServiceError, match="Nothing was filed"):
            ds.probe_state_dir(mac, layout, _svc())

    def test_the_config_is_never_readable_by_others(
        self, mac: FakeMac, layout: Layout, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It can hold an enrollment token: 0600 from creation, 0640 only once it is the service's group."""
        _install_account(mac, layout, 452)
        ds.prepare_files(mac, layout, _svc())
        modes: list[int] = []
        real_open, real_chmod = ds.os.open, ds.os.chmod

        def opening(path, flags, mode=0o777, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
            if "agent.env" in str(path):
                modes.append(mode)
            return real_open(path, flags, mode, *a, **k)

        def chmodding(path, mode, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
            if "agent.env" in str(path):
                assert [ds.CHOWN, "-h", "0:452"] == [c for c in _chowns(mac) if "agent.env" in c[-1]][-1][:3]
                modes.append(mode)
            return real_chmod(path, mode, *a, **k)

        monkeypatch.setattr(ds.os, "open", opening)
        monkeypatch.setattr(ds.os, "chmod", chmodding)
        ds.write_env_file(mac, layout, "FADEN_NODE_AGENT_ENROLLMENT_TOKEN=tok\n")
        assert modes == [0o600, 0o640]
        assert oct(layout.env_file.stat().st_mode & 0o777) == "0o640"
        assert all(m & 0o007 == 0 for m in modes)


class TestThePlist:
    def test_is_the_reviewed_job_and_never_sets_number_of_files(self, layout: Layout) -> None:
        job = plistlib.loads(ds.plist_content(layout, ds.Identity(ds.SERVICE_USER, 452, 452)))
        assert job["Label"] == "com.fadenstack.agent"
        assert job["UserName"] == job["GroupName"] == "_fadenagent"
        assert job["ProgramArguments"] == ["/Library/Fadenstack/agent/current/faden-agent", "run"]
        assert job["KeepAlive"] is True
        assert job["ThrottleInterval"] == 15
        assert job["ExitTimeOut"] == 45
        assert job["WorkingDirectory"] == job["EnvironmentVariables"]["HOME"] == "/var/db/fadenstack/agent"
        assert job["EnvironmentVariables"]["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"
        assert job["EnvironmentVariables"]["FADEN_NODE_AGENT_LOG_FILE"] == "/var/log/fadenstack/agent.log"
        assert "NumberOfFiles" not in str(job)
        assert "SoftResourceLimits" not in job and "HardResourceLimits" not in job


class TestDirectorySearch:
    def test_a_failing_search_is_an_error_not_a_free_id(self, mac: FakeMac, layout: Layout) -> None:
        mac.search_fails = True
        with pytest.raises(ds.DarwinServiceError, match="Could not search"):
            ds.allocate_id(mac, 450, 499)

    def test_a_users_primary_group_number_is_not_free(self, mac: FakeMac) -> None:
        mac.users["someone"] = {"UniqueID": "600", "PrimaryGroupID": "450"}
        assert ds.allocate_id(mac, 450, 499) == 451


# ── activation, upgrade and rollback ──────────────────────────────


def _binary(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / f"src-{len(content)}-{content[:4].hex()}" / "faden-agent"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _ready(mac: FakeMac, layout: Layout) -> ds.Identity:
    _install_account(mac, layout, 452)
    identity = _svc()
    ds.prepare_files(mac, layout, identity)
    return identity


class TestActivation:
    def test_a_first_start_stages_links_and_bootstraps_once(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        identity = _ready(mac, layout)
        clock = Clock()
        ds.activate(mac, layout, identity, _binary(tmp_path, b"build one"), VERSION,
                    say=lambda _m: None, sleep=clock.sleep, clock=clock)
        build = ds.current_build(layout)
        assert build is not None and build.startswith(f"{VERSION}-")
        assert (layout.builds / build / "faden-agent").read_bytes() == b"build one"
        assert os.readlink(layout.cli_link) == "/Library/Fadenstack/agent/current/faden-agent"
        assert oct(layout.plist.stat().st_mode & 0o777) == "0o644"
        assert mac.verbs(ds.LAUNCHCTL).count("bootstrap") == 1
        assert clock.t >= ds.HEALTHY_FOR_SECONDS

    def test_the_cli_copy_of_an_older_install_becomes_a_link(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        identity = _ready(mac, layout)
        layout.cli_link.parent.mkdir(parents=True)
        layout.cli_link.write_bytes(b"old copy")
        clock = Clock()
        ds.activate(mac, layout, identity, _binary(tmp_path, b"b"), VERSION,
                    say=lambda _m: None, sleep=clock.sleep, clock=clock)
        assert layout.cli_link.is_symlink()

    def test_a_build_that_does_not_run_as_this_version_is_not_started_nor_kept(
        self, mac: FakeMac, layout: Layout, tmp_path: Path
    ) -> None:
        identity = _ready(mac, layout)
        mac.version_out = "faden-agent 0.0.1"
        source = _binary(tmp_path, b"b")
        with pytest.raises(ds.DarwinServiceError, match="does not run as agent"):
            ds.activate(mac, layout, identity, source, VERSION, say=lambda _m: None)
        assert "bootstrap" not in mac.verbs(ds.LAUNCHCTL)
        assert [p.name for p in layout.builds.iterdir()] == []
        # a retry checks again rather than trusting what is there
        with pytest.raises(ds.DarwinServiceError, match="does not run as agent"):
            ds.stage_binary(mac, layout, source, VERSION)

    def test_a_staged_build_is_checked_again_on_every_use(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        _ready(mac, layout)
        source = _binary(tmp_path, b"b")
        ds.stage_binary(mac, layout, source, VERSION)
        ds.stage_binary(mac, layout, source, VERSION)
        assert sum(1 for c in mac.calls if c[1:] == ["--version"]) == 2

    def test_an_upgrade_that_crash_loops_is_rolled_back(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        identity = _ready(mac, layout)
        clock = Clock()
        quiet = {"say": lambda _m: None, "sleep": clock.sleep, "clock": clock}
        ds.activate(mac, layout, identity, _binary(tmp_path, b"good"), VERSION, **quiet)
        good = ds.current_build(layout)
        mac.healthy_builds = {good}
        with pytest.raises(ds.DarwinServiceError, match=f"Rolled back to {good}, which is running again"):
            ds.activate(mac, layout, identity, _binary(tmp_path, b"bad build"), VERSION, **quiet)
        assert ds.current_build(layout) == good
        assert mac.loaded

    def test_an_upgrade_launchd_will_not_load_is_rolled_back(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        identity = _ready(mac, layout)
        clock = Clock()
        quiet = {"say": lambda _m: None, "sleep": clock.sleep, "clock": clock}
        ds.activate(mac, layout, identity, _binary(tmp_path, b"good"), VERSION, **quiet)
        good = ds.current_build(layout)
        bad_source = _binary(tmp_path, b"unloadable")
        bad = f"{VERSION}-{ds._sha256(bad_source)[:12]}"
        mac.bootstrap_fails_for = {bad}
        with pytest.raises(ds.DarwinServiceError, match=f"could not be loaded.*Rolled back to {good}, which is running again"):
            ds.activate(mac, layout, identity, bad_source, VERSION, **quiet)
        assert ds.current_build(layout) == good
        assert mac.loaded

    def test_a_good_upgrade_keeps_the_previous_build_and_no_older(
        self, mac: FakeMac, layout: Layout, tmp_path: Path
    ) -> None:
        identity = _ready(mac, layout)
        clock = Clock()
        quiet = {"say": lambda _m: None, "sleep": clock.sleep, "clock": clock}
        names = []
        for content in (b"one", b"two!", b"three"):
            ds.activate(mac, layout, identity, _binary(tmp_path, content), VERSION, **quiet)
            names.append(ds.current_build(layout))
        kept = sorted(p.name for p in layout.builds.iterdir() if p.name != "current")
        assert kept == sorted(names[1:])


class TestHealth:
    def _state(self, mac: FakeMac, script: list[tuple[int | None, int, int | None]]):  # noqa: ANN202
        it = iter(script)
        last = script[-1]

        def run(argv, **_kw):  # noqa: ANN001, ANN202
            pid, runs, code = next(it, last)
            exit_text = "(never exited)" if code is None else str(code)
            pid_line = f"\tpid = {pid}\n" if pid else ""
            return Result(0, f"{pid_line}\truns = {runs}\n\tlast exit code = {exit_text}\n")

        mac.run = run  # type: ignore[method-assign]

    def test_a_steady_process_is_healthy(self, mac: FakeMac) -> None:
        self._state(mac, [(7, 1, None)])
        clock = Clock()
        assert ds.wait_healthy(mac, sleep=clock.sleep, clock=clock)[0]

    def test_a_new_pid_is_not(self, mac: FakeMac) -> None:
        self._state(mac, [(7, 1, None)] * 5 + [(8, 1, None)])
        clock = Clock()
        assert not ds.wait_healthy(mac, sleep=clock.sleep, clock=clock)[0]

    def test_a_rising_run_count_is_not(self, mac: FakeMac) -> None:
        self._state(mac, [(7, 1, None)] * 3 + [(7, 2, None)])
        clock = Clock()
        ok, why = ds.wait_healthy(mac, sleep=clock.sleep, clock=clock)
        assert not ok and "restarted" in why

    def test_a_failed_exit_is_not(self, mac: FakeMac) -> None:
        self._state(mac, [(7, 1, 78)])
        clock = Clock()
        assert not ds.wait_healthy(mac, sleep=clock.sleep, clock=clock)[0]

    def test_no_process_at_all_is_not(self, mac: FakeMac) -> None:
        self._state(mac, [(None, 3, 1)])
        clock = Clock()
        ok, why = ds.wait_healthy(mac, sleep=clock.sleep, clock=clock)
        assert not ok and "no process" in why


# ── stop and uninstall ────────────────────────────────────────────


def _kills(mac: FakeMac) -> list[list[str]]:
    return [c for c in mac.calls if c[0] in (ds.PKILL, ds.PGREP)]


class TestStopAndUninstall:
    def test_stop_unloads_the_job_and_removes_its_definition(self, mac: FakeMac, layout: Layout, tmp_path: Path) -> None:
        identity = _ready(mac, layout)
        clock = Clock()
        ds.activate(mac, layout, identity, _binary(tmp_path, b"b"), VERSION,
                    say=lambda _m: None, sleep=clock.sleep, clock=clock)
        assert ds.stop(mac, layout) is True
        assert not mac.loaded and not layout.plist.exists()
        # the account and state stay for a later start
        assert ds.SERVICE_USER in mac.users and layout.state_dir.exists()

    def test_uninstall_stops_the_accounts_processes_before_deleting_it(
        self, mac: FakeMac, layout: Layout, tmp_path: Path
    ) -> None:
        identity = _ready(mac, layout)
        clock = Clock()
        ds.activate(mac, layout, identity, _binary(tmp_path, b"b"), VERSION,
                    say=lambda _m: None, sleep=clock.sleep, clock=clock)
        mac.calls.clear()
        assert ds.uninstall(mac, layout, say=lambda _m: None, sleep=clock.sleep) is True
        flat = [" ".join(c) for c in mac.calls]
        bootout = next(i for i, c in enumerate(flat) if "bootout" in c)
        pkill = flat.index(f"{ds.PKILL} -TERM -U 452")
        delete = flat.index(f"{ds.DSCL} . -delete /Users/{ds.SERVICE_USER}")
        assert bootout < pkill < delete
        assert ds.SERVICE_USER not in mac.users and ds.SERVICE_USER not in mac.groups
        for gone in (layout.state_dir, layout.log_dir, layout.library, layout.plist, layout.newsyslog,
                     layout.marker, layout.cli_link):
            assert not gone.exists() and not gone.is_symlink(), gone

    def test_uninstall_never_touches_an_account_it_did_not_create(self, mac: FakeMac, layout: Layout) -> None:
        mac.users[ds.SERVICE_USER] = {"UniqueID": "470", "PrimaryGroupID": "470"}
        mac.groups[ds.SERVICE_USER] = {"PrimaryGroupID": "470"}
        said: list[str] = []
        ds.uninstall(mac, layout, say=said.append, sleep=lambda _s: None)
        assert ds.SERVICE_USER in mac.users
        assert not any("-delete" in c for c in mac.calls)
        assert _kills(mac) == []
        assert any("did not create it" in s for s in said)

    @pytest.mark.parametrize("changed", ["Users", "Groups"])
    def test_uninstall_never_kills_or_deletes_when_the_id_changed(
        self, mac: FakeMac, layout: Layout, changed: str
    ) -> None:
        """The marker's number may now be someone else's: killing by it would kill their processes."""
        _install_account(mac, layout, 452)
        table = mac.users if changed == "Users" else mac.groups
        table[ds.SERVICE_USER]["UniqueID" if changed == "Users" else "PrimaryGroupID"] = "460"
        said: list[str] = []
        ds.uninstall(mac, layout, say=said.append, sleep=lambda _s: None)
        assert _kills(mac) == []
        assert not any("-delete" in c for c in mac.calls)
        assert ds.SERVICE_USER in mac.users
        assert layout.marker.exists(), "the marker stays while the account does"
        assert any("not the 452 Fadenstack created" in s for s in said)

    def test_uninstall_after_the_account_is_gone_kills_nothing(self, mac: FakeMac, layout: Layout) -> None:
        layout.marker.parent.mkdir(parents=True)
        layout.marker.write_text("name=_fadenagent\nid=441\n")  # _oahd's number, say
        assert ds.uninstall(mac, layout, say=lambda _m: None, sleep=lambda _s: None) is True
        assert _kills(mac) == []
        assert not layout.marker.exists()

    def test_what_uninstall_cannot_remove_is_reported_and_the_marker_kept(
        self, mac: FakeMac, layout: Layout, tmp_path: Path
    ) -> None:
        identity = _ready(mac, layout)
        stuck = layout.state_dir / "stuck"
        stuck.mkdir()
        (stuck / "file").write_text("x")
        os.chmod(stuck, 0o500)
        said: list[str] = []
        try:
            assert ds.uninstall(mac, layout, say=said.append, sleep=lambda _s: None) is False
        finally:
            os.chmod(stuck, 0o700)
        assert any("Could not remove" in s and "stuck" in s for s in said)
        assert layout.marker.exists()
        assert identity

    def test_a_cli_link_elsewhere_is_left_alone(self, mac: FakeMac, layout: Layout) -> None:
        layout.cli_link.parent.mkdir(parents=True)
        layout.cli_link.symlink_to("/opt/homebrew/bin/faden-agent")
        ds.uninstall(mac, layout, say=lambda _m: None, sleep=lambda _s: None)
        assert layout.cli_link.is_symlink()
