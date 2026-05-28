"""Java language definition."""

from __future__ import annotations

import re

from lib.languages.base import Language


def parse_class_metadata(content: str) -> dict:
    """Extract structural metadata from a Java source file.

    Returns {class_name, package, extends, implements}. Missing fields
    are returned as None / [].
    """
    meta: dict = {
        'class_name': None,
        'package': None,
        'extends': None,
        'implements': [],
    }

    m = re.search(r'^package\s+([\w.]+);', content, re.MULTILINE)
    if m:
        meta['package'] = m.group(1)

    m = re.search(
        r'(?:public\s+)?(?:abstract\s+)?(?:class|interface)\s+(\w+)'
        r'(?:\s+extends\s+([\w<>, ]+?))?'
        r'(?:\s+implements\s+([\w<>, ]+?))?\s*\{',
        content,
    )
    if m:
        meta['class_name'] = m.group(1)
        meta['extends'] = m.group(2).strip() if m.group(2) else None
        if m.group(3):
            meta['implements'] = [i.strip() for i in m.group(3).split(',')]

    return meta


JAVA = Language(
    id='java',
    file_extensions=('.java',),
    parse_class_metadata=parse_class_metadata,
)
