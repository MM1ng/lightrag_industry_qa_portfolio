from industrial_rag.services.retrieval_evaluation import evaluate_rankings, reciprocal_rank


def test_reciprocal_rank_uses_first_relevant_result_and_cutoff():
    relevant = {"c2", "c9"}
    assert reciprocal_rank(["c1", "c2", "c9"], relevant, k=5) == 0.5
    assert reciprocal_rank(["c1", "c2", "c9"], relevant, k=1) == 0.0


def test_evaluate_rankings_deduplicates_ids_and_reports_recall_and_mrr():
    cases = [
        {"question": "2196-R 参数", "question_type": "model", "relevant_chunk_ids": ["c2"]},
        {"question": "机械密封", "question_type": "fault", "relevant_chunk_ids": ["c3"]},
    ]
    rankings = {
        "baseline": [["c1", "c2", "c2"], ["c9"]],
        "candidate": [["c2"], ["c1", "c3"]],
    }

    report = evaluate_rankings(cases, rankings)

    assert report["baseline"]["overall"]["recall@5"] == 0.5
    assert report["baseline"]["overall"]["mrr@5"] == 0.25
    assert report["candidate"]["overall"]["recall@5"] == 1.0
    assert report["candidate"]["overall"]["mrr@5"] == 0.75
    assert report["candidate"]["model"]["recall@10"] == 1.0


def test_evaluate_rankings_rejects_mismatched_case_count():
    cases = [{"question": "q", "relevant_chunk_ids": ["c1"]}]
    try:
        evaluate_rankings(cases, {"baseline": []})
    except ValueError as error:
        assert "case count" in str(error)
    else:
        raise AssertionError("expected case count validation")
