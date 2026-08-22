#!/usr/bin/env python3
"""One-off live probe for SP2-A supplement case selection. Not an acceptance check."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from sp_memory.kg_sparql import (  # noqa: E402
    LiveSparqlClient,
    build_entity_search_request,
    logical_action_id,
    retry_with_backoff,
)

ENDPOINT = "http://localhost:8890/sparql"
CASES = [
    ("head", "m.02mjmr", "people.person.place_of_birth"),
    ("tail", "m.02hrh0_", "people.person.place_of_birth"),
    ("head", "m.02hrh0_", "type.object.name"),
    ("head", "m.02hrh0_", "location.location.containedby"),
    ("head", "m.02hrh0_", "location.location.time_zones"),
    ("head", "m.02hrh0_", "location.location.nearby_airports"),
    ("tail", "m.02hrh", "people.person.place_of_birth"),
]


def main() -> int:
    client = LiveSparqlClient(
        endpoint=ENDPOINT,
        allowed_endpoints=[ENDPOINT],
        timeout_sec=20,
        max_retries=1,
        retry_backoff_sec=[0.5],
    )
    rows = []
    for direction, entity, relation in CASES:
        request = build_entity_search_request(entity, relation, direction, endpoint=ENDPOINT)
        exchanges = retry_with_backoff(
            client,
            request,
            logical_action_id=logical_action_id(
                "sp2a.supp.probe",
                "p0001",
                "EXPAND",
                {"entity": entity, "relation": relation, "direction": direction},
            ),
        )
        final = exchanges[-1]
        targets = [item.value for item in final.targets][:12]
        row = {
            "direction": direction,
            "entity": entity,
            "relation": relation,
            "status": final.status.value,
            "http_status": final.http_status,
            "n": len(final.targets),
            "targets": targets,
            "head": request.head,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
