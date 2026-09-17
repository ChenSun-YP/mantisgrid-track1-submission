from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from rca import glm_escalation as g
from rca import resource_stage1 as r
from run import Solution, ensure_evidence_sections


def candidate(component="service-0", level="pod", score=10.0, reason_score=8.0):
    event = r.Event(component, "container_cpu_usage_seconds",
                    (("container CPU load", 1.0),), score, 100, 100,
                    "increase", 1.0, 5, 1.0, 1.0, False)
    return r.Candidate(component, level, score, event, "container CPU load",
                       (("container CPU load", reason_score),
                        ("container memory load", reason_score * 0.9)))


class GlmEscalationTest(unittest.TestCase):
    def test_hypotheses_are_grounded_and_bounded(self):
        hypotheses, evidence = g.build_hypotheses(
            [candidate(f"service-{i}", score=10 - i) for i in range(7)])
        self.assertEqual(len(hypotheses), 5)
        self.assertEqual(hypotheses[0]["hypothesis_id"], "H1")
        self.assertEqual(hypotheses[0]["source_event"]["kpi"],
                         "container_cpu_usage_seconds")
        self.assertTrue(hypotheses[0]["supporting_evidence_ids"])
        self.assertTrue(evidence)

    def test_ambiguity_gate_reports_close_component_and_reason(self):
        hypotheses, _ = g.build_hypotheses(
            [candidate("service-0", score=10), candidate("service-1", score=9)])
        reasons = g.should_escalate(hypotheses, 1)
        self.assertIn("top component scores are close", reasons)
        self.assertIn("top reason alternatives are close", reasons)

    def test_invalid_model_selection_returns_exact_deterministic_answer(self):
        baseline = Solution("LOCKED", "deterministic evidence")
        response = {"model": g.PRIMARY_MODEL,
                    "result": {"selected_hypotheses": ["UNKNOWN"],
                               "decisive_evidence_ids": [], "explanation": "bad"},
                    "usage": {g.PRIMARY_MODEL: {"prompt_tokens": 1,
                                                "completion_tokens": 1, "calls": 1}}}
        with patch.dict(os.environ, {"FEATHERLESS_API_KEY": "test"}), \
             patch.object(g.deterministic, "solve", return_value=baseline), \
             patch.object(g.deterministic, "generate_candidates",
                          return_value=[candidate(), candidate("service-1", score=9)]), \
             patch.object(g, "call_glm", return_value=response):
            result = g.solve("one failure", Path("/unused"), {})
        self.assertEqual(result.prediction, "LOCKED")
        self.assertIn("Deterministic fallback retained", result.evidence)

    def test_timeout_returns_exact_deterministic_answer(self):
        baseline = Solution("LOCKED", "deterministic evidence")
        with patch.dict(os.environ, {"FEATHERLESS_API_KEY": "test"}), \
             patch.object(g.deterministic, "solve", return_value=baseline), \
             patch.object(g.deterministic, "generate_candidates",
                          return_value=[candidate(), candidate("service-1", score=9)]), \
             patch.object(g, "call_glm", side_effect=TimeoutError("late")):
            result = g.solve("one failure", Path("/unused"), {})
        self.assertEqual(result.prediction, "LOCKED")
        self.assertIn("TimeoutError", result.evidence)

    def test_process_restart_reason_is_carried_into_hypothesis(self):
        baseline = Solution(
            '```json\n{"1": {"root cause occurrence datetime": "2022-03-20 09:01:00", '
            '"root cause component": "service-0", "root cause reason": '
            '"container process termination"}}\n```',
            "Process restart override: yes")
        hypotheses, evidence = g.build_hypotheses([candidate()])
        g._apply_deterministic_overrides(baseline, hypotheses, evidence)
        self.assertEqual(hypotheses[0]["reason"], "container process termination")
        self.assertTrue(any(item["kind"] == "process_restart" for item in evidence))

    def test_model_catalog_selects_fallback_before_single_completion(self):
        model_list = {"data": [{"id": g.FALLBACK_MODEL}]}
        completion = {"choices": [{"message": {"content":
                      '{"selected_hypotheses":["H1"],"decisive_evidence_ids":[], '
                      '"explanation":"supported"}'}}], "usage": {}}
        with patch.dict(os.environ, {"FEATHERLESS_API_KEY": "test"}), \
             patch.object(g, "_request_json", side_effect=[model_list, completion]) as request:
            result = g.call_glm([], [], 1)
        self.assertEqual(result["model"], g.FALLBACK_MODEL)
        self.assertEqual(request.call_count, 2)
        self.assertIsNone(request.call_args_list[0].args[2])
        self.assertIsNotNone(request.call_args_list[1].args[2])
        self.assertEqual(request.call_args_list[1].args[1]["User-Agent"],
                         "MantisGrid-Track1/1.0")
        self.assertEqual(request.call_args_list[1].args[2]["max_tokens"], 1024)

    def test_valid_selection_uses_only_precomputed_answer_fields(self):
        hypotheses, evidence = g.build_hypotheses([candidate()])
        response = {"result": {"selected_hypotheses": ["H1"],
                               "decisive_evidence_ids": ["E1"],
                               "explanation": "supported"}}
        with patch.object(g.b2, "_registry", return_value=["service-0"]):
            answers, decisive = g._validated_selection(
                response, hypotheses, evidence, 1, Path("/unused"), "one failure")
        self.assertEqual(answers[0]["component"], hypotheses[0]["component"])
        self.assertEqual(answers[0]["datetime"], hypotheses[0]["deterministic_onset"])
        self.assertEqual(decisive, ["E1"])

    def test_missing_key_and_unambiguous_case_do_not_call_model(self):
        baseline = Solution("LOCKED", "deterministic evidence")
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(g.deterministic, "solve", return_value=baseline), \
             patch.object(g.deterministic, "generate_candidates",
                          return_value=[candidate()]), \
             patch.object(g, "call_glm") as call:
            result = g.solve("one failure", Path("/unused"), {})
        self.assertEqual(result.prediction, "LOCKED")
        call.assert_not_called()

    def test_required_evidence_headings_are_appended_without_replacement(self):
        original = "## Answer\n\nkept"
        rendered = ensure_evidence_sections(original)
        self.assertIn(original, rendered)
        for heading in ("## Answer", "## Confidence", "## Evidence", "## Ruled out"):
            self.assertIn(heading, rendered)


if __name__ == "__main__":
    unittest.main()
