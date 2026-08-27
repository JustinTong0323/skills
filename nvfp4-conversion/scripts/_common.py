import hashlib
import json
from pathlib import Path
from typing import Any


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(), object_pairs_hook=reject_duplicate_keys)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def write_json(path: Path | None, value: Any) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if path is None:
        print(payload, end="")
        return
    if path.exists() and path.read_text() != payload:
        raise ValueError(f"refusing to overwrite existing file with different content: {path}")
    path.write_text(payload)


def sha256_file(path: Path, chunk_size: int = 16 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_safetensors():
    try:
        from safetensors import safe_open
    except ImportError as error:
        raise RuntimeError("safetensors is required for checkpoint inspection") from error
    return safe_open


def checkpoint_layout(root: Path) -> dict[str, Any]:
    safe_open = require_safetensors()
    if not root.is_dir():
        raise ValueError(f"checkpoint directory does not exist: {root}")
    incomplete_markers = (".tmp", ".partial", ".incomplete")
    incomplete_files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and ".cache/huggingface/" not in path.as_posix()
        and any(path.name.endswith(marker) or f"{marker}." in path.name for marker in incomplete_markers)
    )
    if incomplete_files:
        raise ValueError(f"checkpoint contains incomplete files: {incomplete_files}")
    index_path = root / "model.safetensors.index.json"
    physical_files = sorted(path for path in root.glob("*.safetensors") if path.is_file())
    if not physical_files:
        raise ValueError("checkpoint contains no safetensors files")

    physical_keys: dict[str, str] = {}
    tensor_metadata: dict[str, dict[str, Any]] = {}
    for path in physical_files:
        with safe_open(path, framework="pt", device="cpu") as file:
            for key in file.keys():
                if key in physical_keys:
                    raise ValueError(f"tensor appears in multiple files: {key}")
                tensor_slice = file.get_slice(key)
                physical_keys[key] = path.name
                tensor_metadata[key] = {
                    "dtype": tensor_slice.get_dtype(),
                    "shape": list(tensor_slice.get_shape()),
                }

    if index_path.exists():
        index = load_json(index_path)
        weight_map = index.get("weight_map")
        if not isinstance(weight_map, dict):
            raise ValueError("safetensors index has no object weight_map")
        logical_keys = set(weight_map)
        if logical_keys != set(physical_keys):
            raise ValueError(
                f"index/physical key mismatch: missing={sorted(set(physical_keys) - logical_keys)[:10]} "
                f"extra={sorted(logical_keys - set(physical_keys))[:10]}"
            )
        for key, filename in weight_map.items():
            if physical_keys[key] != filename:
                raise ValueError(f"index points {key} to {filename}, physically in {physical_keys[key]}")
        indexed_files = set(weight_map.values())
        physical_names = {path.name for path in physical_files}
        if indexed_files != physical_names:
            raise ValueError(
                f"index/file mismatch: missing={sorted(physical_names - indexed_files)} "
                f"extra={sorted(indexed_files - physical_names)}"
            )
        indexed_payload_bytes = index.get("metadata", {}).get("total_size")
    else:
        if len(physical_files) != 1:
            raise ValueError("multiple safetensors files require model.safetensors.index.json")
        weight_map = dict(physical_keys)
        indexed_payload_bytes = None

    return {
        "index_path": index_path if index_path.exists() else None,
        "indexed_payload_bytes": indexed_payload_bytes,
        "physical_files": physical_files,
        "tensor_metadata": tensor_metadata,
        "weight_map": weight_map,
    }


def tensor_chunks(root: Path, filename: str, key: str, rows_per_chunk: int = 1024):
    safe_open = require_safetensors()
    with safe_open(root / filename, framework="pt", device="cpu") as file:
        tensor_slice = file.get_slice(key)
        shape = tensor_slice.get_shape()
        if not shape:
            yield file.get_tensor(key)
            return
        for start in range(0, shape[0], rows_per_chunk):
            yield tensor_slice[start : min(start + rows_per_chunk, shape[0])]


def compare_tensor_content(
    source_root: Path,
    source_file: str,
    output_root: Path,
    output_file: str,
    key: str,
    rows_per_chunk: int,
) -> None:
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("torch is required for logical tensor comparison") from error
    source_chunks = tensor_chunks(source_root, source_file, key, rows_per_chunk)
    output_chunks = tensor_chunks(output_root, output_file, key, rows_per_chunk)
    for source_chunk, output_chunk in zip(source_chunks, output_chunks, strict=True):
        if not torch.equal(source_chunk, output_chunk):
            raise ValueError(f"unchanged tensor content mismatch: {key}")


def scan_positive_finite(
    root: Path,
    filename: str,
    key: str,
    rows_per_chunk: int,
) -> tuple[int, float, float]:
    try:
        import torch
    except ImportError as error:
        raise RuntimeError("torch is required for scale validation") from error
    count = 0
    minimum = float("inf")
    maximum = float("-inf")
    for chunk in tensor_chunks(root, filename, key, rows_per_chunk):
        values = chunk.float()
        if not bool(torch.isfinite(values).all()):
            raise ValueError(f"non-finite scale values: {key}")
        if not bool((values > 0).all()):
            raise ValueError(f"non-positive scale values: {key}")
        count += values.numel()
        minimum = min(minimum, float(values.min()))
        maximum = max(maximum, float(values.max()))
    return count, minimum, maximum


def directory_inventory(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise ValueError(f"inventory root does not exist: {root}")
    files = []
    for path in sorted(
        item
        for item in root.rglob("*")
        if item.is_file()
        and ".cache/huggingface/" not in item.as_posix()
        and "/.git/" not in item.as_posix()
    ):
        files.append(
            {
                "name": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "file_count": len(files),
        "files": files,
        "total_file_bytes": sum(item["size"] for item in files),
    }
