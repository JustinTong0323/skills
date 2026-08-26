import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import torch
from safetensors.torch import save_file


SCRIPTS = Path(__file__).resolve().parents[1]


def run_tool(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


class ToolTests(unittest.TestCase):
    def test_inventory_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_file({"weight": torch.ones(2, dtype=torch.bfloat16)}, root / "model.safetensors")
            (root / "config.json").write_text('{"model_type":"a","model_type":"b"}\n')
            result = run_tool("preflight.py", str(root), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate JSON key", result.stderr)

    def test_incomplete_checkpoint_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            save_file({"weight": torch.ones(2)}, root / "model.safetensors")
            (root / "model.safetensors.partial").write_text("partial")
            result = run_tool("inventory.py", str(root), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checkpoint contains incomplete files", result.stderr)

    def test_dense_single_file_preflight_and_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["DenseModel"], "model_type": "dense", "num_hidden_layers": 1})
            save_file({"model.layers.0.mlp.up_proj.weight": torch.ones((2, 4), dtype=torch.bfloat16)}, root / "model.safetensors")
            preflight = json.loads(
                run_tool(
                    "preflight.py",
                    str(root),
                    "--modelopt-supported",
                    "yes",
                    "--whole-model-fit",
                    "yes",
                ).stdout
            )
            self.assertEqual(preflight["decision"], "whole_model")
            self.assertFalse(preflight["routed_expert"]["compatible"])
            inventory = json.loads(run_tool("inventory.py", str(root)).stdout)
            self.assertEqual(inventory["safetensors_shard_count"], 1)
            self.assertEqual(inventory["tensor_count"], 1)
            self.assertIsNone(inventory["safetensors_index"])

    def test_inventory_includes_nested_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "evidence"
            nested.mkdir()
            (nested / "report.json").write_text("{}\n")
            inventory = json.loads(run_tool("inventory.py", str(root), "--skip-checkpoint-layout").stdout)
            self.assertEqual([item["name"] for item in inventory["files"]], ["evidence/report.json"])

    def test_fused_routed_preflight_requires_complete_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["MoeModel"], "model_type": "moe", "num_hidden_layers": 2})
            tensors = {}
            for layer in range(2):
                base = f"model.layers.{layer}.mlp.experts"
                tensors[f"{base}.gate_up_proj.weight"] = torch.ones((4, 8, 4), dtype=torch.bfloat16)
                tensors[f"{base}.down_proj.weight"] = torch.ones((4, 4, 4), dtype=torch.bfloat16)
            save_file(tensors, root / "model.safetensors")
            report = json.loads(
                run_tool(
                    "preflight.py",
                    str(root),
                    "--modelopt-supported",
                    "yes",
                    "--whole-model-fit",
                    "no",
                    "--routed-exporter-qualified",
                    "yes",
                    "--expected-routed-layers",
                    "0,1",
                ).stdout
            )
            self.assertEqual(report["decision"], "routed_expert_streaming")
            self.assertTrue(report["routed_expert"]["compatible"])
            self.assertEqual(report["routed_expert"]["layer_count"], 2)

    def test_preflight_does_not_hide_known_unsupported_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["UnknownModel"], "model_type": "unknown"})
            save_file({"weight": torch.ones(2, dtype=torch.float16)}, root / "model.safetensors")
            report = json.loads(run_tool("preflight.py", str(root), "--modelopt-supported", "no").stdout)
            self.assertEqual(report["decision"], "unsupported")

    def test_routed_layout_requires_qualified_exporter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["MoeModel"], "model_type": "moe", "num_hidden_layers": 1})
            save_file(
                {
                    "model.layers.0.mlp.experts.gate_up_proj.weight": torch.ones((4, 8, 4), dtype=torch.bfloat16),
                    "model.layers.0.mlp.experts.down_proj.weight": torch.ones((4, 4, 4), dtype=torch.bfloat16),
                },
                root / "model.safetensors",
            )
            report = json.loads(
                run_tool(
                    "preflight.py",
                    str(root),
                    "--modelopt-supported",
                    "no",
                    "--routed-exporter-qualified",
                    "no",
                ).stdout
            )
            self.assertEqual(report["decision"], "unsupported")

    def test_indexed_checkpoint_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            save_file({"a": torch.ones(2, dtype=torch.float16)}, root / "model-00001-of-00002.safetensors")
            save_file({"b": torch.ones(3, dtype=torch.float16)}, root / "model-00002-of-00002.safetensors")
            write_json(
                root / "model.safetensors.index.json",
                {
                    "metadata": {"total_size": 10},
                    "weight_map": {
                        "a": "model-00001-of-00002.safetensors",
                        "b": "model-00002-of-00002.safetensors",
                    },
                },
            )
            inventory = json.loads(run_tool("inventory.py", str(root)).stdout)
            self.assertEqual(inventory["safetensors_shard_count"], 2)
            self.assertEqual(inventory["tensor_count"], 2)
            self.assertEqual(inventory["indexed_payload_bytes"], 10)

    def test_build_manifest_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            preflight = root / "preflight.json"
            inventory = root / "inventory.json"
            calibration = root / "calibration.json"
            environment = root / "environment.json"
            arguments = root / "arguments.json"
            precision_contract = root / "precision-contract.json"
            topology = root / "topology.json"
            artifact = root / "runner.py"
            modelopt = root / "modelopt"
            modelopt.mkdir()
            subprocess.run(["git", "init", "-q", str(modelopt)], check=True)
            subprocess.run(["git", "-C", str(modelopt), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(modelopt), "config", "user.name", "Test"], check=True)
            (modelopt / "recipe.py").write_text("CONFIG = {}\n")
            subprocess.run(["git", "-C", str(modelopt), "add", "recipe.py"], check=True)
            subprocess.run(["git", "-C", str(modelopt), "commit", "-q", "-m", "fixture"], check=True)
            write_json(preflight, {"decision": "whole_model", "model_type": "dense"})
            write_json(inventory, {"file_count": 0, "files": [], "total_file_bytes": 0})
            write_json(calibration, {"samples": 1, "seed": 1234})
            write_json(environment, {"python": "test"})
            write_json(arguments, {"batch_size": 1})
            write_json(precision_contract, {"default": "W4A16_NVFP4"})
            write_json(topology, {"gpu_count": 1, "gpu_type": "test"})
            artifact.write_text("print('runner')\n")
            first = root / "first.json"
            second = root / "second.json"
            common = [
                "--preflight",
                str(preflight),
                "--source-inventory",
                str(inventory),
                "--modelopt-root",
                str(modelopt),
                "--recipe",
                "official/recipe",
                "--calibration",
                str(calibration),
                "--environment",
                str(environment),
                "--arguments",
                str(arguments),
                "--precision-contract",
                str(precision_contract),
                "--topology",
                str(topology),
                "--artifact",
                str(artifact),
            ]
            run_tool("build_manifest.py", *common, "--output", str(first))
            run_tool("build_manifest.py", *common, "--output", str(second))
            self.assertEqual(first.read_bytes(), second.read_bytes())
            manifest = json.loads(first.read_text())
            digest_payload = dict(manifest)
            digest = digest_payload.pop("manifest_sha256")
            canonical = (json.dumps(digest_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            self.assertEqual(digest, hashlib.sha256(canonical).hexdigest())

    def test_mixed_checkpoint_audit_and_scale_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            write_json(source / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            layer_map = {
                "fp": {"quant_algo": "FP8"},
                "nv": {"group_size": 4, "quant_algo": "W4A16_NVFP4"},
            }
            write_json(
                output / "config.json",
                {
                    "architectures": ["DenseModel"],
                    "model_type": "dense",
                    "quantization_config": {
                        "kv_cache_scheme": {"dynamic": False, "num_bits": 8, "type": "float"},
                        "quant_algo": "MIXED_PRECISION",
                        "quantized_layers": layer_map,
                    },
                },
            )
            source_tensors = {
                "fp.weight": torch.arange(8, dtype=torch.bfloat16).reshape(2, 4),
                "mtp.weight": torch.tensor([1.0, 2.0], dtype=torch.bfloat16),
                "nv.weight": torch.arange(8, dtype=torch.bfloat16).reshape(2, 4),
                "unchanged.weight": torch.tensor([3.0, 4.0], dtype=torch.bfloat16),
            }
            save_file(source_tensors, source / "model.safetensors")
            output_tensors = {
                "fp.input_scale": torch.tensor(2.0),
                "fp.weight": torch.ones((2, 4), dtype=torch.float8_e4m3fn),
                "fp.weight_scale": torch.tensor(1.0),
                "mtp.weight": source_tensors["mtp.weight"],
                "nv.weight": torch.ones((2, 2), dtype=torch.uint8),
                "nv.weight_scale": torch.ones((2, 1), dtype=torch.float8_e4m3fn),
                "nv.weight_scale_2": torch.tensor(1.0),
                "unchanged.weight": source_tensors["unchanged.weight"],
            }
            save_file(output_tensors, output / "model.safetensors")
            write_json(
                output / "hf_quant_config.json",
                {
                    "quantization": {
                        "kv_cache_quant_algo": "FP8",
                        "quant_algo": "MIXED_PRECISION",
                        "quantized_layers": layer_map,
                    }
                },
            )
            report = json.loads(
                run_tool("audit_checkpoint.py", "--source", str(source), "--output-checkpoint", str(output)).stdout
            )
            self.assertEqual(report["algorithm_base_counts"], {"FP8": 1, "W4A16_NVFP4": 1})
            self.assertEqual(report["mtp_tensor_count"], 1)
            self.assertEqual(report["unchanged_tensor_count"], 2)
            self.assertEqual(report["verdict"], "pass")

            output_tensors["nv.weight_scale_2"] = torch.tensor(0.0)
            save_file(output_tensors, output / "model.safetensors")
            failed = run_tool(
                "audit_checkpoint.py",
                "--source",
                str(source),
                "--output-checkpoint",
                str(output),
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("non-positive scale values", failed.stderr)

    def test_w4a4_checkpoint_audit_requires_input_scale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            write_json(source / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            layer_map = {"nv4": {"group_size": 4, "quant_algo": "NVFP4"}}
            quantization = {"quant_algo": "NVFP4", "quantized_layers": layer_map}
            write_json(
                output / "config.json",
                {
                    "architectures": ["DenseModel"],
                    "model_type": "dense",
                    "quantization_config": quantization,
                },
            )
            save_file(
                {"nv4.weight": torch.arange(8, dtype=torch.bfloat16).reshape(2, 4)},
                source / "model.safetensors",
            )
            output_tensors = {
                "nv4.weight": torch.ones((2, 2), dtype=torch.uint8),
                "nv4.weight_scale": torch.ones((2, 1), dtype=torch.float8_e4m3fn),
                "nv4.weight_scale_2": torch.tensor(1.0),
                "nv4.input_scale": torch.tensor(2.0),
            }
            save_file(output_tensors, output / "model.safetensors")
            write_json(output / "hf_quant_config.json", {"quantization": quantization})
            report = json.loads(
                run_tool("audit_checkpoint.py", "--source", str(source), "--output-checkpoint", str(output)).stdout
            )
            self.assertEqual(report["algorithm_base_counts"], {"NVFP4": 1})
            self.assertEqual(report["verdict"], "pass")

            del output_tensors["nv4.input_scale"]
            save_file(output_tensors, output / "model.safetensors")
            failed = run_tool(
                "audit_checkpoint.py",
                "--source",
                str(source),
                "--output-checkpoint",
                str(output),
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("missing quantized tensor", failed.stderr)

    def test_compare_inventories_reports_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.json"
            right = root / "right.json"
            write_json(left, {"file_count": 1, "files": [{"name": "a", "sha256": "1", "size": 1}], "total_file_bytes": 1})
            write_json(right, {"file_count": 1, "files": [{"name": "a", "sha256": "2", "size": 1}], "total_file_bytes": 1})
            result = run_tool("compare_inventories.py", str(left), str(right), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("changed=['a']", result.stderr)


if __name__ == "__main__":
    unittest.main()
