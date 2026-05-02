#!/usr/bin/env python3
"""One-time migration: convert frontmatter `related` from plain strings to [[wikilinks]]."""

import re
import sys
from pathlib import Path

_RE_FRONTMATTER = re.compile(r"^(---\s*\n)(.*?)(---\s*\n)", re.DOTALL)
_RE_RELATED_BLOCK = re.compile(r"(related:\s*\n)((?:  - .*\n)*)", re.MULTILINE)
_RE_RELATED_ITEM = re.compile(r"^(  - )(.+)$", re.MULTILINE)


def _convert_item(m: re.Match) -> str:
    prefix, title = m.group(1), m.group(2).strip()
    if title.startswith("[[") or title.startswith('"[['):
        return m.group(0)  # already a wikilink
    return f'{prefix}"[[{title}]]"'


def migrate_file(path: Path, dry_run: bool) -> bool:
    content = path.read_text(encoding="utf-8")
    fm_match = _RE_FRONTMATTER.match(content)
    if not fm_match or "related:" not in fm_match.group(2):
        return False

    new_fm = _RE_RELATED_BLOCK.sub(
        lambda m: m.group(1) + _RE_RELATED_ITEM.sub(_convert_item, m.group(2)),
        fm_match.group(2),
    )
    if new_fm == fm_match.group(2):
        return False

    if not dry_run:
        new_content = fm_match.group(1) + new_fm + fm_match.group(3) + content[fm_match.end():]
        path.write_text(new_content, encoding="utf-8")
    return True


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    kb_root = Path("/Users/careycao/knowledge_base")

    updated, skipped = 0, 0
    for md_file in sorted(kb_root.rglob("*.md")):
        try:
            if migrate_file(md_file, dry_run):
                print(f"  {'(dry)' if dry_run else '✓'} {md_file.relative_to(kb_root)}")
                updated += 1
            else:
                skipped += 1
        except Exception as exc:
            print(f"  ✗ {md_file}: {exc}", file=sys.stderr)

    label = "待更新" if dry_run else "已更新"
    print(f"\n完成：{label} {updated} 篇，无需修改 {skipped} 篇")


if __name__ == "__main__":
    main()
