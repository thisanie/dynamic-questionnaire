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


def decision_evidence(answers: dict[str, Any], intake: dict[str, Any]) -> list[dict[str, Any]]:
  selected = set(answers.get("general_health", []))
  selected_ped = selected & SUPPORTED_PED
  evidence: list[dict[str, Any]] = [{
    "question_id": "general_health",
    "answer": answers.get("general_health", []),
    "impact": "Supported PED selection used for underwriting.",
  }]

  if len(selected_ped) > 1:
    evidence[0]["impact"] = "More than one supported PED selected, causing NSTP."
    evidence[0]["triggered"] = True
    return evidence

  if "Diabetes" in selected:
    member_age = intake.get("members", [{}])[0].get("age")
    checks = [
      ("member_age", member_age, "Member age must be between 31 and 55.", member_age is not None and 31 <= int(member_age) <= 55),
      ("diabetes_type", answers.get("diabetes_type"), "Must be Type 2.", answers.get("diabetes_type") == "Type 2"),
      ("diabetes_age", answers.get("diabetes_age"), "Diagnosis age must be above 30.", answers.get("diabetes_age", 0) > 30),
      ("hba1c", answers.get("hba1c"), "HbA1c must be below 10.", answers.get("hba1c", 100) < 10),
      ("hba1c_within_90_days", answers.get("hba1c_within_90_days"), "Latest HbA1c must be within 90 days.", answers.get("hba1c_within_90_days") is True),
      ("diabetes_insulin", answers.get("diabetes_insulin"), "Current insulin use must be false.", answers.get("diabetes_insulin") is False),
      ("diabetes_special_investigation", answers.get("diabetes_special_investigation"), "Special investigation must be false.", answers.get("diabetes_special_investigation") is False),
      ("diabetes_waiting_period", answers.get("diabetes_waiting_period"), "PED waiting period must be Default.", answers.get("diabetes_waiting_period") == "Default"),
    ]
    return evidence + [{"question_id": field, "answer": value, "impact": impact, "triggered": not passed} for field, value, impact, passed in checks]

  if "Hypertension" in selected:
    member_age = intake.get("members", [{}])[0].get("age")
    checks = [
      ("member_age", member_age, "Member age must be between 31 and 55.", member_age is not None and 31 <= int(member_age) <= 55),
      ("hypertension_age", answers.get("hypertension_age"), "Diagnosis age must be above 30.", answers.get("hypertension_age", 0) > 30),
      ("systolic_bp", answers.get("systolic_bp"), "Systolic BP must be 160 or lower.", answers.get("systolic_bp", 10_000) <= 160),
      ("diastolic_bp", answers.get("diastolic_bp"), "Diastolic BP must be 110 or lower.", answers.get("diastolic_bp", 10_000) <= 110),
      ("bp_within_90_days", answers.get("bp_within_90_days"), "Latest BP reading must be within 90 days.", answers.get("bp_within_90_days") is True),
      ("hypertension_special_investigation", answers.get("hypertension_special_investigation"), "Special investigation must be false.", answers.get("hypertension_special_investigation") is False),
      ("hypertension_waiting_period", answers.get("hypertension_waiting_period"), "PED waiting period must be Default.", answers.get("hypertension_waiting_period") == "Default"),
    ]
    return evidence + [{"question_id": field, "answer": value, "impact": impact, "triggered": not passed} for field, value, impact, passed in checks]

  evidence[0]["impact"] = "No supported PED selected; default STP rule applied."
  evidence[0]["triggered"] = False
  return evidence