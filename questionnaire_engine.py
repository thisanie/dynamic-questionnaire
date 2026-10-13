from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


class QuestionnaireError(ValueError):
  """Raised when an answer cannot be accepted for the current question."""


class Questionnaire:
  def __init__(self, definition: dict[str, Any]) -> None:
    self.definition = definition
    self.questions = definition["questions"]

  @classmethod
  def from_file(cls, path: str | Path) -> "Questionnaire":
    with Path(path).open(encoding="utf-8") as questionnaire_file:
      return cls(json.load(questionnaire_file))

  def question(self, question_id: str) -> dict[str, Any]:
    for question in self.questions:
      if question["id"] == question_id:
        return question
    raise QuestionnaireError(f"Unknown question: {question_id}")

  def is_visible(self, question: dict[str, Any], answers: dict[str, Any]) -> bool:
    condition = question.get("visibleWhen")
    if not condition:
      return True

    return all(
      self._condition_matches(field, expected, answers.get(field))
      for field, expected in condition.items()
    )

  def next_question(
    self,
    answers: dict[str, Any],
    skipped_ids: set[str] | None = None,
  ) -> dict[str, Any] | None:
    skipped_ids = skipped_ids or set()
    for question in self.questions:
      if question["id"] not in answers and question["id"] not in skipped_ids and self.is_visible(question, answers):
        return question
    return None

  def answer(self, question_id: str, raw_value: Any) -> Any:
    question = self.question(question_id)
    question_type = question["type"]

    if question_type == "text":
      value = str(raw_value).strip()
      if not value:
        raise QuestionnaireError("Please enter an answer.")
      return value

    if question_type == "select":
      if raw_value not in question["options"]:
        raise QuestionnaireError("Please choose one of the available options.")
      return raw_value

    if question_type == "multiselect":
      values = list(raw_value or [])
      invalid = set(values) - set(question["options"])
      if invalid:
        raise QuestionnaireError("Please choose only the available options.")
      if "None" in values and len(values) > 1:
        raise QuestionnaireError("'None' cannot be selected with another option.")
      return values

    if question_type == "boolean":
      if not isinstance(raw_value, bool):
        raise QuestionnaireError("Please choose Yes or No.")
      return raw_value

    if question_type == "number":
      try:
        value = int(raw_value)
      except (TypeError, ValueError) as exc:
        raise QuestionnaireError("Please enter a whole number.") from exc
      return value

    if question_type == "decimal":
      try:
        value = float(raw_value)
      except (TypeError, ValueError) as exc:
        raise QuestionnaireError("Please enter a number, such as 6.5.") from exc
      return value

    if question_type == "date":
      try:
        return date.fromisoformat(str(raw_value)).isoformat()
      except ValueError as exc:
        raise QuestionnaireError("Please enter a valid date.") from exc

    raise QuestionnaireError(f"Unsupported question type: {question_type}")

  def outcome(self, answers: dict[str, Any]) -> str:
    for rule in self.definition.get("rules", []):
      if all(
        self._condition_matches(field, expected, answers.get(field))
        for field, expected in rule["if"].items()
      ):
        return rule["outcome"]
    return "STP"

  @staticmethod
  def _condition_matches(field: str, expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
      if "exists" in expected:
        exists = actual is not None and actual != "" and actual != []
        return exists is expected["exists"]
      if actual is None:
        return False
      for operator, target in expected.items():
        if operator == "lte" and not actual <= target:
          return False
        if operator == "lt" and not actual < target:
          return False
        if operator == "gte" and not actual >= target:
          return False
        if operator == "gt" and not actual > target:
          return False
      return True

    if isinstance(actual, list):
      return expected in actual
    return actual == expected