"""Real end-to-end run of one case through the running API (docs/agent-impl-plan.md task 6; plan.md phase 5).

    make up && make api        # in another terminal
    make e2e                   # about 15–25 DeepSeek requests + free Qwen; API_BASE_URL defaults to localhost:8000

Prints every progress step, the streamed text, the time of each turn and the citations; exits 1 if a check fails.
"""

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
E2 = Path(__file__).resolve().parents[1] / "evals" / "e2" / "dataset.jsonl"
FAILED: list[str] = []


def check(ok: bool, what: str) -> None:
    print(f"  {'✓' if ok else '✗'} {what}")
    if not ok:
        FAILED.append(what)


def show(name: str, d: dict) -> None:
    if name == "step":
        indent = {"todo": "", "tool": "    ", "node": "        "}[d["kind"]]
        mark = {"running": "◐", "done": "✓", "error": "✗"}[d["status"]]
        label = d["label"] or d["key"]
        extra = f"  {d['params']}" if d["params"] and d["status"] == "running" else ""
        print(f"\n{indent}{mark} {label}{extra}{'  ' + json.dumps(d['summary']) if d['summary'] else ''}", end="")
    elif name == "text":
        print(d["delta"], end="", flush=True)
    elif name == "answer_reset":
        print("\n  [bước kiểm xoá đoạn vừa hiện]", end="")
    elif name == "artifact":
        size = len(d["data"]) if d["kind"] == "findings" else len(d["data"]["suggestions"])
        print(f"\n  [artifact {d['kind']}: {size}]", end="")
    elif name == "review":
        print("\n  [chờ bác sĩ xem biểu hiện]")
        for f in d["findings"]:
            term = (f["term"] or {}).get("hpo_id") or ", ".join(o["hpo_id"] for o in f["options"]) or "—"
            print(f"    {f['finding_id']} | {f['phrase']} | {f['assertion']} | {f['subject']} | {f['mapping_status']} {term}")
    elif name == "done":
        print(f"\n  [xong] citations: {[(c['id'], c['label']) for c in d['citations']]}")
    elif name in ("error", "case"):
        print(f"\n  [{name}] {d}")


def turn(client: httpx.Client, title: str, path: str, body: dict) -> list[tuple[str, dict]]:
    print(f"\n=== {title}  (POST {path})")
    events, name, start = [], None, time.perf_counter()
    with client.stream("POST", BASE + path, json=body) as resp:
        if resp.status_code != 200:
            raise SystemExit(f"{path}: HTTP {resp.status_code} {resp.read().decode()}")
        for line in resp.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                events.append((name, data))
                show(name, data)
    print(f"  ⏱ {time.perf_counter() - start:.1f} s")
    return events


def tools(*responses) -> list[str]:
    return [d["key"][5:] for evs in responses for n, d in evs if n == "step" and d["kind"] == "tool" and d["status"] == "running"]


def last(evs) -> str:
    return evs[-1][0]


def answered(evs, label: str) -> None:
    check(last(evs) == "done", f"{label}: lượt kết thúc bằng done")
    done = evs[-1][1] if last(evs) == "done" else {}
    check(bool(done.get("citations")), f"{label}: câu trả lời có citations")
    resets = sum(n == "answer_reset" for n, _ in evs)
    if resets:
        print(f"  (bước kiểm đã xoá {resets} đoạn trong lượt này)")


