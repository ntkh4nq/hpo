import pytest

from evals.common.calibration import finalize, report


def row(i, judge, agent, agent2, tiebreak=None):
    return {"id": str(i), "judge": judge, "agent": agent, "agent2": agent2, "tiebreak": tiebreak, "final": None}


def test_finalize_majority():
    rows = [row(1, True, True, True), row(2, False, False, False), row(3, True, True, False, tiebreak=False)]
    finalize(rows)
    assert [e["final"] for e in rows] == [True, False, False]
    assert report(rows) == pytest.approx(0.4, abs=0.01)   # judge T,F,T vs final T,F,F: po 2/3, pe 4/9


def test_finalize_needs_tiebreak_and_both_agents():
    with pytest.raises(SystemExit):
        finalize([row(1, True, True, False)])
    with pytest.raises(SystemExit):
        finalize([row(1, True, None, True)])
