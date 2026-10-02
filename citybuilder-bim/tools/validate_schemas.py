#!/usr/bin/env python3
"""Validate SiteBuilder JSON files against the schemas in ../schema.

Usage:
  python3 validate_schemas.py <schema-name> <file.json> [more files...]
  python3 validate_schemas.py --auto <file.json> [...]   # pick schema by file name

schema-name is one of: elements, step_library, mapping_rules, element_step_map,
scenario, sequence.  --auto matches 'elements.json' -> elements, etc.
"""
import json
import os
import sys

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema")
BASE = "https://sitebuilder.dev/schema/"
NAMES = ["common", "elements", "step_library", "mapping_rules", "element_step_map", "scenario", "sequence",
         "recipe", "manual_sequence", "visual_kit"]


def load_registry():
    registry = Registry()
    for name in NAMES:
        path = os.path.join(SCHEMA_DIR, f"{name}.schema.json")
        with open(path, encoding="utf-8") as fh:
            schema = json.load(fh)
        registry = registry.with_resource(BASE + f"{name}.schema.json", Resource.from_contents(schema))
    return registry


def validator_for(name):
    registry = load_registry()
    with open(os.path.join(SCHEMA_DIR, f"{name}.schema.json"), encoding="utf-8") as fh:
        schema = json.load(fh)
    return Draft202012Validator(schema, registry=registry)


def guess_schema(path):
    stem = os.path.basename(path).replace(".json", "")
    for name in NAMES[1:]:
        if stem == name or stem.endswith("_" + name) or stem.startswith(name + "_"):
            return name
    if stem == "plan_export":
        return "element_step_map"
    if stem.startswith("rec_"):
        return "recipe"
    if stem == "kit_manifest":
        return "visual_kit"
    return None


def validate_file(name, path):
    v = validator_for(name)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    errors = sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path))
    for err in errors[:25]:
        loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
        print(f"  {path}: {loc}: {err.message}")
    if len(errors) > 25:
        print(f"  ... {len(errors) - 25} more errors")
    return not errors


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    ok = True
    if argv[0] == "--auto":
        for path in argv[1:]:
            name = guess_schema(path)
            if not name:
                print(f"  {path}: cannot guess schema from file name")
                ok = False
                continue
            good = validate_file(name, path)
            print(f"{'OK  ' if good else 'FAIL'} {name:17s} {path}")
            ok &= good
    else:
        name = argv[0]
        for path in argv[1:]:
            good = validate_file(name, path)
            print(f"{'OK  ' if good else 'FAIL'} {name:17s} {path}")
            ok &= good
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
