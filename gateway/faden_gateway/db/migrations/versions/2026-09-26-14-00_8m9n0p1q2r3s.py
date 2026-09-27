"""Chat session titles are encrypted at rest, like the messages.

A title is the start of the first message, so it held the same text the
message column encrypts -- in plain text. The column becomes ``TEXT`` for
Fernet ciphertext (longer than the old 512 characters). Existing titles are
left as they are: EncryptedText reads plaintext rows unchanged, and a title
is encrypted when it is next written (as the message content was migrated).

Revision ID: 8m9n0p1q2r3s
Revises: 7l8m9n0p1q2r
Create Date: 2026-09-26 14:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "8m9n0p1q2r3s"
down_revision = "7l8m9n0p1q2r"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_session ALTER COLUMN title TYPE TEXT")


def downgrade() -> None:
    # Encrypted titles do not fit 512 characters; they are dropped rather
    # than cut into invalid ciphertext.
    op.execute("UPDATE chat_session SET title = NULL WHERE length(title) > 512")
    op.execute("ALTER TABLE chat_session ALTER COLUMN title TYPE VARCHAR(512)")
