"""YAML from a model's draft of a skill, read with limits.

``yaml.safe_load`` expands aliases, so a few hundred bytes of "billion laughs"
fill the memory of the process that reads them, and the text a model writes
can be steered by whatever the conversation it read contained. A skill needs
none of YAML's references, so this loader refuses anchors and aliases, and
caps the size of the text and how deep it nests. Without aliases a document
cannot grow past its own size.

The skills service has the same rule for the bundles it stores
(``faden_skills/services/safe_yaml.py``); the two services share no code.
"""

from __future__ import annotations

from typing import Any

import yaml

#: Most bytes of YAML read: the frontmatter of a skill is a name and a description of at most 1,024 characters.
MAX_YAML_BYTES = 32 * 1024
#: Deepest nesting of maps and lists. A real frontmatter nests none.
MAX_YAML_DEPTH = 8


class YamlRefused(ValueError):
    """The YAML uses what a skill may not use; the message says what."""


class _Loader(yaml.SafeLoader):
    """``SafeLoader`` without anchors and aliases, and with a limit on nesting."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._depth = 0

    def compose_node(self, parent: yaml.Node | None, index: Any) -> yaml.Node | None:
        # Checked on the event, before the node is built: a deep or aliased document is refused as it is
        # read, not after it has been expanded.
        if self.check_event(yaml.AliasEvent):
            msg = "YAML aliases (*name) are not allowed."
            raise YamlRefused(msg)
        if getattr(self.peek_event(), "anchor", None) is not None:
            msg = "YAML anchors (&name) are not allowed."
            raise YamlRefused(msg)
        self._depth += 1
        if self._depth > MAX_YAML_DEPTH:
            msg = f"The YAML nests more than {MAX_YAML_DEPTH} levels deep."
            raise YamlRefused(msg)
        try:
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1


def load(text: str) -> Any:
    """The value of the YAML in *text*.

    Raises :class:`YamlRefused` for text over the size limit, anchors, aliases and deep nesting, and
    ``yaml.YAMLError`` for YAML that does not parse.
    """
    if len(text.encode("utf-8")) > MAX_YAML_BYTES:
        msg = f"The YAML is larger than {MAX_YAML_BYTES // 1024} KiB."
        raise YamlRefused(msg)
    loader = _Loader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()
