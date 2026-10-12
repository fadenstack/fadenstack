"""YAML from a skill's author, read with limits.

``yaml.safe_load`` expands aliases, so a few hundred bytes of "billion laughs"
(each anchor a list of the last one, nine times over) fill the memory of the
service that reads them -- and a skill's frontmatter and ``faden.yaml`` come
from whoever uploads a bundle. A skill needs none of YAML's references, so
this loader refuses anchors and aliases, and caps the size of the text and how
deep it nests. Without aliases a document cannot grow past its own size.
"""

from __future__ import annotations

from typing import Any

import yaml

#: Most bytes of YAML read. The Agent Skills format keeps a description to 1,024 characters and a name to 64;
#: ``faden.yaml`` lists tool names. 32 KiB is far beyond a real file and still small enough to parse in
#: milliseconds.
MAX_YAML_BYTES = 32 * 1024
#: Deepest nesting of maps and lists. A real file nests 3 levels (a map of fields holding lists of maps).
MAX_YAML_DEPTH = 8


class YamlRefused(ValueError):
    """The YAML is valid, perhaps, but a skill may not use what it uses; the message says what."""


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


class _Dumper(yaml.SafeDumper):
    """``SafeDumper`` that writes a value used twice out twice, never as ``&id001`` and ``*id001``.

    What is written here is read back by :func:`load`, which refuses aliases.
    """

    def ignore_aliases(self, data: Any) -> bool:  # noqa: ARG002
        return True


def dump(data: Any) -> str:
    """*data* as YAML that :func:`load` reads back."""
    return yaml.dump(
        data, Dumper=_Dumper, default_flow_style=False, allow_unicode=True, sort_keys=False,
    )
