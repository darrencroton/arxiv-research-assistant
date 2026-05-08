"""Provider package for the Science Paper Summariser.

Provider selection is explicit:
    create_provider("cli", "claude")
    create_provider("api", "openai")
    create_provider("api", "openai-compatible")

The program never switches mode or provider automatically. The requested mode
and provider must both be valid, and any missing prerequisite must fail
immediately.
"""

import logging
import os
import shutil

from dotenv import load_dotenv

from .api import ClaudeAPI, OpenAIAPI, GeminiAPI, PerplexityAPI, OllamaAPI, OpenAICompatibleAPI
from .cli import ClaudeCLI, CodexCLI, GeminiCLI, CopilotCLI

load_dotenv()

_CLI_PROVIDERS = {
    "claude": ClaudeCLI,
    "gemini": GeminiCLI,
    "codex": CodexCLI,
    "copilot": CopilotCLI,
}

_API_PROVIDERS = {
    "claude": ClaudeAPI,
    "gemini": GeminiAPI,
    "openai": OpenAIAPI,
    "perplexity": PerplexityAPI,
    "ollama": OllamaAPI,
    "openai-compatible": OpenAICompatibleAPI,
}

_API_KEY_ENV_VARS = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
}

SUPPORTED_MODES = ("cli", "api")


def _registry_for_mode(mode):
    """Return the provider registry for the selected mode."""
    if mode == "cli":
        return _CLI_PROVIDERS
    if mode == "api":
        return _API_PROVIDERS
    raise ValueError(
        f"Unknown mode '{mode}'. Supported modes: {', '.join(SUPPORTED_MODES)}."
    )


def get_supported_provider_names(mode):
    """Return the supported provider names for a mode."""
    return tuple(sorted(_registry_for_mode(mode).keys()))


def _validate_prerequisites(mode, provider_name, provider_class):
    """Fail early when the requested provider cannot run in the selected mode."""
    if mode == "cli":
        cli_command = provider_class.cli_command
        if not shutil.which(cli_command):
            raise ValueError(
                f"Cannot start mode '{mode}' with provider '{provider_name}': "
                f"required CLI binary '{cli_command}' was not found on PATH."
            )
        return

    api_key_env = _API_KEY_ENV_VARS.get(provider_name)
    if api_key_env and not os.getenv(api_key_env):
        raise ValueError(
            f"Cannot start mode '{mode}' with provider '{provider_name}': "
            f"required environment variable '{api_key_env}' is not set."
        )


def create_provider(mode, provider_name, config=None):
    """Create a provider instance for an explicit mode/provider selection."""
    mode = mode.lower().strip()
    provider_name = provider_name.lower().strip()

    registry = _registry_for_mode(mode)
    if provider_name not in registry:
        available = ", ".join(get_supported_provider_names(mode))
        raise ValueError(
            f"Provider '{provider_name}' is not supported in mode '{mode}'. "
            f"Available {mode} providers: {available}"
        )

    provider_class = registry[provider_name]
    _validate_prerequisites(mode, provider_name, provider_class)

    try:
        provider = provider_class(config)
    except Exception as exc:
        raise ValueError(
            f"Cannot start mode '{mode}' with provider '{provider_name}': {exc}"
        ) from exc

    provider.mode = mode
    provider.provider_name = provider_name
    logging.info(
        f"Using mode '{mode}' with provider '{provider_name}' ({provider_class.__name__})"
    )
    return provider
