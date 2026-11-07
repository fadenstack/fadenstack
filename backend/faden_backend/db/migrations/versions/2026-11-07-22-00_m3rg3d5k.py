"""Merge the desktop branch's head with 0.6.0 and the approval levels: one head again.

``feat/desktop-core`` added the app pairing tables (``p41r1ng``, on 0.5.1's ``tk3nus3``); 0.6.0 and
``feat/approval-levels`` went on from the same revision to the agent versions' approval levels
(``4ppr0v4l5``). Neither touches the other's tables, so this revision only joins the two; it changes nothing.

Revision ID: m3rg3d5k
Revises: p41r1ng, 4ppr0v4l5
Create Date: 2026-11-07 22:00:00
"""

revision = "m3rg3d5k"
down_revision = ("p41r1ng", "4ppr0v4l5")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Nothing to change: the two branches meet here."""


def downgrade() -> None:
    """Nothing to change: the two branches part here."""