def main() -> None:
    case = next(json.loads(line) for line in E2.read_text(encoding="utf-8").splitlines() if '"E2-01"' in line)
    with httpx.Client(timeout=600) as client:
        # (1) full description; at review: remap, then continue with one edit and one note
        t1a = turn(client, "1 · mô tả E2-01", "/cases", {"text": case["text"]})
        case_id = next(d["case_id"] for n, d in t1a if n == "case")
        check(last(t1a) == "review", "1: dừng ở bước xem biểu hiện")
        findings = t1a[-1][1]["findings"]
        target = next((f for f in findings if f["mapping_status"] == "UNMAPPED"), findings[0])
        t1b = turn(client, f"1 · ánh xạ lại {target['finding_id']}", f"/cases/{case_id}/review",
                   {"action": "remap", "finding_id": target["finding_id"], "phrase": target["phrase"]})
        check(last(t1b) == "review", "1: ánh xạ lại rồi quay về bước xem")
        present = next(f for f in t1b[-1][1]["findings"] if f["assertion"] == "PRESENT")
        edits = {present["finding_id"]: {"finding_id": present["finding_id"], "assertion": "SUSPECTED"}}
        notes = {"f1": "Đề nghị siêu âm tim thai chuyên sâu."}
        t1c = turn(client, "1 · Tiếp tục", f"/cases/{case_id}/review",
                   {"action": "continue", "edits": list(edits.values()), "notes": notes})
        used = tools(t1a, t1b, t1c)
        check("analyze_findings" in used and any(n == "artifact" and d["kind"] == "ranking" for n, d in t1c),
              "1: analyze_findings, có ranking ngay khi Tiếp tục")
        answered(t1c, "1")

        t2 = turn(client, "2 · so sánh", f"/cases/{case_id}/messages",
                  {"text": "Bệnh hạng 1 và hạng 2 khác nhau ở đâu, lần khám sau nên xem thêm gì để phân biệt?"})
        check("compare_diseases" in tools(t2), "2: có compare_diseases")
        answered(t2, "2")

        t3 = turn(client, "3 · xếp hạng thử", f"/cases/{case_id}/messages",
                  {"text": "Nếu thai có thêm đa ối thì thứ hạng thay đổi thế nào?"})
        check("rank_what_if" in tools(t3), "3: có rank_what_if")
        answered(t3, "3")

        # (4) card action: drop f1 from ranking, keeping the earlier edit and note
        print(f"\n=== 4 · bỏ f1 khỏi xếp hạng  (POST /cases/{case_id}/findings)")
        edits["f1"] = {**edits.get("f1", {"finding_id": "f1"}), "excluded": True}
        start = time.perf_counter()
        resp = client.post(f"{BASE}/cases/{case_id}/findings", json={"edits": list(edits.values()), "notes": notes})
        print(f"  HTTP {resp.status_code}  ⏱ {time.perf_counter() - start:.2f} s")
        view = resp.json() if resp.status_code == 200 else {}
        skipped = [s.get("finding_id") for s in (view.get("ranking") or {}).get("skipped", [])]
        check(resp.status_code == 200 and "f1" in skipped, "4: f1 nằm trong ranking.skipped")

        t5a = turn(client, "5 · câu hỏi kèm mô tả", f"/cases/{case_id}/messages",
                   {"text": "Thai 22 tuần có thông liên thất, đa ối. Có phải CHARGE không?"})
        check(last(t5a) == "review", "5: dừng ở bước xem biểu hiện")
        t5b = turn(client, "5 · Tiếp tục", f"/cases/{case_id}/review", {"action": "continue"})
        used = tools(t5a, t5b)
        check(all(t in used for t in ("analyze_findings", "search_disease", "explain_disease"))
              and any(n == "artifact" and d["kind"] == "ranking" for n, d in t5b),
              "5: analyze_findings (có ranking), search_disease, explain_disease")
        answered(t5b, "5")

        t6 = turn(client, "6 · câu hỏi kiến thức", f"/cases/{case_id}/messages",
                  {"text": "Hội chứng CHARGE thường thấy gì trên siêu âm?"})
        used = tools(t6)
        # The disease code must come from a search_disease result: in this turn, or in turn 5 of the same case.
        check("get_disease" in used and "analyze_findings" not in used
              and ("search_disease" in used or "search_disease" in tools(t5a, t5b)),
              "6: get_disease (mã từ search_disease ở lượt này hoặc lượt 5), không analyze_findings")
        answered(t6, "6")

    print(f"\ncase_id (Langfuse session): {case_id}")
    if FAILED:
        print(f"{len(FAILED)} kiểm tra không đạt: {FAILED}")
        sys.exit(1)
    print("Mọi kiểm tra đều đạt.")


if __name__ == "__main__":
    main()
