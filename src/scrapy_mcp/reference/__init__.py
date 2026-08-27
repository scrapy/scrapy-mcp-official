"""Reference materials delivered to the agent.

Kept as packaged Markdown files (not string literals) so they're easy to read and edit
and can be reviewed as docs. Loaded via :mod:`importlib.resources` so they work whether
the package is installed as a wheel or in editable mode.

- ``execute_tool_desc.md`` — the always-loaded inline layer (the ``execute`` tool
  description).
- ``inspection_reference.md`` — the on-demand ``inspection_reference`` tool.

Deliberately minimal; grow them only from observed eval gaps.
"""

from importlib.resources import files


def _read(filename: str) -> str:
    return (files(__package__) / filename).read_text(encoding="utf-8")


EXECUTE_TOOL_DESC = _read("execute_tool_desc.md").strip()
INSPECTION_REFERENCE = _read("inspection_reference.md")
