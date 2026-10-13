from __future__ import annotations

from typing import Any


SUPPORTED_PED = {"Diabetes", "Hypertension"}


def early_nstp(
  answers: dict[str, Any],
  skipped_diseases: set[str] | None = None,
) -> dict[str, str] | None:
  selected = set(answers.get("general_health", []))
  skipped_diseases = skipped_diseases or set()
  if "Diabetes" in selected and "Diabetes" not in skipped_diseases:
    checks = (
      ("diabetes_type", lambda value: value != "Type 2", "Diabetes type does not meet the STP criteria."),
      ("diabetes_age", lambda value: value <= 30, "Diabetes onset age does not meet the STP criteria."),
      ("hba1c", lambda value: value >= 10, "HbA1c does not meet the STP criteria."),
      ("hba1c_within_90_days", lambda value: value is False, "The HbA1c test was not within the last 90 days."),
      ("diabetes_insulin", lambda value: value is True, "Current insulin use does not meet the STP criteria."),
      ("diabetes_special_investigation", lambda value: value is True, "A related special investigation or treatment was declared."),
      ("diabetes_waiting_period", lambda value: value != "Default", "The default PED waiting period was not selected."),
    )
    for field, fails, reason in checks:
      if field in answers and fails(answers[field]):
        return {"disease": "Diabetes", "reason": reason}

  if "Hypertension" in selected and "Hypertension" not in skipped_diseases:
    checks = (
      ("hypertension_age", lambda value: value <= 30, "Hypertension onset age does not meet the STP criteria."),
      ("systolic_bp", lambda value: value > 160, "Systolic blood pressure exceeds the STP limit."),
      ("diastolic_bp", lambda value: value > 110, "Diastolic blood pressure exceeds the STP limit."),
      ("bp_within_90_days", lambda value: value is False, "The blood pressure reading was not within the last 90 days."),
      ("hypertension_special_investigation", lambda value: value is True, "A related special investigation or treatment was declared."),
      ("hypertension_waiting_period", lambda value: value != "Default", "The default PED waiting period was not selected."),
    )
    for field, fails, reason in checks:
      if field in answers and fails(answers[field]):
        return {"disease": "Hypertension", "reason": reason}

  return None


def _member_age_eligible(intake: dict[str, Any]) -> bool:
  return any(31 <= int(member.get("age", -1)) <= 55 for member in intake.get("members", []))


def _diabetes_stp(answers: dict[str, Any], intake: dict[str, Any]) -> bool:
  return (
    _member_age_eligible(intake)
    and answers.get("diabetes_type") == "Type 2"
    and answers.get("diabetes_age", 0) > 30
    and answers.get("hba1c", 100) < 10
    and answers.get("hba1c_within_90_days") is True
    and answers.get("diabetes_insulin") is False
    and answers.get("diabetes_special_investigation") is False
    and answers.get("diabetes_waiting_period") == "Default"
  )


def _hypertension_stp(answers: dict[str, Any], intake: dict[str, Any]) -> bool:
  return (
    _member_age_eligible(intake)
    and answers.get("hypertension_age", 0) > 30
    and answers.get("systolic_bp", 10_000) <= 160
    and answers.get("diastolic_bp", 10_000) <= 110
    and answers.get("bp_within_90_days") is True
    and answers.get("hypertension_special_investigation") is False
    and answers.get("hypertension_waiting_period") == "Default"
  )


def decide(answers: dict[str, Any], intake: dict[str, Any]) -> tuple[str, str]:
  selected = set(answers.get("general_health", []))
  selected_ped = selected & SUPPORTED_PED

  if len(selected_ped) > 1:
    return "NSTP", "More than one supported PED was selected."

  if "Diabetes" in selected:
    if _diabetes_stp(answers, intake):
      return "STP with PED with exclusion", "Diabetes criteria matched for STP with PED exclusion."
    return "NSTP", "The declared diabetes condition did not meet every STP criterion."

  if "Hypertension" in selected:
    if _hypertension_stp(answers, intake):
      return "STP with PED with exclusion", "Hypertension criteria matched for STP with PED exclusion."
    return "NSTP", "The declared hypertension condition did not meet every STP criterion."

  return "STP", "No diabetes or hypertension STP rule was applicable."