import copy

from eval.e2_check import check, load_cases
from eval.e2_run import score_case, totals
from kb.db import connect
from pipeline.schema import Analysis, Finding, Term


def gold(phrase, assertion, hpo_id, assessment="ASSESSED", subject="FETUS"):
    return {"phrase": phrase, "assertion": assertion, "assessment": assessment, "subject": subject, "hpo_id": hpo_id}


def finding(phrase, assertion, status, hpo_id=None, assessment="ASSESSED", subject="FETUS"):
    term = Term(hpo_id=hpo_id, label_en="x", label_vi=None, label_vi_reviewed=False, definition_en=None) if hpo_id else None
    return Finding(phrase=phrase, source_text=phrase, assertion=assertion, assessment=assessment, temporality=None,
                   subject=subject, mapping_status=status, term=term)


def analysis(findings, **flags):
    return Analysis(text="", hpo_release="test", findings=findings, **flags).model_dump(mode="json")


NEAR = {"HP:0001629": {"HP:0011682", "HP:0001627"}}  # VSD: a child and the parent, hand-built
GOLD = [
    gold("đa ối", "PRESENT", "HP:0001561"),
    gold("bàn chân", "UNCERTAIN", None, "LIMITED"),
    gold("cột sống", "UNCERTAIN", None, "LIMITED"),
    gold("thông liên thất", "PRESENT", "HP:0001629"),
    gold("tràn dịch màng ngoài tim", "ABSENT", "HP:0001698"),
    gold("khe hở môi", "PRESENT", "HP:0410030"),                                # not split out: missed
]
FINDINGS = [
    finding("đa ối", "PRESENT", "MAPPED", "HP:0001561"),                        # correct
    finding("bàn chân", "UNCERTAIN", "UNMAPPED", assessment="LIMITED"),         # null gold, UNMAPPED: correct
    finding("cột sống", "UNCERTAIN", "MAPPED", "HP:0002650", assessment="LIMITED"),  # null gold, MAPPED: wrong
    finding("thông liên thất", "PRESENT", "MAPPED", "HP:0011682"),              # direct child: near only
    finding("tràn dịch màng ngoài tim", "PRESENT", "MAPPED", "HP:0001698"),     # right code, wrong assertion
    finding("dây rốn bám màng", "PRESENT", "MAPPED", "HP:0025475"),             # no gold item: extra
]


def test_score_hand_built_case():
    s = score_case(GOLD, analysis(FINDINGS), NEAR)
    assert (s["gold_items"], s["model_items"], s["matched"]) == (6, 6, 5)
    assert s["status_correct"] == 4
    assert ["ABSENT", "PRESENT"] in s["assertion_pairs"]
    assert (s["correct"], s["near_correct"]) == (3, 4)
    assert (s["unmapped"], s["ambiguous"]) == (1, 0)
    # G = {1561 P, 1629 P, 1698 ABSENT, 0410030 P}; P = {1561 P, 2650 U, 11682 P, 1698 P, 25475 P}
    assert (s["profile_hits"], s["profile_gold"], s["profile_pred"]) == (1, 4, 5)


def test_failed_and_errored_cases_have_no_findings():
    failed = score_case(GOLD, analysis([], split_failed=True), NEAR)
    assert failed["split_failed"] and failed["matched"] == failed["model_items"] == 0
    errored = score_case(GOLD, None, NEAR)
    assert errored["error"] and errored["profile_gold"] == 4 and errored["profile_pred"] == 0


def test_totals_pool_cases():
    scores = [score_case(GOLD, analysis(FINDINGS), NEAR), score_case(GOLD, None, NEAR)]
    t = totals(scores, [{"split": 2.0, "graph": 4.0}, {"split": 6.0, "graph": 8.0}])
    assert t["split_recall"][0] == 5 / 12 and t["split_precision"][0] == 5 / 6
    assert t["mapping_accuracy"][0] == 3 / 5 and t["profile_recall"][0] == 1 / 8
    assert t["confusion"][("ABSENT", "PRESENT")] == 1 and t["error"] == 1
    assert t["seconds"]["graph"][0] == 6.0
    nothing_mapped = score_case(GOLD, analysis([finding("đa ối", "PRESENT", "UNMAPPED")]), NEAR)
    assert totals([nothing_mapped], [])["profile_precision"] == (0.0, 0.0, 0.0)


def test_gold_against_itself_is_perfect():
    for case in load_cases():
        findings = [finding(i["phrase"], i["assertion"], "MAPPED" if i["hpo_id"] else "UNMAPPED", i["hpo_id"],
                            i["assessment"], i["subject"]) for i in case["items"]]
        s = score_case(case["items"], analysis(findings), {})
        n = len(case["items"])
        assert s["matched"] == s["status_correct"] == s["correct"] == n, case["id"]
        assert s["profile_hits"] == s["profile_gold"] == s["profile_pred"], case["id"]


def test_check_catches_bad_items():
    with connect() as conn:
        obsolete = conn.execute("SELECT hpo_id FROM hpo_term WHERE is_obsolete LIMIT 1").fetchone()[0]
    cases = load_cases()
    bad = copy.deepcopy(cases[0])
    bad["items"][0]["hpo_id"] = obsolete
    bad["items"][1] |= {"assertion": "PRESENT", "hpo_id": None}
    other = copy.deepcopy(cases[0]) | {"id": "E2-XX", "disease_id": cases[1]["disease_id"]}
    errors = check([bad, other])
    assert any(obsolete in e and "not a current term" in e for e in errors)
    assert any(e.startswith(f"{bad['id']} '") and "hpo_id null but assertion PRESENT" in e for e in errors)
    assert any(e.startswith("E2-XX:") and f"items from {cases[1]['disease_id']}" in e for e in errors)
