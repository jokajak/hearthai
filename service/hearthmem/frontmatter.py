"""Minimal YAML-frontmatter markdown, without a YAML dependency.

Deliberately supports only what entries need: string and list-of-string values.
Anything richer should be a reason to reach for a real YAML library, not to
extend this quietly.
"""

from __future__ import annotations

DELIM = "---"


class FrontmatterError(ValueError):
    pass


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        body = raw[1:-1]
        if raw[0] == '"':
            out, i = [], 0
            while i < len(body):
                ch = body[i]
                if ch == "\\" and i + 1 < len(body):
                    out.append(body[i + 1])
                    i += 2
                    continue
                out.append(ch)
                i += 1
            return "".join(out)
        return body
    return raw


def dumps(meta: dict, body: str) -> str:
    lines = [DELIM]
    for key, value in meta.items():
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend(f"  - {_quote(str(v))}" for v in value)
        else:
            lines.append(f"{key}: {_quote(str(value))}")
    lines.append(DELIM)
    text = "\n".join(lines) + "\n"
    if body:
        text += "\n" + body.rstrip("\n") + "\n"
    return text


def loads(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != DELIM:
        raise FrontmatterError("file does not start with a frontmatter delimiter")

    meta: dict = {}
    current_key: str | None = None
    end = None
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == DELIM:
            end = index
            break
        if line.startswith("  - ") or line.startswith("- "):
            if current_key is None:
                raise FrontmatterError("list item before any key")
            meta.setdefault(current_key, [])
            if not isinstance(meta[current_key], list):
                raise FrontmatterError(f"key {current_key!r} got both a value and a list")
            meta[current_key].append(_unquote(line.split("-", 1)[1]))
            continue
        if ":" not in line:
            raise FrontmatterError(f"malformed frontmatter line: {line!r}")
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        current_key = key
        if rest == "[]":
            meta[key] = []
        elif rest == "":
            meta[key] = []
        else:
            meta[key] = _unquote(rest)

    if end is None:
        raise FrontmatterError("unterminated frontmatter block")

    body = "\n".join(lines[end + 1 :]).strip("\n")
    return meta, body
