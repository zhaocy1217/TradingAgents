"""Shared helpers for invoking an agent with structured output and a graceful fallback.

The Portfolio Manager, Trader, and Research Manager all follow the same
canonical pattern:

1. At agent creation, wrap the LLM with ``with_structured_output(Schema)``
   so the model returns a typed Pydantic instance. If the provider does
   not support structured output (rare; mostly older Ollama models), the
   wrap is skipped and the agent uses free-text generation instead.
2. At invocation, run the structured call and render the result back to
   markdown. If the structured call itself fails for any reason
   (malformed JSON from a weak model, transient provider issue), fall
   back to a plain ``llm.invoke`` so the pipeline never blocks.

Centralising the pattern here keeps the agent factories small and ensures
all three agents log the same warnings when fallback fires.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _coerce_structured_invoke_result(
    result: Any,
    schema: type[BaseModel],
    *,
    agent_name: str,
) -> BaseModel:
    """Turn provider-specific invoke payloads into a concrete ``schema`` instance.

    LangChain / provider stacks differ: some return a Pydantic model, some a
    plain dict, some a wrapper dict with ``parsed`` / ``parsing_error``, and
    weak models occasionally yield ``None``. Normalising here keeps render
    functions strict and avoids ``'NoneType' object has no attribute 'rating'``.
    """
    if isinstance(result, dict) and "parsed" in result:
        err = result.get("parsing_error")
        if err is not None:
            raise ValueError(f"structured parsing_error: {err}")
        result = result.get("parsed")

    if result is None:
        raise ValueError("structured LLM returned None")

    if isinstance(result, schema):
        return result

    if isinstance(result, dict):
        try:
            return schema.model_validate(result)
        except Exception as exc:
            raise ValueError(f"structured dict did not validate as {schema.__name__}: {exc}") from exc

    if isinstance(result, BaseModel):
        try:
            return schema.model_validate(result.model_dump(mode="json"))
        except Exception as exc:
            raise ValueError(
                f"structured {type(result).__name__} could not coerce to {schema.__name__}: {exc}"
            ) from exc

    raise ValueError(
        f"{agent_name}: unexpected structured-output type {type(result)!r}; "
        f"expected {schema.__name__}, dict, or None wrapper"
    )


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Optional[Any]:
    """Return ``llm.with_structured_output(schema)`` or ``None`` if unsupported.

    Logs a warning when the binding fails so the user understands the agent
    will use free-text generation for every call instead of one-shot fallback.
    """
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider does not support with_structured_output (%s); "
            "falling back to free-text generation",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
    *,
    schema: Optional[type[BaseModel]] = None,
) -> str:
    """Run the structured call and render to markdown; fall back to free-text on any failure.

    ``prompt`` is whatever the underlying LLM accepts (a string for chat
    invocations, a list of message dicts for chat models that take that
    shape). The same value is forwarded to the free-text path so the
    fallback sees the same input the structured call did.

    Pass ``schema`` (the same Pydantic model passed to ``with_structured_output``)
    so dict / wrapper / mismatched-model payloads from the provider are coerced
    before ``render`` runs.
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            if schema is not None:
                model = _coerce_structured_invoke_result(result, schema, agent_name=agent_name)
                return render(model)  # type: ignore[arg-type]
            if result is None:
                raise ValueError("structured LLM returned None")
            return render(result)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning(
                "%s: structured-output invocation failed (%s); retrying once as free text",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return response.content
