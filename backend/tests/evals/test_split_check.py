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
    assert all(3 <= len(c["items"]) <= 12 for c in cases)


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
