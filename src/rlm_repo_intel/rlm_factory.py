"""Factory for creating RLM instances with proper API key handling."""

from typing import Any

from rich.console import Console

from rlm_repo_intel.pipeline.rlm_session import _patch_rlm_litellm_kwargs_passthrough

console = Console()


_patch_rlm_litellm_kwargs_passthrough()


def _to_litellm_model_name(model_name: str) -> str:
    model = model_name.strip()
    lower = model.lower()
    if lower.startswith("anthropic/") or lower.startswith("gemini/"):
        return model
    if lower.startswith("claude"):
        return f"anthropic/{model}"
    if lower.startswith("gemini"):
        return f"gemini/{model}"
    return model


def create_rlm(model_name: str, verbose: bool = False, **extra_kwargs) -> Any:
    """Create an RLM instance via LiteLLM backend."""
    from rlm import RLM

    litellm_model_name = _to_litellm_model_name(model_name)
    backend_kwargs: dict[str, Any] = {"model_name": litellm_model_name}

    canonical_model = litellm_model_name.split("/", 1)[-1].lower()
    if canonical_model == "claude-sonnet-4-6":
        backend_kwargs["extra_headers"] = {"anthropic-beta": "context-1m-2025-08-07"}

    backend_kwargs.update(extra_kwargs)

    return RLM(
        backend="litellm",
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
