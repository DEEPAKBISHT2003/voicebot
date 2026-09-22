import math
from typing import List, Dict, Any, Optional, Union
from loguru import logger


def calculate_final_score(
    confirmed_qa_pairs: Optional[List[Dict[str, Any]]] = None,
    holistic_competency: Optional[Union[int, float, Dict[str, Any]]] = None,
    *,
    final_evaluation_report: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Phase 3C: Deterministic Final Score Engine for AI Interview Copilot.

    Locked Architecture:
        Overall Score = (Q&A Accuracy Average * 0.70) + (Holistic Competency * 0.30)

    Rules & Invariants:
    1. Zero LLM calls. Fully deterministic.
    2. Does NOT mutate input confirmed_qa_pairs or report dictionaries.
    3. Q&A Accuracy Average is arithmetic mean of all VALID Phase 2V accuracy_score values (0-100).
    4. Invalid, non-numeric, or missing accuracy scores are skipped and NOT converted to 0.
    5. If zero valid Q&A scores exist:
       qa_accuracy_average = None, overall_score = None, score_status = 'insufficient_evidence'.
    6. If holistic_competency is missing, non-numeric, or out of bounds (0-100):
       holistic_competency_score = None, overall_score = None, score_status = 'insufficient_evidence'.
    7. Overall score is rounded with standard deterministic round() and clamped to 0-100.
    8. No hiring decision or threshold inference is performed here.
    """
    # 1. Extract confirmed_qa_pairs from report if not explicitly supplied
    qa_list: List[Dict[str, Any]] = []
    if confirmed_qa_pairs is not None:
        if isinstance(confirmed_qa_pairs, list):
            qa_list = confirmed_qa_pairs
    elif final_evaluation_report is not None and isinstance(final_evaluation_report, dict):
        if "question_analysis" in final_evaluation_report and isinstance(final_evaluation_report["question_analysis"], list):
            qa_list = final_evaluation_report["question_analysis"]
        elif "confirmed_qa_pairs" in final_evaluation_report and isinstance(final_evaluation_report["confirmed_qa_pairs"], list):
            qa_list = final_evaluation_report["confirmed_qa_pairs"]

    # 2. Extract holistic competency score
    raw_h_val: Any = None
    if holistic_competency is not None:
        if isinstance(holistic_competency, dict):
            raw_h_val = holistic_competency.get("score")
        else:
            raw_h_val = holistic_competency
    elif final_evaluation_report is not None and isinstance(final_evaluation_report, dict):
        h_obj = final_evaluation_report.get("holistic_competency")
        if isinstance(h_obj, dict):
            raw_h_val = h_obj.get("score")
        elif isinstance(h_obj, (int, float)) and not isinstance(h_obj, bool):
            raw_h_val = h_obj

    # Validate holistic score
    valid_holistic_score: Optional[int] = None
    if raw_h_val is not None and isinstance(raw_h_val, (int, float)) and not isinstance(raw_h_val, bool):
        if not math.isnan(raw_h_val) and not math.isinf(raw_h_val):
            if 0 <= raw_h_val <= 100:
                valid_holistic_score = int(round(raw_h_val))
            else:
                logger.warning(f"[FinalScore] Holistic competency score out of bounds (0-100): {raw_h_val}")
        else:
            logger.warning(f"[FinalScore] Holistic competency score is NaN or Inf: {raw_h_val}")
    elif raw_h_val is not None:
        logger.warning(f"[FinalScore] Holistic competency score is non-numeric: {type(raw_h_val)}")

    # 3. Extract and validate Phase 2V accuracy scores
    valid_qa_scores: List[float] = []
    for item in qa_list:
        if not isinstance(item, dict):
            continue
        raw_acc = item.get("accuracy_score")
        if raw_acc is None:
            continue
        if isinstance(raw_acc, (int, float)) and not isinstance(raw_acc, bool):
            if not math.isnan(raw_acc) and not math.isinf(raw_acc):
                if 0 <= raw_acc <= 100:
                    valid_qa_scores.append(float(raw_acc))
                else:
                    logger.warning(f"[FinalScore] Q&A accuracy score out of range (0-100): {raw_acc}")
            else:
                logger.warning(f"[FinalScore] Q&A accuracy score is NaN or Inf: {raw_acc}")
        else:
            logger.warning(f"[FinalScore] Q&A accuracy score is non-numeric: {type(raw_acc)}")

    qa_evaluated_count = len(valid_qa_scores)
    qa_accuracy_average: Optional[int] = None
    if qa_evaluated_count > 0:
        mean_val = sum(valid_qa_scores) / qa_evaluated_count
        qa_accuracy_average = max(0, min(100, int(round(mean_val))))

    # 4. Deterministic Overall Score Calculation (70% Q&A + 30% Holistic)
    overall_score: Optional[int] = None
    score_status: str = "complete"

    if qa_accuracy_average is not None and valid_holistic_score is not None:
        weighted_sum = (qa_accuracy_average * 0.70) + (valid_holistic_score * 0.30)
        overall_score = max(0, min(100, int(round(weighted_sum))))
        score_status = "complete"
    else:
        score_status = "insufficient_evidence"

    return {
        "qa_accuracy_average": qa_accuracy_average,
        "holistic_competency_score": valid_holistic_score,
        "overall_score": overall_score,
        "qa_evaluated_count": qa_evaluated_count,
        "score_status": score_status
    }
