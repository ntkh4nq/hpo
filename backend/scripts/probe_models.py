"""Phase 0: can the chat models + bge-m3 on the DBIZ gateway carry this project?

Gates (exit 1 if any fails):
  * structured output: week-3 example -> 4 mentions, spans verbatim, assertion/assessment as expected
  * tool calling through Deep Agents: direct tool call, and delegation via `task` to a subagent
  * bge-m3: 1024 dims; Vietnamese term closer to its English HPO label than to an unrelated one
Each LLM gate runs per model, with thinking off and on; latency is printed, not gated.

Run: make probe                       # every model in core/llm_models.toml
     python scripts/probe_models.py vlm
"""

import math
import os
import sys
import time
import tomllib
from pathlib import Path
from typing import Literal

from deepagents import create_deep_agent
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from openai import OpenAI
from pydantic import BaseModel, Field

BASE_URL, API_KEY = os.environ["LLM_BASE_URL"], os.environ["LLM_API_KEY"]
EMBEDDING_MODEL = os.environ["EMBEDDING_MODEL"]
MODEL_PARAMS = tomllib.loads((Path(__file__).parents[1] / "core" / "llm_models.toml").read_text())

REPORT = (
    "Thai 22 tuần. Nghi thiểu sản xương mũi. Ghi nhận thông liên thất. "
    "Chưa ghi nhận giãn não thất. Khó đánh giá bàn tay thai do tư thế thai."
)
# keyword in span -> (assertion, assessment), from the week-3 report's Table 3
EXPECTED = {
    "xương mũi": ("SUSPECTED", "ASSESSED"),
    "thông liên thất": ("PRESENT", "ASSESSED"),
    "giãn não thất": ("ABSENT", "ASSESSED"),
    "bàn tay": ("UNCERTAIN", "LIMITED"),
}


class Mention(BaseModel):
    span: str = Field(description="Cụm từ chép nguyên văn từ báo cáo, mô tả đúng một biểu hiện")
    assertion: Literal["PRESENT", "ABSENT", "SUSPECTED", "UNCERTAIN"]
    assessment: Literal["ASSESSED", "LIMITED", "NOT_ASSESSABLE", "NOT_ASSESSED"]
    gestational_week: int | None = Field(description="Tuổi thai (tuần) lúc quan sát, nếu báo cáo có nêu")


class Extraction(BaseModel):
    mentions: list[Mention]


EXTRACT_PROMPT = """Trích xuất mọi biểu hiện (phenotype) của thai được nhắc tới trong báo cáo.
assertion: PRESENT = ghi nhận có; ABSENT = đã khảo sát và không thấy; SUSPECTED = nghi ngờ; UNCERTAIN = chưa rõ kết luận.
assessment: ASSESSED = đã khảo sát; LIMITED = khảo sát bị hạn chế; NOT_ASSESSABLE = không thể khảo sát; NOT_ASSESSED = chưa khảo sát.
Chỉ trích những gì báo cáo nói, không suy diễn."""

