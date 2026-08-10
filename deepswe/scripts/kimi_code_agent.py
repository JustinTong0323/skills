import re
import shlex
from urllib.parse import urlparse

from pier.agents.installed.base import BaseInstalledAgent, with_prompt_template
from pier.environments.base import BaseEnvironment
from pier.models.agent.context import AgentContext
from pier.models.agent.install import AgentInstallSpec, InstallStep
from pier.models.agent.network import NetworkAllowlist


PACKAGE = "@moonshot-ai/kimi-code"
MINIMUM_VERSION = (0, 23, 6)
OUTPUT_PATH = "/logs/agent/kimi-code.txt"


class KimiCode(BaseInstalledAgent):
    SUPPORTS_ATIF = False

    def __init__(
        self,
        *args,
        max_steps_per_turn=None,
        max_retries_per_step=None,
        **kwargs,
    ):
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
    def _path_prefix():
        return (
            'export NVM_DIR="$HOME/.nvm"; '
            '[ ! -s "$NVM_DIR/nvm.sh" ] || . "$NVM_DIR/nvm.sh"; '
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
                        '. "$NVM_DIR/nvm.sh"; '
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
        return NetworkAllowlist(domains=sorted(hosts))

    def populate_context_post_run(self, context):
        return None

    def _loop_control_command(self):
        values = []
        if self.max_steps_per_turn is not None:
            values.append(f"max_steps_per_turn = {self.max_steps_per_turn}")
        if self.max_retries_per_step is not None:
            values.append(f"max_retries_per_step = {self.max_retries_per_step}")
        if not values:
            return ""
        config = "[loop_control]\\n" + "\\n".join(values) + "\\n"
        return (
            'mkdir -p "$HOME/.kimi-code"; '
            f"printf '%b' {shlex.quote(config)} > \"$HOME/.kimi-code/config.toml\"; "
            'chmod 600 "$HOME/.kimi-code/config.toml"; '
        )

    @with_prompt_template
    async def run(
        self, instruction, environment: BaseEnvironment, context: AgentContext
    ):
        for key in (
            "KIMI_MODEL_NAME",
            "KIMI_MODEL_API_KEY",
            "KIMI_MODEL_BASE_URL",
        ):
            if not self._has_env(key):
                raise ValueError(f"{key} is required; pass it with --agent-env")

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

        skills_arg = ""
        if self.skills_dir:
            skills_arg = f"--skills-dir {shlex.quote(self.skills_dir)} "
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"{self._path_prefix()}"
                f"{self._loop_control_command()}"
                f"kimi --prompt {shlex.quote(instruction)} "
                f"{skills_arg}--output-format stream-json "
                f"</dev/null 2>&1 | stdbuf -oL tee {OUTPUT_PATH}"
            ),
            env=env,
        )
