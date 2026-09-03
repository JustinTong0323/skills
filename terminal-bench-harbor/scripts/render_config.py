from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any


CAPABILITY_TIMEOUT_MULTIPLIER = 1_000_000_000.0
DEFAULT_RETRY_EXCEPTIONS = (
    "RuntimeError",
    "NetworkConnectionError",
    "ApiConnectionClosedError",
    "ApiResponseStalledError",
)
DEFAULT_API_KEY_ENV = {
    "terminus-2": "OPENAI_API_KEY",
    "claude-code": "ANTHROPIC_API_KEY",
    "pi": "OPENAI_API_KEY",
}
PI_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")
# Harbor 0.20 derives these from the launcher's own ANTHROPIC_BASE_URL, not the
# agent env, so an org/model ID is truncated unless every alias is set here.
CLAUDE_MODEL_ENV = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
ENV_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def api_bases(value: str) -> tuple[str, str]:
    base = value.rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    return root, f"{root}/v1"


def prefixed_model(prefix: str, model: str) -> str:
    return model if model.startswith(f"{prefix}/") else f"{prefix}/{model}"


def unprefixed_model(prefix: str, model: str) -> str:
    return model.removeprefix(f"{prefix}/")


def retry_config(max_retries: int, exceptions: list[str]) -> dict[str, Any]:
    return {"max_retries": max_retries, "include_exceptions": exceptions}


def api_key_template(args: argparse.Namespace) -> str:
    return f"${{{args.api_key_env}:-EMPTY}}"


