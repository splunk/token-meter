import hashlib
import math
import random
import statistics
import unittest

import meter

MATCHED_PACE_MIN_PAIRS = meter.MATCHED_PACE_MIN_PAIRS
MATCHED_PACE_MIN_COVERAGE = meter.MATCHED_PACE_MIN_COVERAGE


# Frozen copy of the pre-optimization implementation. The optimized code must
# stay bit-identical to it; persisted pair caches depend on that.
def ref_pace_log_distance(left, right, max_distance):
    left = max(1.0, float(left or 0))
    right = max(1.0, float(right or 0))
    distance = abs(math.log(left / right, 2))
    return None if distance > max_distance else distance / max_distance


def ref_pace_match_distance(left, right):
    """Return workload distance, or None when two completed turns are not comparable."""
    left_tools = int(left.get("tool_calls") or 0)
    right_tools = int(right.get("tool_calls") or 0)
    if bool(left_tools) != bool(right_tools):
        return None
    # Reject on the first failing dimension instead of scoring all of them and
    # discarding the result. Measured over real histories, output rejects the
    # most candidates and input almost none, so the cheap discriminating gates
    # run first and the cost of a rejected pair stays low.
    output_distance = ref_pace_log_distance(
        left.get("output_tokens"), right.get("output_tokens"), 2.0,
    )
    if output_distance is None:
        return None
    peak_distance = ref_pace_log_distance(
        left.get("peak_input_tokens") or left.get("input_tokens"),
        right.get("peak_input_tokens") or right.get("input_tokens"), 2.0,
    )
    if peak_distance is None:
        return None
    input_distance = ref_pace_log_distance(
        left.get("input_tokens"), right.get("input_tokens"), 3.0,
    )
    if input_distance is None:
        return None
    model_distance = ref_pace_log_distance(
        left.get("model_calls") or 1, right.get("model_calls") or 1, 2.0,
    )
    if model_distance is None:
        return None
    left_input = max(1, int(left.get("input_tokens") or 0))
    right_input = max(1, int(right.get("input_tokens") or 0))
    left_cache = min(1.0, float(left.get("cache_read_tokens") or 0) / left_input)
    right_cache = min(1.0, float(right.get("cache_read_tokens") or 0) / right_input)
    cache_distance = abs(left_cache - right_cache)
    if cache_distance > 0.60:
        return None
    score = peak_distance * 0.28
    score += input_distance * 0.17
    score += output_distance * 0.22
    score += model_distance * 0.16
    if left_tools:
        tool_distance = ref_pace_log_distance(left_tools, right_tools, 2.0)
        if tool_distance is None:
            return None
        score += tool_distance * 0.12
    score += (cache_distance / 0.60) * 0.05
    recency_days = abs(float(left.get("ts") or 0) - float(right.get("ts") or 0)) / 86400.0
    score += min(1.0, recency_days / 90.0) * 0.03
    return score


def ref_percentile(values, quantile):
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))]


