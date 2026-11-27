"""Chat models on the DBIZ gateway. Per-model request parameters live in llm_models.toml.

Structured output uses function_calling with thinking off (decisions T10, T11b). With thinking on, DeepSeek
rejects structured output (T22), so invoke_json appends the schema to the system prompt and validates the
JSON text with pydantic (T32).
"""

import json
import os
import tomllib
from pathlib import Path

from langchain_core.globals import set_llm_cache
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel

from core.image import data_url
from core.llm_cache import SQLiteCache

if os.environ.get("LLM_CACHE"):   # test runs only: replay recorded answers (core/llm_cache.py)
    set_llm_cache(SQLiteCache(os.environ["LLM_CACHE"]))

MODEL_PARAMS = tomllib.loads((Path(__file__).with_name("llm_models.toml")).read_text())
ATTEMPTS = 2  # one retry on any failure: connection, timeout, schema (plan.md phase 3, step 2)
JSON_INSTRUCTION = "\n\n# Định dạng trả lời\nChỉ trả một JSON đúng schema sau, không kèm chữ nào khác:\n"


def chat(model: str | None = None, timeout: float = 120, thinking: bool = False, name: str | None = None) -> ChatOpenAI:
    """`name` is the step the call serves (split, choose_terms, supervisor, ...): it names the Langfuse generation."""
    model = model or os.environ["LLM_MODEL"]
    return ChatOpenAI(
        name=name,
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        model=model,
        temperature=0,
        timeout=timeout,
        max_retries=0,  # callers decide how often to retry
        extra_body=MODEL_PARAMS[model]["thinking_on" if thinking else "thinking_off"],
    )


def parse_json(text: str, schema: type[BaseModel]) -> BaseModel:
    """Validate a JSON reply, stripping a markdown code fence if the model added one."""
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    return schema.model_validate_json(text)


def invoke_json(system: str, human: str, schema: type[BaseModel], *, thinking: bool,
                timeout: float, model: str | None = None, usage: dict | None = None,
                name: str | None = None) -> BaseModel | None:
    """One request (plus one retry) returning `schema`, or None when both attempts failed.

    `usage`, when given, accumulates {"requests", "input_tokens", "output_tokens"} over the attempts made.
    """
    if thinking:
        llm = chat(model, thinking=True, timeout=timeout, name=name)
        system += JSON_INSTRUCTION + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    else:
        # include_raw: the reply's token usage; a schema error comes back as parsing_error instead of raising (T82)
        llm = chat(model, timeout=timeout, name=name).with_structured_output(schema, method="function_calling", include_raw=True)
    messages = [("system", system), ("human", human)]
    for _ in range(ATTEMPTS):
        if usage is not None:
            usage["requests"] = usage.get("requests", 0) + 1
        try:
            out = llm.invoke(messages, config={"callbacks": [CallbackHandler()]})
            message = out if thinking else out["raw"]
            if usage is not None:
                meta = getattr(message, "usage_metadata", None) or {}
                for k in ("input_tokens", "output_tokens"):
                    usage[k] = usage.get(k, 0) + meta.get(k, 0)
            if thinking:
                out = parse_json(out.content, schema)
            else:
                if out["parsing_error"] is not None:
                    continue
                out = out["parsed"]
        except Exception:  # noqa: BLE001 - timeout, gateway error, schema validation: all count as a failed attempt
            continue
        if out is not None:
            return out
    return None


def describe_image(image_path, prompt: str, *, model: str, timeout: float, name: str | None = None) -> str | None:
    """One image + instruction to a vision chat model (thinking off); None when every attempt failed."""
    llm = chat(model, timeout=timeout, name=name)
    message = HumanMessage(content=[{"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": data_url(image_path)}}])
    for _ in range(ATTEMPTS):
        try:
            out = llm.invoke([message], config={"callbacks": [CallbackHandler()]})
        except Exception:  # noqa: BLE001 - timeout, gateway error: a failed attempt
            continue
        text = out.content.strip() if isinstance(out.content, str) else ""
        if text:
            return text
    return None
