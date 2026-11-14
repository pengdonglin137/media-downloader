"""Insert explicit review-caption line breaks without losing authored characters."""

import re
import unicodedata

from media_workflow.native_audio import require


CLOSING = frozenset('，。、；：！？）》」』】〕,.;:!?)]}')
OPENING = frozenset('（《「『【〔([{')


def cells(text):
    return sum(0 if unicodedata.combining(character) else
        2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1 for character in text)


def wrap(text, protected_terms=(), max_cells=100):
    require(isinstance(text, str) and text and all(ord(character) >= 32 for character in text),
        'Expected one complete single-line review paragraph')
    require(not text[0].isspace() and not text[-1].isspace(), 'Review paragraph has unsafe edge whitespace')
    require(type(max_cells) is int and 16 <= max_cells <= 120, 'Invalid explicit review line width')
    require(isinstance(protected_terms, (list, tuple)) and len(protected_terms) <= 256
        and len(set(protected_terms)) == len(protected_terms)
        and all(isinstance(term, str) and 2 <= len(term) <= 64
            and all(ord(character) >= 32 for character in term) for term in protected_terms),
        'Expected distinct explicit protected terms')
    alternatives = [re.escape(term) for term in sorted(protected_terms, key=lambda term: (-len(term), term))]
    tokens = re.findall('|'.join(alternatives + [r'[A-Za-z0-9_./:+-]+', r'.']), text)
    rows, current = [], []
    for token in tokens:
        require(cells(token) <= max_cells, 'An indivisible review term exceeds the line width')
        if current and cells(''.join(current) + token) > max_cells:
            boundary = len(current)
            following = token
            while boundary and (following[0] in CLOSING or current[boundary - 1][-1] in OPENING
                    or following[0].isspace() or current[boundary - 1][-1].isspace()):
                boundary -= 1
                following = ''.join(current[boundary:]) + token
            require(boundary > 0, 'No safe review-caption break fits the requested width')
            rows.append(''.join(current[:boundary]))
            current = current[boundary:]
        current.append(token)
        require(cells(''.join(current)) <= max_cells, 'Review-caption punctuation exceeds line width')
    if current:
        rows.append(''.join(current))
    require(''.join(rows) == text, 'Explicit review wrapping changed authored characters')
    return '\n'.join(rows)
