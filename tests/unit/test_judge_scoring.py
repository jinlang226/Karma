from app.judge.scoring import compute_weighted_scores


def test_compute_weighted_scores_uses_tracks_and_weights():
    rubric = {
        "objective_weights": {"process_quality": 0.8, "efficiency": 0.2},
        "questions": [
            {"id": "q1", "track": "process_quality", "weight": 0.5, "prompt": "a"},
            {"id": "q2", "track": "process_quality", "weight": 0.5, "prompt": "b"},
            {"id": "q3", "track": "efficiency", "weight": 1.0, "prompt": "c"},
        ],
    }
    model_output = {
        "dimension_scores": [
            {"id": "q1", "score": 5, "confidence": 1.0, "evidence_ids": ["E001"]},
            {"id": "q2", "score": 3, "confidence": 0.8, "evidence_ids": ["E002"]},
            {"id": "q3", "score": 2, "confidence": 0.6, "evidence_ids": ["E003"]},
        ]
    }

    scores = compute_weighted_scores(rubric, model_output)
    assert scores["process_quality_score"] == 4.0
    assert scores["efficiency_score"] == 2.0
    assert scores["final_score"] == 3.6
    assert scores["scored_dimensions"] == 3


def test_compute_weighted_scores_renormalizes_when_efficiency_missing():
    rubric = {
        "objective_weights": {"process_quality": 0.72, "efficiency": 0.28},
        "questions": [
            {"id": "q1", "track": "process_quality", "weight": 1.0, "prompt": "a"},
            {"id": "q2", "track": "efficiency", "weight": 1.0, "prompt": "b"},
        ],
    }
    model_output = {
        "dimension_scores": [
            {"id": "q1", "score": 4, "confidence": 0.9, "evidence_ids": ["agent.log:L000001-L000002"]},
            {"id": "q2", "score": None, "confidence": 0.0, "evidence_ids": []},
        ]
    }

    scores = compute_weighted_scores(rubric, model_output)
    assert scores["process_quality_score"] == 4.0
    assert scores["efficiency_score"] is None
    assert scores["final_score"] == 4.0
    assert scores["missing_tracks"] == ["efficiency"]
    assert scores["weights"]["process_quality"] == 1.0
    assert scores["weights"]["efficiency"] == 0.0
