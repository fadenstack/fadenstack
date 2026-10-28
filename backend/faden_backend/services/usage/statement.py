"""The configuration statement: the privacy settings in force, as a Markdown document (S09).

For the works agreement's annex and the DPIA (GDPR Art. 5(2); § 90(1) BetrVG, "erforderliche Unterlagen"): the
purpose and the excluded uses (S13), each ``usage.*`` setting, the instance's limits, who sees what, who holds
the usage auditor role, and how many views of named people's usage were recorded in the last 12 months.

The texts are the backend's translations (``i18n/<language>/common.json``, ``usage.statement.*``); a key a
language lacks falls back to English.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

from faden_backend.services.usage.config import UsageSettings
from faden_backend.settings import settings

Language = Literal["en", "de", "es", "fr", "zh"]
LANGUAGES: tuple[Language, ...] = ("en", "de", "es", "fr", "zh")

_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|#])")


@lru_cache(maxsize=len(LANGUAGES))
def _texts(language: str) -> dict[str, str]:
    """``usage.statement`` of *language*'s bundle (empty when there is none)."""
    path = settings.i18n_path / language / "common.json"
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = bundle.get("usage", {}).get("statement", {})
    return {key: str(value) for key, value in found.items()} if isinstance(found, dict) else {}


def texts(language: str) -> dict[str, str]:
    """The statement's texts in *language*, English where it has none."""
    return {**_texts("en"), **_texts(language)}


def _md(value: object) -> str:
    """A value set into Markdown, its special characters escaped (an address may hold ``_``)."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", str(value))


class _Writer:
    def __init__(self, language: str) -> None:
        self.texts = texts(language)
        self.lines: list[str] = []

    def t(self, key: str, **params: object) -> str:
        found = self.texts.get(key, key)
        for name, value in params.items():
            found = found.replace("{{" + name + "}}", str(value))
        return found

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)


def render(
    config: UsageSettings,
    limits: dict[str, int | None],
    auditor_emails: list[str],
    views_last_year: int,
    language: str,
    now: datetime,
) -> str:
    """The statement as Markdown."""
    w = _Writer(language if language in LANGUAGES else "en")
    on_off = w.t("on") if config.person_reporting else w.t("off")
    settings_rows: list[tuple[str, Any]] = [
        ("person_reporting", on_off),
        ("min_group_size", w.t("people", count=config.k)),
        ("person_granularity", w.t(config.person_granularity)),
        ("request_log_retention_days", w.t("days", count=config.request_log_retention_days)),
        ("person_retention_days", w.t("days", count=config.person_retention_days)),
        ("hourly_retention_days", w.t("days", count=config.hourly_retention_days)),
        ("daily_retention_months", w.t("months", count=config.daily_retention_months)),
        ("time_zone", _md(config.time_zone)),
    ]
    w.add(f"# {w.t('title')}", "", w.t("generated", date=now.astimezone(config.zone).date().isoformat()), "")
    w.add(w.t("intro"), "")
    w.add(f"## {w.t('purpose_heading')}", "", w.t("purpose"), "", w.t("excluded"), "")
    w.add(f"## {w.t('settings_heading')}", "")
    w.add(f"| {w.t('column_setting')} | {w.t('column_value')} |", "| --- | --- |")
    w.add(*(f"| {w.t(name)} | {value} |" for name, value in settings_rows))
    w.add("", f"## {w.t('limits_heading')}", "")
    w.add(f"| {w.t('column_setting')} | {w.t('column_value')} |", "| --- | --- |")
    w.add(*(
        f"| {w.t(name)} | {w.t('no_limit') if value is None else value} |" for name, value in limits.items()
    ))
    w.add("", w.t("limits_note"), "")
    w.add(f"## {w.t('access_heading')}", "")
    w.add(f"- {w.t('access_own')}", f"- {w.t('access_aggregate', k=config.k)}")
    w.add(f"- {w.t('access_people_on') if config.person_reporting else w.t('access_people_off')}")
    w.add(f"- {w.t('access_superuser')}")
    names = ", ".join(_md(email) for email in auditor_emails)
    w.add(f"- {w.t('auditors', names=names) if auditor_emails else w.t('auditors_none')}")
    w.add(f"- {w.t('views', count=views_last_year)}", "", "---", "", f"_{w.t('footer')}_", "")
    return "\n".join(w.lines)


def year_before(now: datetime) -> datetime:
    """Twelve months before *now*: the window of the counted views."""
    return now - timedelta(days=365)