results: list[tuple[str, bool, str]] = []
label = ""  # prefixes check names with the model/mode under test


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((label + name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def chat(model: str, thinking: bool) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=model,
        temperature=0,
        timeout=120,
        extra_body=MODEL_PARAMS[model]["thinking_on" if thinking else "thinking_off"],
    )


def probe_structured(model: str, thinking: bool, method: str) -> None:
    llm = chat(model, thinking).with_structured_output(Extraction, method=method)
    t0 = time.perf_counter()
    try:
        out: Extraction | None = llm.invoke([("system", EXTRACT_PROMPT), ("human", REPORT)])
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not crash
        out = None
        detail = repr(exc)[:200]
    else:
        detail = "parsed None"
    if out is None:
        check(f"structured[{method}] returns", False, f"{detail}, {time.perf_counter() - t0:.1f}s")
        return
    dt = time.perf_counter() - t0
    check(f"structured[{method}] returns", True, f"{len(out.mentions)} mentions, {dt:.1f}s")
    check("  spans verbatim", all(m.span in REPORT for m in out.mentions),
          str([m.span for m in out.mentions if m.span not in REPORT]))
    for key, want in EXPECTED.items():
        got = [(m.assertion, m.assessment) for m in out.mentions if key in m.span]
        check(f"  {key} -> {want}", got == [want], f"got {got}")


@tool
def search_hpo(text: str) -> str:
    """Tìm các HPO term ứng viên cho một cụm mô tả biểu hiện."""
    return "HP:0001629 Ventricular septal defect\nHP:0001631 Atrial septal defect"


def tool_names(messages) -> list[str]:
    return [m.name for m in messages if isinstance(m, ToolMessage)]


def probe_agent(model: str, thinking: bool) -> None:
    question = [{"role": "user", "content": "Mã HPO của 'thông liên thất' là gì?"}]

    agent = create_deep_agent(
        model=chat(model, thinking),
        tools=[search_hpo],
        system_prompt="Luôn dùng tool search_hpo, rồi trả lời bằng đúng một mã HPO.",
    )
    t0 = time.perf_counter()
    out = agent.invoke({"messages": question})
    answer = out["messages"][-1].text
    check("agent calls search_hpo", "search_hpo" in tool_names(out["messages"]),
          f"tools={tool_names(out['messages'])}, {time.perf_counter() - t0:.1f}s")
    check("  answer has HP:0001629", "HP:0001629" in answer, answer[:120].replace("\n", " "))

    agent = create_deep_agent(
        model=chat(model, thinking),
        system_prompt="Không tự tra cứu. Giao việc tra HPO cho subagent hpo-lookup bằng tool task.",
        subagents=[{
            "name": "hpo-lookup",
            "description": "Tra mã HPO cho một cụm mô tả biểu hiện.",
            "system_prompt": "Dùng search_hpo và trả về đúng một mã HPO.",
            "tools": [search_hpo],
        }],
    )
    t0 = time.perf_counter()
    out = agent.invoke({"messages": question})
    answer = out["messages"][-1].text
    check("agent delegates via task", "task" in tool_names(out["messages"]),
          f"tools={tool_names(out['messages'])}, {time.perf_counter() - t0:.1f}s")
    check("  answer has HP:0001629", "HP:0001629" in answer, answer[:120].replace("\n", " "))
    check("  no <think> leak", "<think>" not in answer)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


def probe_embedding() -> None:
    texts = ["thông liên thất", "Ventricular septal defect", "Microcephaly",
             "thiểu sản xương mũi thai", "Hypoplastic fetal nasal bone"]
    t0 = time.perf_counter()
    data = OpenAI(base_url=BASE_URL, api_key=API_KEY).embeddings.create(model=EMBEDDING_MODEL, input=texts).data
    v = {t: d.embedding for t, d in zip(texts, data)}
    check("bge-m3 dim == 1024", len(data[0].embedding) == 1024,
          f"dim={len(data[0].embedding)}, {time.perf_counter() - t0:.2f}s for {len(texts)}")
    match, control = cosine(v["thông liên thất"], v["Ventricular septal defect"]), cosine(v["thông liên thất"], v["Microcephaly"])
    check("  VSD vi~en > vi~Microcephaly", match > control, f"{match:.3f} vs {control:.3f}")
    nasal = cosine(v["thiểu sản xương mũi thai"], v["Hypoplastic fetal nasal bone"])
    check("  nasal bone vi~en > VSD vi~Microcephaly", nasal > control, f"{nasal:.3f}")

    # vi mention vs vi translation (Excel names, as hpo_name will hold them): should beat vi~en by far
    texts = ["lỗ thông liên thất phần màng", "Thông liên thất", "Giãn não thất", "Ventricular septal defect"]
    data = OpenAI(base_url=BASE_URL, api_key=API_KEY).embeddings.create(model=EMBEDDING_MODEL, input=texts).data
    m, vi_ok, vi_bad, en = (d.embedding for d in data)
    check("  vi mention: vi name > other vi name", cosine(m, vi_ok) > cosine(m, vi_bad),
          f"{cosine(m, vi_ok):.3f} vs {cosine(m, vi_bad):.3f} (en label {cosine(m, en):.3f})")


if __name__ == "__main__":
    print("== embedding")
    probe_embedding()
    for model in sys.argv[1:] or list(MODEL_PARAMS):
        for thinking in (False, True):
            label = f"{model}/thinking={'on' if thinking else 'off'}: "
            print(f"== {label}")
            for method in ("json_schema", "function_calling"):
                probe_structured(model, thinking, method)
            probe_agent(model, thinking)
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
    sys.exit(1 if failed else 0)