def ref_bootstrap_median_interval(values, seed_key, repetitions=400):
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    seed = int(hashlib.sha256(str(seed_key).encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    medians = []
    for _ in range(repetitions):
        medians.append(statistics.median(rng.choice(values) for _ in values))
    return ref_percentile(medians, 0.025), ref_percentile(medians, 0.975)


def ref_matched_pace_comparison(a_id, a_samples, b_id, b_samples, distance_cache=None):
    """Compare two model-runtime histories using deterministic workload matching."""
    def usable(sample):
        return (
            float(sample.get("duration_s") or 0) > 0
            and int(sample.get("input_tokens") or 0) > 0
            and int(sample.get("output_tokens") or 0) > 0
        )

    a_rows = [sample for sample in (a_samples or []) if usable(sample)]
    b_rows = [sample for sample in (b_samples or []) if usable(sample)]
    result = {
        "a_id": a_id, "b_id": b_id,
        "a_samples": len(a_rows), "b_samples": len(b_rows),
        "matched_pairs": 0, "coverage": 0.0, "pace_ratio": 0.0,
        "ci_low": 0.0, "ci_high": 0.0, "available": False,
    }
    smaller = min(len(a_rows), len(b_rows))
    if smaller < MATCHED_PACE_MIN_PAIRS:
        result["reason"] = f"needs {MATCHED_PACE_MIN_PAIRS} timed turns per runtime"
        return result

    candidates = []
    for a_index, left in enumerate(a_rows):
        for b_index, right in enumerate(b_rows):
            cache_key = (id(left), id(right))
            if distance_cache is not None and cache_key in distance_cache:
                distance = distance_cache[cache_key]
            else:
                distance = ref_pace_match_distance(left, right)
                if distance_cache is not None:
                    distance_cache[cache_key] = distance
            if distance is not None:
                candidates.append((distance, abs(float(left.get("ts") or 0) - float(right.get("ts") or 0)),
                                   a_index, b_index))
    candidates.sort()
    used_a = set()
    used_b = set()
    ratios = []
    for _, _, a_index, b_index in candidates:
        if a_index in used_a or b_index in used_b:
            continue
        used_a.add(a_index)
        used_b.add(b_index)
        left_duration = float(a_rows[a_index].get("duration_s") or 0)
        right_duration = float(b_rows[b_index].get("duration_s") or 0)
        ratios.append(right_duration / left_duration)

    matched_pairs = len(ratios)
    coverage = matched_pairs / smaller if smaller else 0.0
    result["matched_pairs"] = matched_pairs
    result["coverage"] = coverage
    if ratios:
        result["pace_ratio"] = statistics.median(ratios)
    if matched_pairs >= MATCHED_PACE_MIN_PAIRS:
        result["ci_low"], result["ci_high"] = ref_bootstrap_median_interval(
            ratios, f"{a_id}|{b_id}|{matched_pairs}"
        )
    if matched_pairs < MATCHED_PACE_MIN_PAIRS:
        result["reason"] = f"only {matched_pairs} comparable turns; needs {MATCHED_PACE_MIN_PAIRS}"
    elif coverage < MATCHED_PACE_MIN_COVERAGE:
        result["reason"] = f"only {round(coverage * 100)}% of the smaller history overlaps"
    else:
        result["available"] = True
        result["reason"] = ""
    return result


def _sample(rng, sparse=False):
    def pick(choices):
        return rng.choice(choices)
    sample = {
        "duration_s": pick([0, 0.5, 3.0, rng.uniform(0.1, 400)]),
        "ts": pick([0, None, rng.uniform(1.7e9, 1.8e9)]),
        "input_tokens": pick([0, 1, rng.randint(1, 400_000)]),
        "peak_input_tokens": pick([None, 0, rng.randint(1, 400_000)]),
        "cache_read_tokens": pick([None, 0, rng.randint(0, 500_000)]),
        "output_tokens": pick([0, 1, rng.randint(1, 40_000)]),
        "tool_calls": pick([None, 0, rng.randint(0, 40)]),
        "model_calls": pick([None, 0, rng.randint(1, 30)]),
    }
    if sparse:
        for key in list(sample):
            if rng.random() < 0.2:
                sample.pop(key)
    return sample


class MatchedPaceParityTests(unittest.TestCase):
    def test_distance_is_bit_identical_including_edge_values(self):
        rng = random.Random(7)
        samples = [_sample(rng, sparse=index % 3 == 0) for index in range(300)]
        for left in samples[:150]:
            for right in samples[150:]:
                self.assertEqual(
                    repr(meter.pace_match_distance(left, right)),
                    repr(ref_pace_match_distance(left, right)),
                )

    def test_bootstrap_draws_are_bit_identical(self):
        rng = random.Random(11)
        for size in (1, 2, 3, 20, 21, 64, 257):
            values = [rng.uniform(0.1, 9) for _ in range(size)]
            for seed in ("a|b|20", "x|y|21", size):
                self.assertEqual(
                    meter._bootstrap_median_interval(values, seed, repetitions=60),
                    ref_bootstrap_median_interval(values, seed, repetitions=60),
                )
        self.assertEqual(meter._bootstrap_median_interval([], "s"), (0.0, 0.0))

    def test_comparison_is_identical_with_and_without_a_shared_cache(self):
        rng = random.Random(3)
        for trial in range(25):
            a = [_sample(rng) for _ in range(rng.randint(0, 90))]
            b = [_sample(rng, sparse=trial % 2 == 0) for _ in range(rng.randint(0, 90))]
            expected = ref_matched_pace_comparison("a", a, "b", b)
            self.assertEqual(meter.matched_pace_comparison("a", a, "b", b), expected)
            shared = {}
            for _ in range(2):
                self.assertEqual(
                    meter.matched_pace_comparison("a", a, "b", b, distance_cache=shared),
                    expected,
                )

    def test_comparison_matches_on_dense_comparable_histories(self):
        rng = random.Random(5)

        def turn(scale):
            tokens = int(rng.uniform(800, 1200) * scale)
            return {
                "duration_s": rng.uniform(2, 30) * scale, "ts": 1.75e9 + rng.uniform(0, 8e6),
                "input_tokens": tokens * 10, "peak_input_tokens": tokens * 12,
                "cache_read_tokens": tokens * 6, "output_tokens": tokens,
                "tool_calls": rng.randint(1, 6), "model_calls": rng.randint(1, 5),
            }

        a = [turn(1.0) for _ in range(120)]
        b = [turn(1.3) for _ in range(140)]
        result = meter.matched_pace_comparison("a", a, "b", b)
        self.assertTrue(result["available"])
        self.assertEqual(result, ref_matched_pace_comparison("a", a, "b", b))


if __name__ == "__main__":
    unittest.main()
