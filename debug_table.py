#!/usr/bin/env python
from app.extractor import find_labeled_amount, BASE_LABELS, VAT_LABELS, TOTAL_LABELS, _fold, _label_regex

# Test the exact failing case
text = """baSe | IvA | totaL
22,67 | 1.215,48 | 48.464,71"""

print(f"Text:\n{text}\n")

# Test each label set
for label_set, name in [(BASE_LABELS, "BASE"), (VAT_LABELS, "VAT"), (TOTAL_LABELS, "TOTAL")]:
    print(f"\n{name} labels:")
    result = find_labeled_amount(text, label_set)
    print(f"  Result: {result}")
    
    # Debug: check if any label matches
    lines = text.splitlines()
    for label in label_set:
        pattern = _label_regex(label)
        if pattern:
            for idx, line in enumerate(lines):
                folded = _fold(line)
                match = pattern.search(folded)
                if match:
                    print(f"    Label '{label}' matches line {idx} (folded='{folded}')")
