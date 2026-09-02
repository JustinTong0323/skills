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


def write_checkpoint(root: Path, config: dict, tensors: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "config.json", config)
    save_file(tensors, root / "model.safetensors")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args], check=True)


class ToolTests(unittest.TestCase):
    def test_preflight_rejects_duplicate_json_keys(self) -> None:
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
            write_checkpoint(
                root,
                {"architectures": ["DenseModel"], "model_type": "dense", "num_hidden_layers": 1},
                {"model.layers.0.mlp.up_proj.weight": torch.ones((2, 4), dtype=torch.bfloat16)},
            )
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
            write_json(
                root / "config.json", {"architectures": ["MoeModel"], "model_type": "moe", "num_hidden_layers": 2}
            )
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
            write_json(
                root / "config.json", {"architectures": ["MoeModel"], "model_type": "moe", "num_hidden_layers": 1}
            )
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
            git(modelopt, "init", "-q")
            git(modelopt, "config", "user.email", "test@example.com")
            git(modelopt, "config", "user.name", "Test")
            (modelopt / "recipe.py").write_text("CONFIG = {}\n")
            git(modelopt, "add", "recipe.py")
            git(modelopt, "commit", "-q", "-m", "fixture")
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
                "--source-repository",
                "example/repo",
                "--source-revision",
                "abc123",
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
                "nv": {"group_size": 16, "quant_algo": "W4A16_NVFP4"},
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
                "nv.weight": torch.arange(32, dtype=torch.bfloat16).reshape(2, 16),
                "unchanged.weight": torch.tensor([3.0, 4.0], dtype=torch.bfloat16),
            }
            save_file(source_tensors, source / "model.safetensors")
            output_tensors = {
                "fp.input_scale": torch.tensor(2.0),
                "fp.weight": torch.ones((2, 4), dtype=torch.float8_e4m3fn),
                "fp.weight_scale": torch.tensor(1.0),
                "mtp.weight": source_tensors["mtp.weight"],
                "nv.weight": torch.ones((2, 8), dtype=torch.uint8),
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
            layer_map = {"nv4": {"group_size": 16, "quant_algo": "NVFP4"}}
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
                {"nv4.weight": torch.arange(32, dtype=torch.bfloat16).reshape(2, 16)},
                source / "model.safetensors",
            )
            output_tensors = {
                "nv4.weight": torch.ones((2, 8), dtype=torch.uint8),
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

    def test_nvfp4_group_size_must_be_16(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            write_json(source / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            layer_map = {"nv": {"group_size": 8, "quant_algo": "W4A16_NVFP4"}}
            quantization = {"quant_algo": "W4A16_NVFP4", "quantized_layers": layer_map}
            write_json(
                output / "config.json",
                {
                    "architectures": ["DenseModel"],
                    "model_type": "dense",
                    "quantization_config": quantization,
                },
            )
            save_file({"nv.weight": torch.zeros((2, 16), dtype=torch.bfloat16)}, source / "model.safetensors")
            save_file(
                {
                    "nv.weight": torch.ones((2, 8), dtype=torch.uint8),
                    "nv.weight_scale": torch.ones((2, 2), dtype=torch.float8_e4m3fn),
                    "nv.weight_scale_2": torch.tensor(1.0),
                },
                output / "model.safetensors",
            )
            write_json(output / "hf_quant_config.json", {"quantization": quantization})
            failed = run_tool(
                "audit_checkpoint.py",
                "--source",
                str(source),
                "--output-checkpoint",
                str(output),
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("invalid NVFP4 group_size", failed.stderr)

    def test_fused_rank3_expert_audit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            write_json(source / "config.json", {"architectures": ["MoeModel"], "model_type": "moe"})
            gate_base = "model.layers.0.mlp.experts.gate_up_proj"
            down_base = "model.layers.0.mlp.experts.down_proj"
            layer_map = {
                gate_base: {"group_size": 16, "quant_algo": "W4A16_NVFP4"},
                down_base: {"group_size": 16, "quant_algo": "W4A16_NVFP4"},
            }
            quantization = {"quant_algo": "W4A16_NVFP4", "quantized_layers": layer_map}
            write_json(
                output / "config.json",
                {
                    "architectures": ["MoeModel"],
                    "model_type": "moe",
                    "quantization_config": quantization,
                },
            )
            save_file(
                {
                    f"{gate_base}.weight": torch.zeros((2, 32, 16), dtype=torch.bfloat16),
                    f"{down_base}.weight": torch.zeros((2, 16, 16), dtype=torch.bfloat16),
                },
                source / "model.safetensors",
            )
            save_file(
                {
                    f"{gate_base}.weight": torch.ones((2, 32, 8), dtype=torch.uint8),
                    f"{gate_base}.weight_scale": torch.ones((2, 32, 1), dtype=torch.float8_e4m3fn),
                    f"{gate_base}.weight_scale_2": torch.tensor(1.0),
                    f"{down_base}.weight": torch.ones((2, 16, 8), dtype=torch.uint8),
                    f"{down_base}.weight_scale": torch.ones((2, 16, 1), dtype=torch.float8_e4m3fn),
                    f"{down_base}.weight_scale_2": torch.tensor(1.0),
                },
                output / "model.safetensors",
            )
            write_json(output / "hf_quant_config.json", {"quantization": quantization})
            report = json.loads(
                run_tool("audit_checkpoint.py", "--source", str(source), "--output-checkpoint", str(output)).stdout
            )
            self.assertEqual(report["algorithm_base_counts"], {"W4A16_NVFP4": 2})
            self.assertEqual(report["verdict"], "pass")

    def test_protected_quantization_requires_explicit_flag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            write_json(source / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            layer_map = {
                "mtp": {"group_size": 16, "quant_algo": "W4A16_NVFP4"},
                "ok": {"group_size": 16, "quant_algo": "W4A16_NVFP4"},
            }
            quantization = {"quant_algo": "W4A16_NVFP4", "quantized_layers": layer_map}
            write_json(
                output / "config.json",
                {
                    "architectures": ["DenseModel"],
                    "model_type": "dense",
                    "quantization_config": quantization,
                },
            )
            save_file(
                {
                    "mtp.weight": torch.zeros((2, 16), dtype=torch.bfloat16),
                    "ok.weight": torch.zeros((2, 16), dtype=torch.bfloat16),
                },
                source / "model.safetensors",
            )
            save_file(
                {
                    "mtp.weight": torch.ones((2, 8), dtype=torch.uint8),
                    "mtp.weight_scale": torch.ones((2, 1), dtype=torch.float8_e4m3fn),
                    "mtp.weight_scale_2": torch.tensor(1.0),
                    "ok.weight": torch.ones((2, 8), dtype=torch.uint8),
                    "ok.weight_scale": torch.ones((2, 1), dtype=torch.float8_e4m3fn),
                    "ok.weight_scale_2": torch.tensor(1.0),
                },
                output / "model.safetensors",
            )
            write_json(output / "hf_quant_config.json", {"quantization": quantization})
            failed = run_tool(
                "audit_checkpoint.py",
                "--source",
                str(source),
                "--output-checkpoint",
                str(output),
                check=False,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("protected modules appear in quantized_layers", failed.stderr)
            report = json.loads(
                run_tool(
                    "audit_checkpoint.py",
                    "--source",
                    str(source),
                    "--output-checkpoint",
                    str(output),
                    "--allow-quantized-protected",
                ).stdout
            )
            self.assertEqual(report["quantized_protected_bases"], ["mtp"])
            self.assertEqual(report["verdict"], "pass")

    def test_preflight_rejects_nested_quantization_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(
                root / "config.json",
                {
                    "architectures": ["DenseModel"],
                    "model_type": "dense",
                    "text_config": {"model_type": "dense_text", "quantization_config": {"quant_algo": "NVFP4"}},
                },
            )
            save_file({"weight": torch.ones(2, dtype=torch.bfloat16)}, root / "model.safetensors")
            failed = run_tool("preflight.py", str(root), check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("already quantized", failed.stderr)

    def test_routed_preflight_requires_bf16(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(
                root / "config.json", {"architectures": ["MoeModel"], "model_type": "moe", "num_hidden_layers": 1}
            )
            save_file(
                {
                    "model.layers.0.mlp.experts.gate_up_proj.weight": torch.ones((4, 8, 4), dtype=torch.float16),
                    "model.layers.0.mlp.experts.down_proj.weight": torch.ones((4, 4, 4), dtype=torch.float16),
                },
                root / "model.safetensors",
            )
            report = json.loads(
                run_tool(
                    "preflight.py",
                    str(root),
                    "--modelopt-supported",
                    "no",
                    "--whole-model-fit",
                    "no",
                    "--routed-exporter-qualified",
                    "yes",
                    "--expected-routed-layers",
                    "0",
                ).stdout
            )
            self.assertFalse(report["routed_expert"]["compatible"])
            self.assertEqual(report["decision"], "unsupported")

    def test_incomplete_infix_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {"architectures": ["DenseModel"], "model_type": "dense"})
            save_file({"weight": torch.ones(2)}, root / "model.safetensors")
            (root / "model.tmp.safetensors").write_text("partial")
            staging = root / "staging"
            staging.mkdir()
            (staging / "shard.partial").write_text("partial")
            failed = run_tool("inventory.py", str(root), check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("incomplete files", failed.stderr)

    def test_compare_inventories_reports_changed_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / "left.json"
            right = root / "right.json"
            files = [{"name": "a", "sha256": "1", "size": 1}]
            write_json(left, {"file_count": 1, "files": files, "total_file_bytes": 1})
            write_json(right, {"file_count": 1, "files": [{**files[0], "sha256": "2"}], "total_file_bytes": 1})
            result = run_tool("compare_inventories.py", str(left), str(right), check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("changed=['a']", result.stderr)

    def test_scans_use_paths_relative_to_the_checkpoint_root(self) -> None:
        # hf download without --local-dir lands under ~/.cache/huggingface/hub/...; the
        # exclusion for the nested download cache must not swallow the checkpoint itself.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".cache" / "huggingface" / "hub" / "models--org--name" / "snapshots" / "abc"
            write_checkpoint(root, {"architectures": ["DenseModel"], "model_type": "dense"}, {"w": torch.ones(2)})
            (root / ".git" / "lfs" / "tmp").mkdir(parents=True)
            (root / ".git" / "lfs" / "tmp" / "object.tmp").write_text("in-flight")
            (root / ".cache" / "huggingface" / "download").mkdir(parents=True)
            (root / ".cache" / "huggingface" / "download" / "model.safetensors.metadata").write_text("meta")
            inventory = json.loads(run_tool("inventory.py", str(root)).stdout)
            self.assertEqual([item["name"] for item in inventory["files"]], ["config.json", "model.safetensors"])
            self.assertEqual(inventory["tensor_count"], 1)

            (root / "model.safetensors.partial").write_text("partial")
            failed = run_tool("inventory.py", str(root), check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("incomplete files: ['model.safetensors.partial']", failed.stderr)

    def test_preflight_require_decision_leaves_output_rerunnable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src"
            write_checkpoint(source, {"architectures": ["DenseModel"], "model_type": "dense"}, {"w": torch.ones(2)})
            output = root / "preflight.json"
            first = run_tool("preflight.py", str(source), "--require-decision", "--output", str(output), check=False)
            self.assertEqual(first.returncode, 2)
            self.assertEqual(json.loads(first.stdout)["decision"], "needs_evidence")
            self.assertFalse(output.exists())
            run_tool(
                "preflight.py",
                str(root / "src"),
                "--modelopt-supported",
                "yes",
                "--whole-model-fit",
                "yes",
                "--require-decision",
                "--output",
                str(output),
            )
            self.assertEqual(json.loads(output.read_text())["decision"], "whole_model")

    def test_preflight_counts_nextn_layer_as_mtp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_checkpoint(
                root,
                {
                    "architectures": ["GlmMoeModel"],
                    "model_type": "glm_moe",
                    "text_config": {"num_hidden_layers": 1, "num_nextn_predict_layers": 1},
                },
                {
                    "model.layers.0.mlp.up_proj.weight": torch.ones((2, 4), dtype=torch.bfloat16),
                    "model.layers.1.eh_proj.weight": torch.ones((2, 4), dtype=torch.bfloat16),
                    "model.layers.1.mlp.up_proj.weight": torch.ones((2, 4), dtype=torch.bfloat16),
                },
            )
            report = json.loads(run_tool("preflight.py", str(root)).stdout)
            self.assertEqual(report["hidden_layers"], 1)
            self.assertEqual(report["mtp_layer_prefix"], "model.layers.1.")
            self.assertEqual(report["mtp_tensor_count"], 2)

    def test_audit_derives_contract_from_single_algorithm_exclusions(self) -> None:
        # Official single-algorithm ModelOpt exports carry quant_algo + exclude_modules and no
        # quantized_layers map; the audit must derive the quantized set from the exclusions.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            exclusions = ["lm_head", "model.embed_tokens", "model.layers.0.self_attn*"]
            source_tensors = {
                "lm_head.weight": torch.ones((4, 16), dtype=torch.bfloat16),
                "model.embed_tokens.weight": torch.ones((4, 16), dtype=torch.bfloat16),
                "model.layers.0.input_layernorm.weight": torch.ones(16, dtype=torch.bfloat16),
                "model.layers.0.mlp.experts.0.up_proj.weight": torch.ones((2, 16), dtype=torch.bfloat16),
                "model.layers.0.mlp.experts.1.up_proj.weight": torch.ones((2, 16), dtype=torch.bfloat16),
                "model.layers.0.mlp.gate.weight": torch.ones((2, 16), dtype=torch.bfloat16),
                "model.layers.0.self_attn.q_proj.weight": torch.ones((2, 16), dtype=torch.bfloat16),
            }
            write_checkpoint(source, {"architectures": ["MoeModel"], "model_type": "moe"}, source_tensors)
            quantized = ["model.layers.0.mlp.experts.0.up_proj", "model.layers.0.mlp.experts.1.up_proj"]

            def output_tensors(quantized_bases: list[str]) -> dict:
                tensors = dict(source_tensors)
                for base in quantized_bases:
                    tensors[base + ".weight"] = torch.ones((2, 8), dtype=torch.uint8)
                    tensors[base + ".weight_scale"] = torch.ones((2, 1), dtype=torch.float8_e4m3fn)
                    tensors[base + ".weight_scale_2"] = torch.tensor(1.0)
                    tensors[base + ".input_scale"] = torch.tensor(1.0)
                return tensors

            def write_output(quantized_bases: list[str], config_ignore: list[str]) -> None:
                write_checkpoint(
                    output,
                    {
                        "architectures": ["MoeModel"],
                        "model_type": "moe",
                        "quantization_config": {"ignore": config_ignore, "quant_algo": "NVFP4"},
                    },
                    output_tensors(quantized_bases),
                )
                write_json(
                    output / "hf_quant_config.json",
                    {"quantization": {"exclude_modules": exclusions, "group_size": 16, "quant_algo": "NVFP4"}},
                )

            write_output(quantized, exclusions)
            report = json.loads(
                run_tool("audit_checkpoint.py", "--source", str(source), "--output-checkpoint", str(output)).stdout
            )
            self.assertEqual(report["algorithm_base_counts"], {"NVFP4": 2})
            self.assertTrue(report["quantized_layers_derived"])
            self.assertEqual(report["precision_contract_origin"], "hf_quant_config.json")
            self.assertEqual(report["unchanged_tensor_count"], 5)

            def failed_audit() -> str:
                result = run_tool(
                    "audit_checkpoint.py", "--source", str(source), "--output-checkpoint", str(output), check=False
                )
                self.assertNotEqual(result.returncode, 0)
                return result.stderr

            write_output(quantized[:1], exclusions)
            self.assertIn("neither excluded nor quantized", failed_audit())
            write_output(quantized + ["model.layers.0.self_attn.q_proj"], exclusions)
            self.assertIn("excluded module was quantized", failed_audit())
            write_output(quantized, [])
            self.assertIn("config.json quantization_config exclusions disagree", failed_audit())


if __name__ == "__main__":
    unittest.main()
