"""``faden secrets rotate``, the backend's half: a second key that still opens, and the job that re-seals.

A rotation puts the new key in .env next to the old one, so for a while every service must open values
sealed with either (MultiFernet), and the job must turn all of them into values the new key alone opens, in
one transaction, however often it is run. The machines' credential hashes are keyed with the pepper, which
a rotation must not touch: a hashed credential has to verify afterwards.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from cryptography.fernet import InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from faden_backend.db.meta import meta
from faden_backend.services.machine_tls import authority as auth
from faden_backend.services.nodes.auth import hash_with_pepper
from faden_backend.services.system_settings.crypto import SettingsCrypto, fernet_for
from faden_backend.settings import settings
from faden_backend.tools import key_rotation, rotate_keys
from faden_backend.tools.key_rotation import RotationError, Scheme, Target, rotate

OLD = "the-old-master-key-0123456789"
NEW = "the-new-master-key-9876543210"


class _Joined:
    """An engine look-alike that runs the job in a savepoint of the test's own connection."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncConnection]:
        nested = await self._connection.begin_nested()
        try:
            yield self._connection
        except BaseException:
            await nested.rollback()
            raise
        await nested.commit()


def _scheme(previous: str | None = OLD) -> Scheme:
    return Scheme(fernet_for(NEW), fernet_for(previous) if previous else None)


# ── the second key ────────────────────────────────────────────────


def test_a_value_sealed_with_the_previous_key_still_opens() -> None:
    sealed_before = SettingsCrypto(OLD).encrypt("hf_secret")
    crypto = SettingsCrypto(NEW, OLD)
    assert crypto.decrypt(sealed_before) == "hf_secret"
    assert not crypto.opens_with_current_key(sealed_before)


def test_new_values_are_sealed_with_the_current_key_alone() -> None:
    sealed = SettingsCrypto(NEW, OLD).encrypt("x")
    assert SettingsCrypto(NEW).decrypt(sealed) == "x"
    with pytest.raises(InvalidToken):
        SettingsCrypto(OLD).decrypt(sealed)


def test_rotate_turns_an_old_value_into_one_the_new_key_opens() -> None:
    crypto = SettingsCrypto(NEW, OLD)
    again = crypto.rotate(SettingsCrypto(OLD).encrypt("keep me"))
    assert SettingsCrypto(NEW).decrypt(again) == "keep me"


def test_a_key_that_is_neither_still_fails() -> None:
    with pytest.raises(InvalidToken):
        SettingsCrypto(NEW, OLD).decrypt(SettingsCrypto("somebody-else").encrypt("x"))


def test_from_settings_reads_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    assert SettingsCrypto.from_settings().decrypt(SettingsCrypto(OLD).encrypt("a")) == "a"


