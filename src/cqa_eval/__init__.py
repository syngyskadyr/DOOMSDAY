"""Public backend interfaces; importing this package never makes an API call."""
from .core import calculate_score
from .validation import ValidatedAssessment, ValidationError, validate_response
from .evaluator import evaluate_dialogue, EvaluationError

__all__ = ["evaluate_dialogue", "calculate_score", "validate_response",
           "ValidatedAssessment", "ValidationError", "EvaluationError"]
