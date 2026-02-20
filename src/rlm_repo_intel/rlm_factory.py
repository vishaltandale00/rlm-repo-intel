"""Factory for creating RLM instances with proper API key handling."""

import os
from typing import Any

from rich.console import Console

console = Console()


def _infer_backend(model_name: str) -> str:
    model = model_name.lower()
    if model.startswith("claude"):
        return "anthropic"
    if model.startswith("gemini"):
        return "gemini"
    return "openai"


def _get_api_key(backend: str) -> str | None:
    """Get API key for a backend from environment."""
    key_map = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "google": "GOOGLE_API_KEY",
    }
    env_var = key_map.get(backend, "")
    return os.environ.get(env_var)


def create_rlm(model_name: str, verbose: bool = False, **extra_kwargs) -> Any:
    """Create an RLM instance with proper backend detection and API key injection.
    
    The rlms library requires api_key as an explicit kwarg for Anthropic/Gemini.
    This factory handles that automatically.
    """
    from rlm import RLM

    backend = _infer_backend(model_name)
    api_key = _get_api_key(backend)

    backend_kwargs: dict[str, Any] = {"model_name": model_name}
    if api_key:
        backend_kwargs["api_key"] = api_key

    backend_kwargs.update(extra_kwargs)

    return RLM(
        backend=backend,
        backend_kwargs=backend_kwargs,
        verbose=verbose,
    )


def try_create_rlm(model_name: str, label: str = "", verbose: bool = False) -> Any | None:
    """Try to create an RLM, return None on failure with a warning."""
    try:
        return create_rlm(model_name, verbose=verbose)
    except Exception as exc:
        console.print(f"[yellow]Warning: failed to initialize {label or model_name} RLM: {exc}[/]")
        return None
