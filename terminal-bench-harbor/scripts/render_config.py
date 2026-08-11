from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any


def api_bases(value: str) -> tuple[str, str]:
    base = value.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    return root, f"{root}/v1"


def prefixed_model(prefix: str, model: str) -> str:
    return model if model.startswith(f"{prefix}/") else f"{prefix}/{model}"


def retry_config(max_retries: int, exceptions: list[str]) -> dict[str, Any]:
    return {"max_retries": max_retries, "include_exceptions": exceptions}


def terminus_agent(args: argparse.Namespace, openai_base: str) -> dict[str, Any]:
    return {
        "name": "terminus-2",
        "model_name": prefixed_model("openai", args.model),
        "env": {"OPENAI_API_KEY": args.api_key},
        "kwargs": {
            "api_base": openai_base,
            "reasoning_effort": args.reasoning_effort,
            "temperature": args.temperature,
            "interleaved_thinking": args.interleaved_thinking,
            "model_info": {
                "max_input_tokens": args.context_window,
                "max_output_tokens": args.max_output_tokens,
                "input_cost_per_token": 0.0,
                "output_cost_per_token": 0.0,
            },
            "llm_call_kwargs": {
                "max_tokens": args.max_output_tokens,
                "top_p": args.top_p,
            },
        },
    }


def claude_agent(args: argparse.Namespace, server_root: str) -> dict[str, Any]:
    return {
        "name": "claude-code",
        "model_name": args.model,
        "kwargs": {
            "reasoning_effort": args.reasoning_effort,
            "max_thinking_tokens": args.max_output_tokens,
        },
        "env": {
            "ANTHROPIC_BASE_URL": server_root,
            "ANTHROPIC_API_KEY": args.api_key,
            "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
        },
    }


def pi_agent(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "name": "pi",
        "model_name": prefixed_model(args.pi_provider, args.model),
        "kwargs": {"thinking": args.pi_thinking},
        "env": {"PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1"},
    }


def pi_models(args: argparse.Namespace, openai_base: str) -> dict[str, Any]:
    return {
        "providers": {
            args.pi_provider: {
                "baseUrl": openai_base,
                "api": "openai-completions",
                "apiKey": args.api_key,
                "authHeader": True,
                "compat": {
                    "supportsDeveloperRole": False,
                    "supportsReasoningEffort": True,
                    "supportsUsageInStreaming": True,
                    "supportsFinishReason": True,
                    "maxTokensField": "max_tokens",
                    "requiresReasoningContentOnAssistantMessages": True,
                    "thinkingFormat": "reasoning_effort",
                    "supportsStrictMode": False,
                },
                "models": [
                    {
                        "id": args.model,
                        "name": f"{args.model} via {args.pi_provider}",
                        "reasoning": True,
                        "thinkingLevelMap": {
                            "off": None,
                            "minimal": None,
                            "low": None,
                            "medium": None,
                            "high": "high",
                            "xhigh": args.reasoning_effort,
                            "max": args.reasoning_effort,
                        },
                        "input": ["text"],
                        "contextWindow": args.context_window,
                        "maxTokens": args.max_output_tokens,
                        "cost": {
                            "input": 0,
                            "output": 0,
                            "cacheRead": 0,
                            "cacheWrite": 0,
                        },
                    }
                ],
            }
        }
    }


def build_job(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any] | None]:
    server_root, openai_base = api_bases(args.api_base)
    dataset: dict[str, Any] = {
        "name": args.dataset,
        "ref": args.dataset_ref,
    }
    if args.task:
        dataset["task_names"] = args.task
    agents = {
        "terminus-2": lambda: terminus_agent(args, openai_base),
        "claude-code": lambda: claude_agent(args, server_root),
        "pi": lambda: pi_agent(args),
    }
    job: dict[str, Any] = {
        "job_name": args.job_name,
        "jobs_dir": args.jobs_dir,
        "n_attempts": args.attempts,
        "agent_setup_timeout_multiplier": args.agent_setup_timeout_multiplier,
        "n_concurrent_trials": args.concurrency,
        "retry": retry_config(args.max_retries, args.retry_exception),
        "agents": [agents[args.agent]()],
        "datasets": [dataset],
    }
    models = None
    if args.agent_timeout_multiplier is not None:
        job["agent_timeout_multiplier"] = args.agent_timeout_multiplier
    if args.agent == "pi":
        if args.pi_models_path is None:
            raise ValueError("--pi-models-path is required for the Pi harness")
        if Path(args.output).resolve() == Path(args.pi_models_path).resolve():
            raise ValueError(
                "--output and --pi-models-path must refer to different files"
            )
        job["environment"] = {
            "type": "docker",
            "mounts": [
                {
                    "type": "bind",
                    "source": args.pi_models_path,
                    "target": "/root/.pi/agent/models.json",
                    "read_only": True,
                }
            ],
        }
        models = pi_models(args, openai_base)
    return job, models


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(data, output, indent=2, allow_nan=False)
        output.write("\n")
    os.chmod(destination, 0o600)


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def finite_positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def finite_non_negative_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be finite and non-negative")
    return number


def probability_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 1:
        raise argparse.ArgumentTypeError("must be finite and in (0, 1]")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--agent", choices=("terminus-2", "claude-code", "pi"), required=True
    )
    parser.add_argument("--job-name", required=True)
    parser.add_argument("--jobs-dir", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--dataset", default="terminal-bench/terminal-bench-2-1")
    parser.add_argument("--dataset-ref", required=True)
    parser.add_argument("--task", action="append")
    parser.add_argument("--context-window", type=positive_int, required=True)
    parser.add_argument("--max-output-tokens", type=positive_int, required=True)
    parser.add_argument("--reasoning-effort", default="max")
    parser.add_argument("--temperature", type=finite_non_negative_float, default=1.0)
    parser.add_argument("--top-p", type=probability_float, default=0.95)
    parser.add_argument(
        "--interleaved-thinking", action=argparse.BooleanOptionalAction, default=True
    )
    parser.add_argument("--concurrency", type=positive_int, default=16)
    parser.add_argument("--attempts", type=positive_int, default=1)
    parser.add_argument("--agent-timeout-multiplier", type=finite_positive_float)
    parser.add_argument(
        "--agent-setup-timeout-multiplier", type=finite_positive_float, default=3.0
    )
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument(
        "--retry-exception",
        action="append",
        default=["RuntimeError", "NetworkConnectionError"],
    )
    parser.add_argument("--api-key", default="EMPTY")
    parser.add_argument("--pi-provider", default="sglang")
    parser.add_argument("--pi-thinking", default="xhigh")
    parser.add_argument("--pi-models-path")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.max_retries < 0:
        parser.error("--max-retries must be non-negative")
    if args.task and any(not task.startswith("terminal-bench/") for task in args.task):
        parser.error("--task values must use full terminal-bench/<task> names")
    return args


def main() -> None:
    args = parse_args()
    try:
        job, models = build_job(args)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    write_json(args.output, job)
    if models is not None:
        write_json(args.pi_models_path, models)


if __name__ == "__main__":
    main()
