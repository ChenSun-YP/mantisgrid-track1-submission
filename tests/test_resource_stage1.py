from __future__ import annotations

import sys
from pathlib import Path
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rca import resource_stage1 as r  # noqa: E402


def series(values, kpi="container_memory_rss"):
    return pd.DataFrame({"timestamp": range(100, 100 + 60 * len(values), 60),
                         "component": "service-0", "kpi_name": kpi, "value": values})


class ResourceStage1Test(unittest.TestCase):
    def test_persistent_increase_has_local_onset(self):
        event = r._local_event(series([1] * 5 + [8] * 6),
                               pd.Series({"median": 1, "mad": 0, "std": 0}), 100, 1000)
        self.assertIsNotNone(event)
        self.assertEqual(event.direction, "increase")
        self.assertEqual(event.onset, 400)
        self.assertTrue(event.zero_mad_state_change)

    def test_persistent_decrease_is_detected(self):
        event = r._local_event(series([8] * 5 + [1] * 6),
                               pd.Series({"median": 8, "mad": 1, "std": 1}), 100, 1000)
        self.assertIsNotNone(event)
        self.assertEqual(event.direction, "decrease")

    def test_isolated_spike_is_not_a_change(self):
        event = r._local_event(series([1] * 5 + [20] + [1] * 5),
                               pd.Series({"median": 1, "mad": 1, "std": 1}), 100, 1000)
        self.assertIsNone(event)

    def test_hierarchy_and_resource_scope(self):
        self.assertEqual(r.service_of("checkout-service-2"), "checkout-service")
        self.assertIsNone(r.service_of("node-6"))
        self.assertEqual(r.kpi_reason_weights("container_network_receive_MB.eth0", "svc-0"), ())
        reasons = {x[0] for x in r.kpi_reason_weights("system.io.r_s", "node-1")}
        self.assertEqual(reasons, {"node disk read I/O consumption"})

    def test_inference_module_has_no_label_input(self):
        source = Path(r.__file__).read_text()
        self.assertNotIn("scoring_points", source)
        self.assertNotIn("query_dev.csv", source)


if __name__ == "__main__":
    unittest.main()
