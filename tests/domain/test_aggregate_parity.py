"""Bit-identical parity for aggregates whose internal copying was removed."""

import json
import os
import random
import time
import unittest
from pathlib import Path

from tests.domain import aggregate_parity_rows
from token_meter.domain import agents, aggregates, usage

# Per-section digests of the same computation run against main before the
# set-reuse change; regenerate only from a build whose output is the reference.
GOLDEN = Path(__file__).resolve().with_name("aggregate_parity_golden.json")


def reference_make_usage_provenance(session_ids, estimated_ids=(), available_ids=None,
                                    estimated_cost=0.0, estimated_tokens=0):
    """The copying implementation on main before the set-reuse change."""
    session_ids = set(session_ids or ())
    estimated_ids = set(estimated_ids or ()) & session_ids
    available_ids = (
        session_ids
        if available_ids is None
        else set(available_ids or ()) & session_ids
    )
    estimated_ids &= available_ids
    reported_ids = available_ids - estimated_ids
    unavailable_ids = session_ids - available_ids
    if estimated_ids and reported_ids:
        basis = "mixed"
    elif estimated_ids:
        basis = "local_estimate"
    elif reported_ids:
        basis = "reported"
    else:
        basis = "unavailable"
    return {
        "usage_basis": basis,
        "reported_sessions": len(reported_ids),
        "estimated_sessions": len(estimated_ids),
        "unavailable_sessions": len(unavailable_ids),
        "estimated_cost": float(estimated_cost or 0),
        "estimated_tokens": int(estimated_tokens or 0),
    }


@unittest.skipUnless(hasattr(time, "tzset"), "golden is pinned to UTC via tzset")
class AggregateParityGoldenTests(unittest.TestCase):
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_aggregates_match_the_golden_generated_by_the_copying_build(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        sections = aggregate_parity_rows.compute(aggregates, usage)
        sections["agent_usage"] = aggregate_parity_rows.compute_agent_usage(agents)

        self.assertEqual(aggregate_parity_rows.digests(sections), golden["sha256"])

    def test_fixture_exercises_every_provenance_basis(self):
        sections = aggregate_parity_rows.compute(aggregates, usage)
        model_stats = json.loads(sections["model_stats"])
        bases = {row["usage_basis"] for row in model_stats["models"]}

        self.assertEqual(
            bases, {"reported", "local_estimate", "mixed", "unavailable"},
        )
        self.assertGreater(len(model_stats["models"]), 5)
        self.assertGreater(len(json.loads(sections["monthly"])), 1)
        self.assertGreater(len(json.loads(sections["daily"])), 4)

    def test_agent_fixture_has_shared_and_distinct_scope_selections(self):
        usage_payload = json.loads(aggregate_parity_rows.compute_agent_usage(agents))
        bodies = [
            json.dumps({key: scope[key] for key in ("totals", "models")}, sort_keys=True)
            for scope in usage_payload["scopes"]
        ]

        self.assertGreater(usage_payload["totals"]["agents"], 10)
        self.assertGreater(usage_payload["totals"]["unresolved_agents"], 0)
        self.assertGreater(len(set(bodies)), 10)
        self.assertLess(len(set(bodies)), len(bodies))


class UsageProvenanceReuseTests(unittest.TestCase):
    def test_matches_the_copying_reference_without_mutating_inputs(self):
        rng = random.Random(7)
        shapes = (set, frozenset, list, tuple)
        for _ in range(400):
            universe = ["id-{}".format(index) for index in range(rng.randint(0, 9))]

            def pick():
                values = [value for value in universe if rng.random() < 0.6]
                values.append("stray") if rng.random() < 0.2 else None
                return rng.choice(shapes)(values)

            session_ids, estimated_ids = pick(), pick()
            available_ids = None if rng.random() < 0.3 else pick()
            if rng.random() < 0.1:
                estimated_ids = None
            arguments = (session_ids, estimated_ids, available_ids, rng.random(), rng.randint(0, 9))
            before = [
                (type(value), sorted(value) if value is not None else None)
                for value in arguments[:3]
            ]

            self.assertEqual(
                usage.make_usage_provenance(*arguments),
                reference_make_usage_provenance(*arguments),
            )
            self.assertEqual(
                [
                    (type(value), sorted(value) if value is not None else None)
                    for value in arguments[:3]
                ],
                before,
            )


if __name__ == "__main__":
    unittest.main()