def test_the_machine_ca_key_opens_with_the_previous_key(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = auth.seal(b"PEM", OLD, auth.KEY_PURPOSE_CA)
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    assert auth.unseal(sealed, NEW, auth.KEY_PURPOSE_CA) == b"PEM"
    # The label still separates what the keys seal.
    with pytest.raises(InvalidToken):
        auth.unseal(sealed, NEW, auth.KEY_PURPOSE_CLIENT)


def test_another_install_key_does_not_borrow_the_previous_one(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed = auth.seal(b"PEM", OLD, auth.KEY_PURPOSE_CA)
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    with pytest.raises(InvalidToken):
        auth.unseal(sealed, "a-restored-install-key", auth.KEY_PURPOSE_CA)


# ── the pepper ────────────────────────────────────────────────────


def test_the_pepper_falls_back_to_the_master_key_and_follows_its_own_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "settings_master_key", OLD)
    monkeypatch.setattr(settings, "credential_pepper", "")
    assert settings.machine_pepper == OLD
    monkeypatch.setattr(settings, "credential_pepper", "the-pepper")
    assert settings.machine_pepper == "the-pepper"


def test_a_credential_hashed_before_a_rotation_verifies_after_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Before: no pepper setting, the master key was the pepper. `faden upgrade` copies it into the setting.
    monkeypatch.setattr(settings, "settings_master_key", OLD)
    monkeypatch.setattr(settings, "credential_pepper", "")
    stored = hash_with_pepper("machine-secret", pepper=settings.machine_pepper)
    monkeypatch.setattr(settings, "credential_pepper", OLD)
    # The rotation: a new master key, the pepper untouched.
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    assert hash_with_pepper("machine-secret", pepper=settings.machine_pepper) == stored


def test_the_cluster_seed_token_does_not_change_with_the_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from faden_backend.services.inference.drivers.ray.environment import (
        RayEnvironmentManager,
    )

    env = SimpleNamespace(id="e", generation=3)
    monkeypatch.setattr(settings, "credential_pepper", "the-pepper")
    monkeypatch.setattr(settings, "settings_master_key", OLD)
    before = RayEnvironmentManager._seed_token(env, "n1")
    monkeypatch.setattr(settings, "settings_master_key", NEW)
    assert RayEnvironmentManager._seed_token(env, "n1") == before


# ── the job ───────────────────────────────────────────────────────


@pytest.fixture
async def probe(dbsession: AsyncSession) -> AsyncIterator[AsyncConnection]:
    """A scratch table of sealed values, in the test's own transaction."""
    connection = await dbsession.connection()
    await connection.execute(text("CREATE TABLE rotation_probe (id integer PRIMARY KEY, secret text)"))
    yield connection


async def _fill(connection: AsyncConnection, values: dict[int, str | None]) -> None:
    for key, value in values.items():
        await connection.execute(
            text("INSERT INTO rotation_probe (id, secret) VALUES (:i, :s)"), {"i": key, "s": value},
        )


async def _stored(connection: AsyncConnection) -> dict[int, str | None]:
    rows = await connection.execute(text("SELECT id, secret FROM rotation_probe ORDER BY id"))
    return {row.id: row.secret for row in rows}


def _probe_target(**kwargs: Any) -> Target:
    return Target("rotation_probe", ("id",), "secret", _scheme(), **kwargs)


async def test_the_job_seals_every_value_with_the_new_key(probe: AsyncConnection) -> None:
    await _fill(probe, {
        1: SettingsCrypto(OLD).encrypt("one"),
        2: SettingsCrypto(NEW).encrypt("two"),  # a rerun: already done
        3: None,
    })
    report = await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    counts = report.columns["rotation_probe.secret"]
    assert (counts.rotated, counts.already_current) == (1, 1)
    now = await _stored(probe)
    only_new = SettingsCrypto(NEW)
    assert only_new.decrypt(now[1] or "") == "one"
    assert only_new.decrypt(now[2] or "") == "two"
    assert now[3] is None


async def test_a_second_run_changes_nothing(probe: AsyncConnection) -> None:
    await _fill(probe, {1: SettingsCrypto(OLD).encrypt("one")})
    await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    first = await _stored(probe)
    report = await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    assert await _stored(probe) == first
    assert report.columns["rotation_probe.secret"].rotated == 0


async def test_a_crash_midway_leaves_every_value_openable_and_a_rerun_finishes(
    probe: AsyncConnection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    originals = {i: SettingsCrypto(OLD).encrypt(f"value {i}") for i in range(1, 6)}
    await _fill(probe, originals)
    real = key_rotation.reseal
    calls = {"n": 0}

    def crash_on_the_fourth(value: str, scheme: Scheme) -> tuple[str, str | None]:
        calls["n"] += 1
        if calls["n"] == 4:
            raise ConnectionError("the database went away")
        return real(value, scheme)

    monkeypatch.setattr(key_rotation, "reseal", crash_on_the_fourth)
    with pytest.raises(ConnectionError):
        await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    # Rolled back: nothing half-done, and what is stored opens with the two keys the services hold.
    after_crash = await _stored(probe)
    assert after_crash == originals
    both = SettingsCrypto(NEW, OLD)
    assert [both.decrypt(v or "") for v in after_crash.values()] == [f"value {i}" for i in range(1, 6)]

    monkeypatch.setattr(key_rotation, "reseal", real)
    report = await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    assert report.columns["rotation_probe.secret"].rotated == 5
    only_new = SettingsCrypto(NEW)
    assert [only_new.decrypt(v or "") for v in (await _stored(probe)).values()] == [
        f"value {i}" for i in range(1, 6)
    ]


async def test_a_value_the_new_key_does_not_open_is_never_committed(
    probe: AsyncConnection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = SettingsCrypto(OLD).encrypt("one")
    await _fill(probe, {1: original})
    # A sealing step that wrote something the new key alone cannot open (here: sealed with the old one again).
    monkeypatch.setattr(key_rotation, "reseal", lambda value, scheme: ("rotated", SettingsCrypto(OLD).encrypt("one")))
    with pytest.raises(RotationError, match="do not open with the new key"):
        await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    assert await _stored(probe) == {1: original}


async def test_a_value_neither_key_opens_stops_the_rotation_unless_told_to_skip_it(probe: AsyncConnection) -> None:
    good = SettingsCrypto(OLD).encrypt("one")
    lost = SettingsCrypto("a-key-nobody-has-any-more").encrypt("two")
    await _fill(probe, {1: good, 2: lost})
    with pytest.raises(RotationError, match="neither"):
        await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    assert await _stored(probe) == {1: good, 2: lost}  # not even the good one was changed
    report = await rotate(
        _Joined(probe), [_probe_target()], skip_unreadable=True, progress=lambda _: None,  # type: ignore[arg-type]
    )
    counts = report.columns["rotation_probe.secret"]
    assert (counts.rotated, counts.unreadable) == (1, 1)
    now = await _stored(probe)
    assert now[2] == lost
    assert SettingsCrypto(NEW).decrypt(now[1] or "") == "one"


async def test_plain_text_is_left_alone_where_it_is_allowed_and_refused_where_it_is_not(
    probe: AsyncConnection,
) -> None:
    await _fill(probe, {1: "sk-typed-by-hand"})
    report = await rotate(_Joined(probe), [_probe_target(plaintext_ok=True)], progress=lambda _: None)  # type: ignore[arg-type]
    assert report.columns["rotation_probe.secret"].plaintext == 1
    assert (await _stored(probe))[1] == "sk-typed-by-hand"
    with pytest.raises(RotationError):
        await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]


async def test_the_job_pages_through_more_rows_than_one_batch(
    probe: AsyncConnection, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(key_rotation, "BATCH", 3)
    await _fill(probe, {i: SettingsCrypto(OLD).encrypt(str(i)) for i in range(1, 11)})
    report = await rotate(_Joined(probe), [_probe_target()], progress=lambda _: None)  # type: ignore[arg-type]
    assert report.columns["rotation_probe.secret"].rotated == 10
    only_new = SettingsCrypto(NEW)
    assert [only_new.decrypt(v or "") for v in (await _stored(probe)).values()] == [str(i) for i in range(1, 11)]


# ── the real columns ──────────────────────────────────────────────


def test_every_target_is_a_real_column() -> None:
    from faden_backend.db.models import load_all_models

    load_all_models()
    for target in rotate_keys.targets(NEW, OLD):
        assert target.table in meta.tables, target.label
        assert target.column in meta.tables[target.table].c, target.label
        assert set(target.primary_key) == {c.name for c in meta.tables[target.table].primary_key.columns}


#: Columns whose names say "sealed" but that this job does not own, with the reason.
_NOT_SEALED_BY_THE_MASTER_KEY: dict[str, str] = {}


def test_no_sealed_column_is_forgotten() -> None:
    """A new ``*_encrypted``, ``*_sealed`` or ``ciphertext`` column must be added to the job (or excused here)."""
    from faden_backend.db.models import load_all_models

    load_all_models()
    covered = {target.label for target in rotate_keys.targets(NEW, OLD)}
    found = {
        f"{t.name}.{c.name}"
        for t in meta.tables.values()
        for c in t.columns
        if c.name.endswith(("_encrypted", "_sealed")) or c.name == "ciphertext"
    }
    assert sorted(found - covered - set(_NOT_SEALED_BY_THE_MASTER_KEY)) == []


async def test_the_job_re_seals_the_console_secrets_and_the_machine_ca_key(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from faden_backend.db.models.machine_tls import MachineCA
    from faden_backend.db.models.system_settings import SystemSettingSecret

    # Another test's rows in this transaction-scoped database are not ours to judge.
    await dbsession.execute(text("DELETE FROM system_setting_secret"))
    await dbsession.execute(text("DELETE FROM machine_ca"))
    now = datetime(2026, 10, 12, tzinfo=UTC)
    dbsession.add(SystemSettingSecret(key="hf.token", ciphertext=SettingsCrypto(OLD).encrypt("hf_x")))
    dbsession.add(MachineCA(
        id=uuid4(), cert_pem="c", key_sealed=auth.seal(b"CA-KEY", OLD, auth.KEY_PURPOSE_CA),
        fingerprint_sha256="f" * 64, not_after=now, issuing_from=now,
    ))
    await dbsession.flush()

    monkeypatch.setattr(settings, "settings_master_key", NEW)
    monkeypatch.setattr(settings, "settings_master_key_previous", OLD)
    outcome = await rotate_keys.run(skip_unreadable=False, engine=_Joined(await dbsession.connection()))  # type: ignore[arg-type]
    assert outcome["ok"], outcome
    assert outcome["columns"]["system_setting_secret.ciphertext"]["rotated"] == 1
    assert outcome["columns"]["machine_ca.key_sealed"]["rotated"] == 1

    secret = (await dbsession.execute(text("SELECT ciphertext FROM system_setting_secret"))).scalar_one()
    ca_key = (await dbsession.execute(text("SELECT key_sealed FROM machine_ca"))).scalar_one()
    assert SettingsCrypto(NEW).decrypt(secret) == "hf_x"
    assert auth.sealing_fernet(NEW, auth.KEY_PURPOSE_CA).decrypt(ca_key.encode()) == b"CA-KEY"


async def test_an_empty_master_key_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "settings_master_key", "")
    outcome = await rotate_keys.run(skip_unreadable=False)
    assert outcome["ok"] is False
