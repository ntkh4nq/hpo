from evals.common.matching import similarity
from evals.split_check.score import load_cases, score_case


def item(phrase, assertion="PRESENT", assessment="ASSESSED", subject="FETUS"):
    return {"phrase": phrase, "assertion": assertion, "assessment": assessment, "subject": subject}


def test_gold_against_itself_is_perfect():
    for case in load_cases():
        s = score_case(case["items"], case["items"])
        assert s["matched"] == len(case["items"]) and not (s["missed"] or s["extra"] or s["wrong_status"])


def test_dataset_shape():
    cases = load_cases()
    assert len(cases) == 40 and len({c["id"] for c in cases}) == 40
    written = [[i for i in c["items"] if not (i.get("measurement") and i["assertion"] == "UNCERTAIN")] for c in cases]
    assert all(3 <= len(items) <= 12 for items in written)   # measurements awaiting the doctor (T119) not counted
    assert sum(len(c["items"]) for c in cases) - sum(map(len, written)) == 5


def test_similarity_keeps_diacritics_and_ignores_case():
    assert similarity("Đa ối", "đa ối") == 1.0
    assert similarity("giãn bể lớn", "gian be lon") == 0.0


def test_missed_extra_and_wrong_status():
    gold = [item("bánh nhau dày"), item("bánh nhau phù"), item("ối", "ABSENT")]
    pred = [item("bánh nhau dày"), item("nước ối", "PRESENT"), item("dây rốn quấn cổ")]
    s = score_case(gold, pred, threshold=0.5)
    assert [g["phrase"] for g in s["missed"]] == ["bánh nhau phù"]  # one-to-one: "dày" already taken
    assert [p["phrase"] for p in s["extra"]] == ["dây rốn quấn cổ"]
    assert s["wrong_status"][0]["fields"] == ["assertion"]
    assert s["fuzzy"] == [("ối", "nước ối", 0.67)]


def test_measurement_value_is_scored_only_when_gold_has_one():
    gold = [{**item("bất thường chỉ số ối", "UNCERTAIN"), "measurement": {"quantity": "chỉ số ối", "value": "26 cm"}},
            item("đa ối")]
    pred = [{**item("bất thường chỉ số ối", "UNCERTAIN"), "measurement": {"quantity": "chỉ số ối", "value": "26  cm"}},
            {**item("đa ối"), "measurement": {"quantity": "x", "value": "1"}}]
    assert score_case(gold, pred)["measured"] == 1 and score_case(gold, pred)["wrong_measurement"] == []
    pred[0]["measurement"]["value"] = "2,6 cm"
    assert len(score_case(gold, pred)["wrong_measurement"]) == 1
    pred[0]["measurement"] = None
    assert len(score_case(gold, pred)["wrong_measurement"]) == 1
