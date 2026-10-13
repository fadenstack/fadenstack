"""release/update_feed.py builds the feed installs read: what it takes, and what it refuses."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "release" / "update_feed.py"
if not SCRIPT.exists():
    pytest.skip("not a checkout of the whole repository", allow_module_level=True)

spec = importlib.util.spec_from_file_location("update_feed", SCRIPT)
assert spec is not None and spec.loader is not None
update_feed = importlib.util.module_from_spec(spec)
sys.modules["update_feed"] = update_feed
spec.loader.exec_module(update_feed)

TODAY = date(2026, 10, 20)


def entry(version: str, **extra: object) -> dict:
    return {"version": version, "severity": "normal", "summary": "Fixes.", **extra}


def test_a_release_gets_its_notes_page_highlights_and_the_build_date() -> None:
    feed = update_feed.build(
        {"releases": [entry("0.4.2", highlights=["  One  line. ", ""], components={"agent": "0.2.7"})]},
        version="0.4.2",
        agent="0.2.7",
        today=TODAY,
    )
    release = feed["releases"][0]
    assert release["notes_url"] == "https://fadenstack.com/releases/#v0.4.2"
    assert release["highlights"] == ["One line."]
    assert release["date"] == "2026-10-20"
    assert release["components"] == {"cli": "0.4.2", "agent": "0.2.7"}
    assert feed["channels"] == {"stable": "0.4.2", "preview": "0.4.2"}


def test_the_github_release_text_has_the_summary_highlights_and_notes_page() -> None:
    feed = update_feed.build(
        {"releases": [entry("0.4.2", highlights=["One line.", "Another."]), entry("0.4.1")]},
        version="0.4.2",
        agent=None,
        today=TODAY,
    )
    assert update_feed.notes(feed, "0.4.2") == (
        "Fixes.\n\n**What's new**\n\n- One line.\n- Another.\n\n"
        "Release notes: https://fadenstack.com/releases/#v0.4.2\n"
    )
    assert "What's new" not in update_feed.notes(feed, "0.4.1")
    with pytest.raises(update_feed.BuildError, match=r"no entry for 0.4.9"):
        update_feed.notes(feed, "0.4.9")


def test_a_tag_without_an_entry_is_refused() -> None:
    with pytest.raises(update_feed.BuildError, match=r"no entry for 0.4.3"):
        update_feed.build({"releases": [entry("0.4.2")]}, version="0.4.3", agent=None, today=TODAY)


def test_the_agent_the_backend_expects_must_be_the_released_one() -> None:
    with pytest.raises(update_feed.BuildError, match="AGENT_VERSION"):
        update_feed.build(
            {"releases": [entry("0.4.2", components={"agent": "0.2.6"})]},
            version="0.4.2",
            agent="0.2.7",
            today=TODAY,
        )


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (entry("0.5.0rc1"), "pre-release"),
        (entry("0.5.0", severity="urgent"), "severity"),
        (entry("0.5.0", summary=" "), "summary"),
        (entry("0.5.0", upgrade_from="soon"), "upgrade_from"),
    ],
)
def test_entries_installs_could_not_read_are_refused(bad: dict, message: str) -> None:
    with pytest.raises(update_feed.BuildError, match=message):
        update_feed.build({"releases": [bad]}, version=None, agent=None, today=TODAY)
