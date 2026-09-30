#!/usr/bin/env python
from app.extractor import find_labeled_amount, TOTAL_LABELS, _fold, _label_regex
import re

text = """base imponible (eur) 0,00
total iva 0,00
total 0,01"""

lines = text.splitlines()
print('Lines:', lines)
print('TOTAL_LABELS:', TOTAL_LABELS)
print()

for label in TOTAL_LABELS:
    pattern = _label_regex(label)
    print(f'Label: "{label}"')
    print(f'Pattern: {pattern.pattern if pattern else None}')
    if pattern:
        for idx, line in enumerate(lines):
            folded = _fold(line)
            match = pattern.search(folded)
            if match:
                print(f'  Line {idx} (folded="{folded}"): MATCH at {match.span()}')
    print()

print('Result from find_labeled_amount:', find_labeled_amount(text, TOTAL_LABELS))
