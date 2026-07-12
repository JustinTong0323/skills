#!/usr/bin/env python3
import argparse
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--command-timeout", type=int, default=180)
    parser.add_argument("--container-timeout", default="12h")
    parser.add_argument("--completion-timeout", type=int, default=1200)
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.source).read_text())
    environment = config.setdefault("environment", {})
    environment.update(
        {
            "environment_class": "docker",
            "cwd": "/app",
            "timeout": args.command_timeout,
            "container_timeout": args.container_timeout,
            "pull_timeout": 1800,
            "run_args": ["--rm", "--entrypoint", ""],
        }
    )
    model = config.setdefault("model", {})
    model["cost_tracking"] = "ignore_errors"
    model_kwargs = model.setdefault("model_kwargs", {})
    model_kwargs.update(
        {
            "timeout": args.completion_timeout,
            "drop_params": True,
        }
    )
    config.setdefault("run", {})["env_startup_command"] = (
        "git -C /app ls-files --others --exclude-standard --directory "
        "| sed 's#^#/#' >> /app/.git/info/exclude"
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(config, sort_keys=False))
    print(output)


if __name__ == "__main__":
    main()
