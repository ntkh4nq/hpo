"""Chat models on the DBIZ gateway. Per-model request parameters live in llm_models.toml.

Structured output uses function_calling with thinking off (decisions T10, T11b). With thinking on, DeepSeek
rejects structured output (T22), so invoke_json appends the schema to the system prompt and validates the
JSON text with pydantic (T32).
"""

import json
import os
import tomllib
from pathlib import Path

from langchain_openai import ChatOpenAI
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel

MODEL_PARAMS = tomllib.loads((Path(__file__).with_name("llm_models.toml")).read_text())
ATTEMPTS = 2  # one retry on any failure: connection, timeout, schema (plan.md phase 3, step 2)
JSON_INSTRUCTION = "\n\n# Định dạng trả lời\nChỉ trả một JSON đúng schema sau, không kèm chữ nào khác:\n"


def chat(model: str | None = None, timeout: float = 120, thinking: bool = False) -> ChatOpenAI:
    model = model or os.environ["LLM_MODEL"]
    return ChatOpenAI(
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
                timeout: float, model: str | None = None) -> BaseModel | None:
    """One request (plus one retry) returning `schema`, or None when both attempts failed."""
    if thinking:
        llm = chat(model, thinking=True, timeout=timeout)
        system += JSON_INSTRUCTION + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    else:
        llm = chat(model, timeout=timeout).with_structured_output(schema, method="function_calling")
    messages = [("system", system), ("human", human)]
    for _ in range(ATTEMPTS):
        try:
            out = llm.invoke(messages, config={"callbacks": [CallbackHandler()]})
            if thinking:
                out = parse_json(out.content, schema)
        except Exception:  # noqa: BLE001 - timeout, gateway error, schema validation: all count as a failed attempt
            continue
        if out is not None:
            return out
    return None
