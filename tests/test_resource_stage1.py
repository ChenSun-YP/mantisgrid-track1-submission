from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def test_reason_only_request_uses_query_semantics(self):
        self.assertTrue(r.reason_only_request(
            "A failure occurred. Please identify the root cause reason."))
        self.assertFalse(r.reason_only_request(
            "A failure occurred. Please identify the root cause component and reason."))

    def test_start_time_change_detects_process_restart(self):
        instruction = ("A failure occurred on March 20, 2022, from 09:00 to 09:30. "
                       "Please identify the root cause reason.")
        lo, _ = r.b1.parse_window(instruction)
        timestamp = int(lo.timestamp())
        frame = pd.DataFrame({
            "timestamp": [timestamp, timestamp + 60],
            "cmdb_id": ["node-1.service-0", "node-1.service-0"],
            "kpi_name": ["container_start_time_seconds"] * 2,
            "value": [100.0, 200.0],
            "source": ["metric_container"] * 2,
        })
        with patch.object(r.b1, "_load_interval", return_value=frame):
            self.assertTrue(r.process_restart_detected(instruction, Path("/unused")))

    def test_hierarchy_requires_replica_agreement(self):
        def candidate(component, level, score, source):
            event = r.Event(source, "container_cpu_usage_seconds",
                            (("container CPU load", 1.0),), score, 100, 100,
                            "increase", 1.0, 5, 1.0, 1.0, False)
            return r.Candidate(component, level, score, event,
                               "container CPU load", (("container CPU load", score),))

        service = candidate("service", "service", 10.0, "service-0")
        pod0 = candidate("service-0", "pod", 10.0, "service-0")
        pod1 = candidate("service-1", "pod", 9.0, "service-1")
        self.assertEqual(r.hierarchy_ordered([service, pod0])[0].component, "service-0")
        self.assertEqual(r.hierarchy_ordered([service, pod0, pod1])[0].component, "service")

    def test_inference_module_has_no_label_input(self):
        source = Path(r.__file__).read_text()
        self.assertNotIn("scoring_points", source)
        self.assertNotIn("query_dev.csv", source)


if __name__ == "__main__":
    unittest.main()