def terminus_agent(args: argparse.Namespace, openai_base: str) -> dict[str, Any]:
    return {
        "name": "terminus-2",
        "model_name": prefixed_model("openai", args.model),
        "env": {"OPENAI_API_KEY": api_key_template(args)},
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
    kwargs: dict[str, Any] = {
        "reasoning_effort": args.reasoning_effort,
        "max_thinking_tokens": args.max_output_tokens,
    }
    if args.claude_disallowed_tools is not None:
        kwargs["disallowed_tools"] = args.claude_disallowed_tools
    env = {
        "ANTHROPIC_BASE_URL": server_root,
        "ANTHROPIC_API_KEY": api_key_template(args),
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
    }
    env.update({name: args.model for name in CLAUDE_MODEL_ENV})
    return {
        "name": "claude-code",
        "model_name": args.model,
        "kwargs": kwargs,
        "env": env,
    }


def pi_agent(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "name": "pi",
        "model_name": prefixed_model(args.pi_provider, args.model),
        "kwargs": {"thinking": args.pi_thinking},
        "env": {"PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1"},
    }


def pi_models(args: argparse.Namespace, openai_base: str) -> dict[str, Any]:
    model = unprefixed_model(args.pi_provider, args.model)
    return {
        "providers": {
            args.pi_provider: {
                "baseUrl": openai_base,
                "api": "openai-completions",
                # Pi reads this file inside the container, so the value is literal.
                "apiKey": os.environ.get(args.api_key_env, "EMPTY"),
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
                        "id": model,
                        "name": f"{model} via {args.pi_provider}",
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


def pi_registry_semantic_digest(models: dict[str, Any]) -> str:
    semantic = json.loads(json.dumps(models))
    for provider in semantic["providers"].values():
        provider["apiKey"] = "<credential>"
    payload = json.dumps(
        semantic, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def content_addressed_path(path: str | Path, digest: str) -> Path:
    requested = Path(path)
    suffix = requested.suffix or ".json"
    stem = requested.stem if requested.suffix else requested.name
    return requested.with_name(f"{stem}.sha256-{digest}{suffix}")


def is_content_addressed_registry_path(
    candidate: str | Path, base_path: str | Path
) -> bool:
    requested = Path(base_path)
    suffix = requested.suffix or ".json"
    stem = requested.stem if requested.suffix else requested.name
    candidate_path = Path(candidate)
    return (
        candidate_path.resolve().parent == requested.resolve().parent
        and re.fullmatch(
            rf"{re.escape(stem)}\.sha256-[0-9a-f]{{64}}{re.escape(suffix)}",
            candidate_path.name,
        )
        is not None
    )


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
    if args.agent_timeout_policy == "capability":
        job["agent_timeout_multiplier"] = (
            args.agent_timeout_multiplier or CAPABILITY_TIMEOUT_MULTIPLIER
        )
    if args.verifier_timeout_multiplier is not None:
        job["verifier_timeout_multiplier"] = args.verifier_timeout_multiplier
    if args.agent == "pi":
        if args.pi_models_path is None:
            raise ValueError("--pi-models-path is required for the Pi harness")
        if Path(args.output).resolve() == Path(args.pi_models_path).resolve():
            raise ValueError(
                "--output and --pi-models-path must refer to different files"
            )
        if is_content_addressed_registry_path(args.output, args.pi_models_path):
            raise ValueError(
                "--output cannot target a content-addressed Pi registry in the "
                "--pi-models-path namespace"
            )
        models = pi_models(args, openai_base)
        digest = pi_registry_semantic_digest(models)
        registry_path = content_addressed_path(args.pi_models_path, digest)
        if Path(args.output).resolve() == registry_path.resolve():
            raise ValueError(
                "--output and the content-addressed Pi registry must refer to different files"
            )
        job["agents"][0]["env"]["TB_PI_MODELS_SEMANTIC_SHA256"] = f"sha256:{digest}"
        job["environment"] = {
            "type": "docker",
            "mounts": [
                {
                    "type": "bind",
                    "source": str(registry_path),
                    "target": "/root/.pi/agent/models.json",
                    "read_only": True,
                }
            ],
        }
    return job, models


def write_json(
    path: str | Path, data: dict[str, Any], *, overwrite: bool = True
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, allow_nan=False) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != payload:
                    raise ValueError(
                        f"{destination} already exists with different content"
                    ) from None
    finally:
        temporary.unlink(missing_ok=True)
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
    parser.add_argument(
        "--agent-timeout-policy",
        choices=("capability", "task-defined"),
        default="capability",
    )
    parser.add_argument("--agent-timeout-multiplier", type=finite_positive_float)
    parser.add_argument(
        "--agent-setup-timeout-multiplier", type=finite_positive_float, default=3.0
    )
    parser.add_argument("--verifier-timeout-multiplier", type=finite_positive_float)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-exception", action="append")
    parser.add_argument("--api-key-env")
    parser.add_argument("--claude-disallowed-tools")
    parser.add_argument("--pi-provider", default="sglang")
    parser.add_argument("--pi-thinking", choices=PI_THINKING_LEVELS, default="xhigh")
    parser.add_argument("--pi-models-path")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.retry_exception is None:
        args.retry_exception = list(DEFAULT_RETRY_EXCEPTIONS)
    if args.api_key_env is None:
        args.api_key_env = DEFAULT_API_KEY_ENV[args.agent]
    if not ENV_NAME_PATTERN.fullmatch(args.api_key_env):
        parser.error("--api-key-env must be an environment variable name")
    if args.agent == "terminus-2" and args.api_key_env != "OPENAI_API_KEY":
        parser.error(
            "terminus-2 reads OPENAI_API_KEY in the Harbor launcher process; "
            "--api-key-env cannot rename it"
        )
    if args.claude_disallowed_tools is not None:
        if args.agent != "claude-code":
            parser.error("--claude-disallowed-tools requires --agent claude-code")
        try:
            tools = shlex.split(args.claude_disallowed_tools)
        except ValueError as error:
            parser.error(f"--claude-disallowed-tools is not shell-parseable: {error}")
        if not tools or "Read" in tools:
            parser.error(
                "--claude-disallowed-tools must scope Read, such as 'Read(**/*.pdf)'; "
                "a bare Read removes all file reading"
            )
    if args.max_retries < 0:
        parser.error("--max-retries must be non-negative")
    if (
        args.agent_timeout_policy == "task-defined"
        and args.agent_timeout_multiplier is not None
    ):
        parser.error(
            "--agent-timeout-multiplier requires --agent-timeout-policy capability"
        )
    if args.task and "/" in args.dataset:
        org = args.dataset.split("/", 1)[0]
        if any(not task.startswith(f"{org}/") for task in args.task):
            parser.error(f"--task values must use full {org}/<task> names")
    return args


def main() -> None:
    args = parse_args()
    try:
        job, models = build_job(args)
        if models is not None:
            registry_path = job["environment"]["mounts"][0]["source"]
            write_json(registry_path, models, overwrite=False)
            print(f"Pi registry: {registry_path}")
        write_json(args.output, job)
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
