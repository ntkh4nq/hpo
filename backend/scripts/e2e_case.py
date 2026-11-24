"""Real end-to-end run of one case through the running API (docs/agent-impl-plan.md task 6; plan.md phase 5;
explanations and the literature turn: docs/evidence-impl-plan.md task 4).

    make up && make api        # in another terminal
    make e2e                   # about 20–25 DeepSeek requests + free Qwen; API_BASE_URL defaults to localhost:8000

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


def explanations(client: httpx.Client, case_id: str, label: str, suggestions: int) -> dict:
    """POST /explanations and check its shape (evidence-plan §4.3)."""
    print(f"\n=== {label}  (POST /cases/{case_id}/explanations)")
    start = time.perf_counter()
    resp = client.post(f"{BASE}/cases/{case_id}/explanations")
    print(f"  HTTP {resp.status_code}  ⏱ {time.perf_counter() - start:.1f} s")
    check(resp.status_code == 200, f"{label}: HTTP 200")
    if resp.status_code != 200:
        print(resp.text)
        return {}
    e = resp.json()
    pairs = [p for i in e["items"] for p in i["pairs"]]
    counts = {s: sum(p["status"] == s for p in pairs) for s in ("cited", "no_supporting_passage", "no_passage", "failed")}
    print(f"  cached={e['cached']} usage={e['usage']} statuses={counts}")
    for i in e["items"]:
        for p in i["pairs"]:
            for c in p["claims"]:
                print(f"    {i['disease_id']} {p['disease_hpo_id']}: {c['text']} {c['refs']}")
    check(len(e["items"]) == suggestions, f"{label}: one item per suggestion")
    check(all(r in e["passages"] for p in pairs for c in p["claims"] for r in c["refs"]),
          f"{label}: every cited ref is in passages")
    check(all(p["claims"] for p in pairs if p["status"] == "cited"), f"{label}: every cited pair has claims")
    return e


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
        top = len(t1c[-1][1]["view"]["ranking"]["suggestions"]) if last(t1c) == "done" else 0
        e1 = explanations(client, case_id, "1 · giải thích", top)
        e1b = explanations(client, case_id, "1 · giải thích lần 2", top)
        check(bool(e1) and e1b.get("explanation_key") == e1.get("explanation_key") and e1b.get("cached") is True
              and e1b.get("usage", {}).get("requests") == 0, "1: lần 2 trúng cache, 0 request")

        t2 = turn(client, "2 · so sánh", f"/cases/{case_id}/messages",
                  {"text": "Bệnh hạng 1 và hạng 2 khác nhau ở đâu, lần khám sau nên xem thêm gì để phân biệt?"})
        check("compare_diseases" in tools(t2), "2: có compare_diseases")
        answered(t2, "2")

        t3 = turn(client, "3 · xếp hạng thử", f"/cases/{case_id}/messages",
                  {"text": "Nếu thai có thêm đa ối thì thứ hạng thay đổi thế nào?"})
        check("rank_what_if" in tools(t3), "3: có rank_what_if")
        answered(t3, "3")

        # (4) card action: drop a finding the ranking uses (an unmapped one would leave the explanations unchanged),
        # keeping the earlier edit and note
        dropped = t1c[-1][1]["view"]["ranking"]["terms"][0]["finding_ids"][0] if last(t1c) == "done" else "f1"
        print(f"\n=== 4 · bỏ {dropped} khỏi xếp hạng  (POST /cases/{case_id}/findings)")
        edits[dropped] = {**edits.get(dropped, {"finding_id": dropped}), "excluded": True}
        start = time.perf_counter()
        resp = client.post(f"{BASE}/cases/{case_id}/findings", json={"edits": list(edits.values()), "notes": notes})
        print(f"  HTTP {resp.status_code}  ⏱ {time.perf_counter() - start:.2f} s")
        view = resp.json() if resp.status_code == 200 else {}
        skipped = [s.get("finding_id") for s in (view.get("ranking") or {}).get("skipped", [])]
        check(resp.status_code == 200 and dropped in skipped, f"4: {dropped} nằm trong ranking.skipped")
        e4 = explanations(client, case_id, "4 · giải thích sau khi sửa", len(view.get("ranking", {}).get("suggestions", [])))
        check(bool(e4) and e4.get("explanation_key") != e1.get("explanation_key"), "4: khoá giải thích đổi")

        t5a = turn(client, "5 · câu hỏi kèm mô tả", f"/cases/{case_id}/messages",
                   {"text": "Thai 22 tuần có thông liên thất, đa ối. Có phải CHARGE không?"})
        check(last(t5a) == "review", "5: dừng ở bước xem biểu hiện")
        t5b = turn(client, "5 · Tiếp tục", f"/cases/{case_id}/review", {"action": "continue"})
        used = tools(t5a, t5b)
        check(len(t5a[-1][1]["findings"]) > len(findings) if last(t5a) == "review" else False,
              "5: tin nhắn được thêm vào mô tả, giữ biểu hiện cũ (T137)")
        check("analyze_findings" in used and any(n == "artifact" and d["kind"] == "ranking" for n, d in t5b),
              "5: analyze_findings, có ranking")
        answered(t5b, "5")

        t6 = turn(client, "6 · câu hỏi kiến thức", f"/cases/{case_id}/messages",
                  {"text": "Hội chứng CHARGE thường thấy gì trên siêu âm?"})
        used = tools(t6)
        # The literature tool is an equally valid answer to this knowledge question (docs/evidence-impl-plan.md
        # task 4); the disease code comes from search_disease or from the case's own list.
        check(("get_disease" in used or "find_passages" in used) and "analyze_findings" not in used,
              "6: get_disease hoặc find_passages, không analyze_findings")
        answered(t6, "6")

        t7 = turn(client, "7 · hỏi tài liệu", f"/cases/{case_id}/messages",
                  {"text": "Tài liệu nói gì về tật đầu nhỏ ở bệnh hạng 1?"})
        check("find_passages" in tools(t7), "7: có find_passages")
        answered(t7, "7")
        chunks = [c["id"] for c in (t7[-1][1].get("citations") or []) if c["kind"] == "chunk"] if last(t7) == "done" else []
        print(f"  CHUNK citations: {chunks}")
        for ref in chunks:
            check(client.get(f"{BASE}/passages/{ref.split(':')[1]}").status_code == 200, f"7: GET /passages của {ref}")

    print(f"\ncase_id (Langfuse session): {case_id}")
    if FAILED:
        print(f"{len(FAILED)} kiểm tra không đạt: {FAILED}")
        sys.exit(1)
    print("Mọi kiểm tra đều đạt.")


if __name__ == "__main__":
    main()
