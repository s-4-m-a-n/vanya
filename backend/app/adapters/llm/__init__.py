from functools import lru_cache

from app.adapters.llm.base import ImageInput, LLMClient, LLMError
from app.config import get_settings


@lru_cache
def get_llm() -> LLMClient:
    settings = get_settings()
    if settings.llm_provider == "openrouter":
        from app.adapters.llm.openrouter import OpenRouterClient

        if not settings.openrouter_api_key:
            raise LLMError("OPENROUTER_API_KEY must be set when LLM_PROVIDER=openrouter")
        return OpenRouterClient(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
            base_url=settings.openrouter_base_url,
            reasoning_effort=settings.llm_reasoning_effort,
            max_output_tokens=settings.llm_max_output_tokens,
            timeout=settings.llm_timeout_seconds,
        )
    if settings.llm_provider == "vertex":
        from app.adapters.llm.vertex import VertexGeminiClient

        if not settings.gcp_project_id:
            raise LLMError("GCP_PROJECT_ID must be set when LLM_PROVIDER=vertex")
        return VertexGeminiClient(
            project=settings.gcp_project_id,
            location=settings.gcp_location,
            model=settings.llm_model,
            reasoning_effort=settings.llm_reasoning_effort,
            max_output_tokens=settings.llm_max_output_tokens,
        )
    raise LLMError("No LLM configured (set LLM_PROVIDER)")


__all__ = ["ImageInput", "LLMClient", "LLMError", "get_llm"]
