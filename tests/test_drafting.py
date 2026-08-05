from tailoring.drafting import _questions_worth_asking


def test_maxed_score_suppresses_clarifying_questions():
    questions = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _questions_worth_asking(questions, 100) == []


def test_below_max_score_keeps_clarifying_questions():
    questions = [{"skill": "SQL", "type": "skill_gap", "question": "?", "suggested_answer": ""}]
    assert _questions_worth_asking(questions, 99) == questions


def test_empty_questions_stay_empty_regardless_of_score():
    assert _questions_worth_asking([], 42) == []
    assert _questions_worth_asking([], 100) == []
