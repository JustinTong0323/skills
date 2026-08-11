import re
import shlex
import tomllib
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse

from pier.agents.installed.base import BaseInstalledAgent, with_prompt_template
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.network import NetworkAllowlist

from kimi_config import merge_loop_control


PACKAGE = "@moonshot-ai/kimi-code"
MINIMUM_VERSION = (0, 23, 6)
OUTPUT_PATH = "/logs/agent/kimi-code.txt"


class KimiCode(BaseInstalledAgent):
    SUPPORTS_ATIF = False

    def __init__(
        self,
        *args,
        config_file=None,
        filesystem_polling=False,
        watch_poll_interval_ms=1000,
        max_steps_per_turn=None,
        max_retries_per_step=None,
        **kwargs,
    ):
        self.config_file = self._config_file(config_file)
        self.filesystem_polling = self._boolean(
            filesystem_polling, "filesystem_polling"
        )
        self.watch_poll_interval_ms = self._positive_int(
            watch_poll_interval_ms, "watch_poll_interval_ms"
        )
        self.max_steps_per_turn = self._positive_int(
            max_steps_per_turn, "max_steps_per_turn"
        )
        self.max_retries_per_step = self._nonnegative_int(
            max_retries_per_step, "max_retries_per_step"
        )
        super().__init__(*args, **kwargs)

    @staticmethod
    def name():
        return "kimi-code"

    @staticmethod
    def _positive_int(value, name):
        if value is None:
            return None
        value = int(value)
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        return value

    @staticmethod
    def _nonnegative_int(value, name):
        if value is None:
            return None
        value = int(value)
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
        return value

    @staticmethod
    def _config_file(value):
        if value is None:
            return None
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Kimi Code config file does not exist: {path}")
        return path

    @staticmethod
    def _boolean(value, name):
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{name} must be a boolean")

    @staticmethod
    def _path_prefix():
        return (
            'export NVM_DIR="$HOME/.nvm"; '
            '[ ! -s "$NVM_DIR/nvm.sh" ] || . "$NVM_DIR/nvm.sh" || command -v nvm >/dev/null; '
            'export PATH="$HOME/.local/bin:$PATH"; '
        )

    def _validated_version(self):
        if not self._version:
            raise ValueError("Kimi Code requires an exact version")
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", self._version)
        if not match:
            raise ValueError("Kimi Code version must use MAJOR.MINOR.PATCH")
        if tuple(map(int, match.groups())) < MINIMUM_VERSION:
            raise ValueError("Kimi Code must be version 0.23.6 or newer")
        return self._version

    def get_version_command(self):
        return self._path_prefix() + "kimi --version"

    def parse_version(self, stdout):
        match = re.search(r"(\d+\.\d+\.\d+)", stdout)
        return match.group(1) if match else stdout.strip()

    def install_spec(self):
        version = self._validated_version()
        return AgentInstallSpec(
            agent_name=self.name(),
            version=version,
            steps=[
                InstallStep(
                    user="root",
                    env={"DEBIAN_FRONTEND": "noninteractive"},
                    run="apt-get update && apt-get install -y ca-certificates curl",
                ),
                InstallStep(
                    user="agent",
                    run=(
                        "set -euo pipefail; "
                        "curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash; "
                        'export NVM_DIR="$HOME/.nvm"; '
                        '. "$NVM_DIR/nvm.sh" || command -v nvm >/dev/null; '
                        "nvm install 22; "
                        'mkdir -p "$HOME/.local"; '
                        f'npm install --global --prefix "$HOME/.local" {PACKAGE}@{version}; '
                        f"{self._path_prefix()}kimi --version"
                    ),
                ),
            ],
            verification_command=self.get_version_command(),
        )

    def network_allowlist(self):
        hosts = {
            "github.com",
            "nodejs.org",
            "raw.githubusercontent.com",
            "registry.npmjs.org",
        }
        base_url = self._get_env("KIMI_MODEL_BASE_URL")
        if base_url:
            host = urlparse(base_url).hostname
            if host:
                hosts.add(host.lower().rstrip("."))
        if self.config_file:
            config = tomllib.loads(self.config_file.read_text())
            for provider in config.get("providers", {}).values():
                if not isinstance(provider, dict):
                    continue
                host = urlparse(provider.get("base_url", "")).hostname
                if host:
                    hosts.add(host.lower().rstrip("."))
        return NetworkAllowlist(domains=sorted(hosts))

    def populate_context_post_run(self, context):
        return None

    @contextmanager
    def _effective_config_file(self):
        if self.config_file:
            config_text = self.config_file.read_text()
        else:
            config_text = ""
        merged = merge_loop_control(
            config_text,
            self.max_steps_per_turn,
            self.max_retries_per_step,
        )
        if merged == config_text:
            yield self.config_file
            return
        with NamedTemporaryFile(mode="w", encoding="utf-8") as temporary:
            temporary.write(merged)
            temporary.flush()
            yield Path(temporary.name)

    @with_prompt_template
    async def run(
        self, instruction, environment: BaseEnvironment, context: AgentContext
    ):
        if not self.config_file:
            for key in (
                "KIMI_MODEL_NAME",
                "KIMI_MODEL_API_KEY",
                "KIMI_MODEL_BASE_URL",
            ):
                if not self._has_env(key):
                    raise ValueError(f"{key} is required; pass it with --agent-env")

        with self._effective_config_file() as config_file:
            if config_file:
                remote_config = "/tmp/harbor-kimi-code-config.toml"
                await environment.upload_file(config_file, remote_config)
                identity = await self.exec_as_agent(
                    environment,
                    command='printf "%s\n%s\n%s\n" "$(id -u)" "$(id -g)" "$HOME"',
                )
                uid, gid, agent_home = identity.stdout.splitlines()
                if (
                    not uid.isdigit()
                    or not gid.isdigit()
                    or not agent_home.startswith("/")
                ):
                    raise ValueError("cannot resolve Kimi Code runtime identity")
                config_dir = shlex.quote(f"{agent_home}/.kimi-code")
                config_path = shlex.quote(f"{agent_home}/.kimi-code/config.toml")
                await self.exec_as_root(
                    environment,
                    command=(
                        f"install -d -m 700 -o {uid} -g {gid} {config_dir}; "
                        f"install -m 600 -o {uid} -g {gid} {remote_config} "
                        f"{config_path}; "
                        f"rm -f {remote_config}"
                    ),
                )

        env = self.build_process_env()
        for key in (
            "KIMI_MODEL_NAME",
            "KIMI_MODEL_API_KEY",
            "KIMI_MODEL_BASE_URL",
            "KIMI_MODEL_PROVIDER_TYPE",
            "KIMI_MODEL_MAX_CONTEXT_SIZE",
            "KIMI_MODEL_CAPABILITIES",
            "KIMI_MODEL_THINKING_EFFORT",
            "KIMI_MODEL_THINKING_KEEP",
            "KIMI_MODEL_TEMPERATURE",
            "KIMI_MODEL_TOP_P",
            "KIMI_MODEL_MAX_COMPLETION_TOKENS",
        ):
            value = self._get_env(key)
            if value is not None:
                env[key] = value
        env.update(
            {
                "CI": "1",
                "KIMI_CODE_NO_AUTO_UPDATE": "true",
                "KIMI_DISABLE_TELEMETRY": "true",
                "NO_COLOR": "1",
            }
        )
        if self.filesystem_polling:
            env.update(
                {
                    "CHOKIDAR_USEPOLLING": "true",
                    "CHOKIDAR_INTERVAL": str(self.watch_poll_interval_ms),
                }
            )

        skills_arg = ""
        if self.skills_dir:
            skills_arg = f"--skills-dir {shlex.quote(self.skills_dir)} "
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"{self._path_prefix()}"
                f"kimi --prompt {shlex.quote(instruction)} "
                f"{skills_arg}--output-format stream-json "
                f"</dev/null 2>&1 | stdbuf -oL tee {OUTPUT_PATH}"
            ),
            env=env,
        )
