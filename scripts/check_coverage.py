#!/usr/bin/env python3
"""Check a study-notes audit ledger, not the truth or completeness of notes."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys


def markdown_headings(text):
    """Collect ATX headings outside fenced code blocks."""
    headings = []
    fence_char = None
    fence_size = 0
    for line in text.splitlines():
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker, suffix = fence.groups()
            if fence_char is None:
                fence_char, fence_size = marker[0], len(marker)
            elif (
                marker[0] == fence_char
                and len(marker) >= fence_size
                and not suffix.strip()
            ):
                fence_char = None
            continue
        if fence_char is None:
            heading = re.match(r"^ {0,3}#{1,6}[ \t]+(.+?)\s*$", line)
            if heading:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(1))
                headings.append(title)
    return Counter(headings)


def validate(data, base_dir):
    """Return structural errors and declared gaps; never write input files."""
    errors = []
    gaps = []
    if not isinstance(data, dict):
        return ["Ledger must be a JSON object."], gaps

    def require_text(record, field, label):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}: {field} must be nonempty text.")
            return ""
        return value.strip()

    def records(field, allow_empty=False):
        values = data.get(field)
        if not isinstance(values, list) or (not values and not allow_empty):
            errors.append(f"{field}: expected {'a' if allow_empty else 'a nonempty'} list.")
            return {}
        indexed = {}
        for index, record in enumerate(values):
            label = f"{field}[{index}]"
            if not isinstance(record, dict):
                errors.append(f"{label}: expected an object.")
                continue
            identifier = require_text(record, "id", label)
            if identifier in indexed:
                errors.append(f"{label}: duplicate id {identifier}.")
            elif identifier:
                indexed[identifier] = record
        return indexed

    heading_cache = {}

    def check_target(record, label):
        target = record.get("target")
        if not isinstance(target, dict):
            errors.append(f"{label}: target must contain file and heading.")
            return
        file_name = require_text(target, "file", label + ".target")
        heading = require_text(target, "heading", label + ".target")
        if not file_name or not heading:
            return
        path = Path(file_name)
        if path.is_absolute():
            errors.append(f"{label}: target file must be relative to the ledger.")
            return
        path = (Path(base_dir) / path).resolve()
        if path.suffix.lower() != ".md":
            errors.append(f"{label}: target must be a Markdown file.")
            return
        if path not in heading_cache:
            try:
                heading_cache[path] = markdown_headings(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError) as error:
                errors.append(f"{label}: cannot read target {file_name}: {error}")
                return
        count = heading_cache[path][heading]
        if count != 1:
            errors.append(f"{label}: heading {heading!r} occurs {count} times in {file_name}.")

    require_text(data, "scope", "ledger")
    sources = records("sources")
    units = records("units")
    items = records("items", allow_empty=True)
    unit_counts = Counter()
    source_counts = Counter()
    for identifier, source in sources.items():
        require_text(source, "location", identifier)
        access = source.get("access")
        if access not in ("complete", "partial", "unavailable"):
            errors.append(f"{identifier}: invalid access status.")
        elif access != "complete":
            require_text(source, "reason", identifier)
            gaps.append(f"{identifier}: source access is {access}.")

    for identifier, unit in units.items():
        require_text(unit, "locator", identifier)
        source_id = require_text(unit, "source", identifier)
        source = sources.get(source_id)
        if source is None:
            errors.append(f"{identifier}: unknown source {source_id}.")
        else:
            source_counts[source_id] += 1
            if source.get("access") == "unavailable" and unit.get("status") == "read":
                errors.append(f"{identifier}: cannot read an unavailable source.")
        status = unit.get("status")
        if status not in ("read", "excluded", "unavailable"):
            errors.append(f"{identifier}: invalid unit status.")
        elif status != "read":
            require_text(unit, "reason", identifier)
            if status == "unavailable":
                gaps.append(f"{identifier}: source unit is unavailable.")
            elif unit.get("basis") not in ("out_of_scope", "nonknowledge"):
                errors.append(f"{identifier}: invalid unit exclusion basis.")

    for identifier, item in items.items():
        require_text(item, "point", identifier)
        require_text(item, "locator", identifier)
        unit_id = require_text(item, "unit", identifier)
        unit = units.get(unit_id)
        if unit is None:
            errors.append(f"{identifier}: unknown unit {unit_id}.")
        else:
            unit_counts[unit_id] += 1
            if unit.get("status") != "read":
                errors.append(f"{identifier}: items must belong to a read unit.")
        status = item.get("status")
        if status in ("covered", "merged"):
            check_target(item, identifier)
        elif status in ("omitted", "gap"):
            require_text(item, "reason", identifier)
            if status == "gap":
                gaps.append(f"{identifier}: knowledge point is unresolved.")
            elif item.get("basis") not in ("out_of_scope", "nonknowledge", "duplicate"):
                errors.append(f"{identifier}: invalid omission basis.")
            elif item.get("basis") == "duplicate":
                check_target(item, identifier)
        else:
            errors.append(f"{identifier}: invalid item status.")

    for identifier, unit in units.items():
        if unit.get("status") == "read" and not unit_counts[identifier]:
            errors.append(f"{identifier}: read unit has no knowledge points.")
    for identifier in sources:
        if not source_counts[identifier]:
            errors.append(f"{identifier}: source has no inventoried units.")
    return errors, gaps


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ledger", type=Path, help="Path to the audit JSON file.")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
        errors, gaps = validate(data, args.ledger.resolve().parent)
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        print(f"ERROR: Cannot read ledger: {error}", file=sys.stderr)
        return 1
    for error in errors:
        print(f"ERROR: {error}")
    for gap in gaps:
        print(f"GAP: {gap}")
    if errors:
        return 1
    if gaps:
        print("PARTIAL: Declared source or knowledge gaps remain.")
        return 2
    print("OK: Ledger structure and note targets pass. Source completeness is not proven.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
