"""Workflow schemas and the disposition catalogue (read from assignment-a-data/schemas)."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "assignment-a-data" / "schemas"


@dataclass
class Field:
    name: str
    type: str
    required: bool = False
    pii: bool = False
    reference: bool = False
    aliases: list = field(default_factory=list)
    values: list = field(default_factory=list)
    default: str | None = None


@dataclass
class Workflow:
    workflow_id: str
    fields: list[Field]

    @property
    def phone_field(self) -> Field:
        return next(f for f in self.fields if f.type == "phone")

    @property
    def reference_field(self) -> Field | None:
        return next((f for f in self.fields if f.reference), None)


@dataclass
class Disposition:
    code: str
    is_terminal: bool
    is_success: bool


def load_workflows(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> dict[str, Workflow]:
    out = {}
    for p in sorted(Path(schema_dir).glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        fields = [Field(
            name=f["name"], type=f["type"], required=f.get("required", False),
            pii=f.get("pii", False), reference=f.get("reference", False),
            aliases=f.get("aliases", []), values=f.get("values", []), default=f.get("default"),
        ) for f in d["fields"]]
        out[d["workflow_id"]] = Workflow(d["workflow_id"], fields)
    return out


def load_dispositions(schema_dir: Path = DEFAULT_SCHEMA_DIR) -> dict[str, dict[str, Disposition]]:
    out: dict[str, dict[str, Disposition]] = {}
    with open(Path(schema_dir) / "dispositions.csv", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["workflow_id"], {})[r["code"]] = Disposition(
                r["code"], r["is_terminal"].strip().lower() == "true",
                r["is_success"].strip().lower() == "true")
    return out
