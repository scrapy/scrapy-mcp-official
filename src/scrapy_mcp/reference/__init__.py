"""Reference materials delivered to the agent.

Kept as packaged Markdown files (not string literals) so they're easy to read and edit
and can be reviewed as docs.
"""

from importlib.resources import files


def _read(filename: str) -> str:
    return (files(__package__) / filename).read_text(encoding="utf-8")


# this is used as the description for the "execute" tool (so it's always loaded)
EXECUTE_TOOL_DESC = _read("execute_tool_desc.md").strip()
# this is available as output of the "inspection_reference" tool
INSPECTION_REFERENCE = _read("inspection_reference.md")
