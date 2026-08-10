import argparse
import json
import tomllib
from pathlib import Path


def add_issue(issues, task, message):
    issues.setdefault(task, []).append(message)


def audit(tasks_dir):
    root = Path(tasks_dir)
    task_dirs = sorted(path.parent for path in root.glob("*/task.toml"))
    issues = {}
    task_ids = {}
    external_ids = {}

    for task_dir in task_dirs:
        name = task_dir.name
        try:
            config = tomllib.loads((task_dir / "task.toml").read_text())
        except (OSError, tomllib.TOMLDecodeError) as error:
            add_issue(issues, name, f"cannot parse task.toml: {error}")
            continue

        for relative in ("instruction.md", "tests/test.sh"):
            if not (task_dir / relative).is_file():
                add_issue(issues, name, f"missing {relative}")

        metadata = config.get("metadata", {})
        task_id = metadata.get("task_id")
        external_id = metadata.get("ext_id")
        if not task_id:
            add_issue(issues, name, "missing metadata.task_id")
        elif task_id in task_ids:
            add_issue(
                issues, name, f"duplicate metadata.task_id with {task_ids[task_id]}"
            )
        else:
            task_ids[task_id] = name
        if not external_id:
            add_issue(issues, name, "missing metadata.ext_id")
        elif external_id in external_ids:
            add_issue(
                issues,
                name,
                f"duplicate metadata.ext_id with {external_ids[external_id]}",
            )
        else:
            external_ids[external_id] = name

        agent = config.get("agent", {})
        verifier = config.get("verifier", {})
        environment = config.get("environment", {})
        if agent.get("network_mode") != "no-network":
            add_issue(issues, name, "agent.network_mode must be no-network")
        if verifier.get("network_mode") != "no-network":
            add_issue(issues, name, "verifier.network_mode must be no-network")
        if verifier.get("environment_mode") != "separate":
            add_issue(issues, name, "verifier.environment_mode must be separate")
        if not environment.get("docker_image"):
            add_issue(issues, name, "missing environment.docker_image")

        collect = verifier.get("collect", [])
        commands = [
            entry.get("command", "") for entry in collect if isinstance(entry, dict)
        ]
        if not any("/logs/artifacts/model.patch" in command for command in commands):
            add_issue(issues, name, "verifier collect hook does not create model.patch")

        artifacts = config.get("artifacts", [])
        if "/logs/artifacts/model.patch" not in artifacts:
            add_issue(issues, name, "artifacts does not include model.patch")

    return {
        "tasks_dir": str(root.resolve()),
        "task_count": len(task_dirs),
        "valid_tasks": len(task_dirs) - len(issues),
        "invalid_tasks": len(issues),
        "issues": issues,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--expected", type=int)
    args = parser.parse_args()

    result = audit(args.tasks)
    if args.expected is not None and result["task_count"] != args.expected:
        result["count_error"] = (
            f"expected {args.expected} tasks, found {result['task_count']}"
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["invalid_tasks"] or "count_error" in result:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
