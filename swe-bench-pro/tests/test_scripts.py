import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class ScriptTests(unittest.TestCase):
    def test_convert_predictions_distinguishes_missing_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            expected = tmp / "expected.jsonl"
            expected.write_text(
                "".join(json.dumps({"instance_id": value}) + "\n" for value in "abc")
            )
            predictions = tmp / "preds.json"
            predictions.write_text(
                json.dumps(
                    {
                        "a": {"instance_id": "a", "model_patch": "diff --git a/a b/a"},
                        "b": {"instance_id": "b", "model_patch": ""},
                    }
                )
            )
            output = tmp / "patches.json"
            result = subprocess.run(
                [
                    sys.executable,
                    ROOT / "scripts/convert_predictions.py",
                    "--input",
                    predictions,
                    "--output",
                    output,
                    "--prefix",
                    "test",
                    "--expected",
                    expected,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            summary = json.loads(result.stdout)
            self.assertEqual(summary["records"], 2)
            self.assertEqual(summary["nonempty"], 1)
            self.assertEqual(summary["empty"], 1)
            self.assertEqual(summary["missing"], 1)
            self.assertEqual(summary["missing_or_empty"], 2)

    def test_summarize_pass_at_k(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            expected = tmp / "expected.jsonl"
            expected.write_text(
                "".join(json.dumps({"instance_id": value}) + "\n" for value in "abc")
            )
            run1 = self.make_run(tmp, "run1", {"a": True, "b": False})
            run2 = self.make_run(tmp, "run2", {"b": True, "c": False})
            result = subprocess.run(
                [
                    sys.executable,
                    ROOT / "scripts/summarize_pass_at_k.py",
                    "--expected",
                    expected,
                    "--run",
                    run1,
                    "--run",
                    run2,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["k"], 2)
            self.assertEqual(report["resolved_union"], 2)
            self.assertEqual(report["empirical_pass_at_2"], 2 / 3)
            self.assertEqual(report["resolved_by_run_count"], {"0": 1, "1": 2, "2": 0})

    def test_summarize_pass_at_k_rejects_incomplete_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            expected = tmp / "expected.jsonl"
            expected.write_text(json.dumps({"instance_id": "a"}) + "\n")
            run = self.make_run(tmp, "run", {"a": True})
            (run / "evaluation/eval_results.json").write_text("{}\n")
            result = subprocess.run(
                [
                    sys.executable,
                    ROOT / "scripts/summarize_pass_at_k.py",
                    "--expected",
                    expected,
                    "--run",
                    run,
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing results", result.stderr)

    def test_summarize_pass_at_k_rejects_duplicate_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            expected = tmp / "expected.jsonl"
            expected.write_text(json.dumps({"instance_id": "a"}) + "\n")
            run = self.make_run(tmp, "run", {"a": True})
            result = subprocess.run(
                [
                    sys.executable,
                    ROOT / "scripts/summarize_pass_at_k.py",
                    "--expected",
                    expected,
                    "--run",
                    run,
                    "--run",
                    run,
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be distinct", result.stderr)

    def test_strict_summary_rejects_incomplete_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            expected = tmp / "expected.jsonl"
            expected.write_text(json.dumps({"instance_id": "a"}) + "\n")
            run = self.make_run(tmp, "run", {"a": True})
            (run / "evaluation/eval_results.json").write_text("{}\n")
            result = subprocess.run(
                [
                    sys.executable,
                    ROOT / "scripts/summarize_results.py",
                    "--eval-results",
                    run / "evaluation/eval_results.json",
                    "--expected",
                    expected,
                    "--predictions",
                    run / "patches.json",
                    "--require-complete-submitted",
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing evaluation results", result.stderr)

    @staticmethod
    def make_run(root, name, results):
        run = root / name
        evaluation = run / "evaluation"
        evaluation.mkdir(parents=True)
        patches = [
            {
                "instance_id": instance_id,
                "patch": f"diff --git a/{instance_id} b/{instance_id}",
                "prefix": name,
            }
            for instance_id in results
        ]
        (run / "patches.json").write_text(json.dumps(patches))
        (evaluation / "eval_results.json").write_text(json.dumps(results))
        return run


if __name__ == "__main__":
    unittest.main()
