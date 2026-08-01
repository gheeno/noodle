"""Framework documentation lookup — the shared implementation behind both
`noodle docs` (CLI) and the MCP `read_docs` tool (NOOD_0160).

NOOD_0210 — this used to live in noodle/mcp/server.py, so `noodle docs`
imported the MCP server just to read a markdown file. That module builds a
FastMCP instance at import time, so when `mcp` 2.0 dropped
`mcp.server.fastmcp` every `noodle docs` call died with ModuleNotFoundError —
the exact failure the CLI form exists to avoid ("so an agent without MCP still
reaches content"). Nothing here needs MCP; it is stdlib file reading.
"""
from pathlib import Path

# NOOD_0158 — a doc at or under this rides back whole; past it the caller gets
# the section index and picks. agent-playbook.md is 57 KB: returning it whole
# cost a spilled tool result plus 7 recovery greps, to use one 4 KB section.
DOC_WHOLE_MAX_BYTES = 8_000


def _doc_sections(text: str) -> list[dict]:
    """Split a doc on its `## ` headings. The preamble (anything before the
    first heading) is section one, so nothing is unreachable."""
    secs: list[dict] = []
    title, buf = "(preamble)", []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            if "".join(buf).strip():
                secs.append({"title": title, "body": "".join(buf)})
            title, buf = line[3:].strip(), [line]
        else:
            buf.append(line)
    if "".join(buf).strip():
        secs.append({"title": title, "body": "".join(buf)})
    return secs


def _pick_section(secs: list[dict], want: str) -> dict | None:
    """Match a section by 1-based number, exact title, or substring — an agent
    quoting a heading loosely ('steps dictionary') should not need a retry."""
    w = want.strip()
    if w.lstrip("#").isdigit():
        i = int(w.lstrip("#"))
        return secs[i - 1] if 1 <= i <= len(secs) else None
    for s in secs:
        if s["title"].lower() == w.lower():
            return s
    for s in secs:
        if w.lower() in s["title"].lower():
            return s
    return None


def _docs_dir() -> Path | None:
    """The framework's docs/ folder. Resolves next to the installed noodle
    package — present in a repo checkout / editable install. ponytail: wheel
    installs don't ship docs/; package them if that install mode matters."""
    d = Path(__file__).resolve().parent.parent / "docs"
    return d if d.is_dir() else None


def read_docs(name: str | None = None, query: str | None = None,
              section: str | None = None) -> dict:
    """Framework documentation lookup (NOOD_0089) — keeps agent context lean:
    call this when you need Noodle detail instead of guessing or pasting docs
    into prompts. No args → the list of available docs, each with a one-line
    summary and its byte cost. name (e.g. 'agent-playbook') → that doc, whole
    when it is small; a large doc returns its SECTION INDEX instead
    (NOOD_0158) — pick one and call again with section='<heading or its
    number>' rather than pulling 57 KB into context. query → matching lines
    (with doc + section + line) across all docs, for one fact, not a file."""
    d = _docs_dir()
    if d is None:
        return {"error": "docs/ not found next to the installed noodle package "
                         "— read them at https://github.com/gheeno/noodle/tree/main/docs"}
    files = sorted(d.glob("*.md"))
    if name:
        stem = name.removesuffix(".md")
        f = d / f"{stem}.md"
        if not f.is_file():
            return {"error": f"no doc named {name!r}",
                    "available": [p.stem for p in files]}
        text = f.read_text(encoding="utf-8")
        secs = _doc_sections(text)
        if section:
            hit = _pick_section(secs, section)
            if hit is None:
                return {"error": f"no section matching {section!r} in {f.name}",
                        "sections": [s["title"] for s in secs]}
            return {"name": f.name, "section": hit["title"],
                    "content": hit["body"]}
        if len(text) <= DOC_WHOLE_MAX_BYTES or len(secs) < 2:
            return {"name": f.name, "content": text}
        return {"name": f.name, "bytes": len(text),
                "note": (f"{len(text) // 1000} KB — too large to return whole. "
                         f"Call read_docs(name={stem!r}, section='<title or #>') "
                         f"for one section, or query=… to grep every doc."),
                "sections": [{"n": i, "title": s["title"], "bytes": len(s["body"])}
                             for i, s in enumerate(secs, 1)]}
    if query:
        q = query.lower()
        hits = []
        for f in files:
            sec = "(preamble)"       # NOOD_0159: name the section so a hit is
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if line.startswith("## "):     # retrievable without an index
                    sec = line[3:].strip()     # round trip
                if q in line.lower():
                    hits.append({"doc": f.stem, "section": sec, "line": i,
                                 "text": line.strip()})
                    if len(hits) >= 50:
                        return {"query": query, "hits": hits, "truncated": True}
        return {"query": query, "hits": hits}

    def _entry(f: Path) -> dict:
        text = f.read_text(encoding="utf-8")
        summary = next((ln.strip() for ln in text.splitlines()
                        if ln.strip() and not ln.startswith("#")), "")
        # NOOD_0159: cost rides in the index so the caller knows what a
        # retrieval spends before making it.
        return {"name": f.stem, "summary": summary, "bytes": len(text),
                "sections": len(_doc_sections(text))}
    return {"docs": [_entry(f) for f in files]}
