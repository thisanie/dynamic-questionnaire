from __future__ import annotations

import secrets
import json
import errno
import os
from pathlib import Path
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from decision_engine import decide, decision_evidence, early_nstp
from questionnaire_engine import Questionnaire, QuestionnaireError


ROOT = Path(__file__).parent
questionnaire = Questionnaire.from_file(ROOT / "questionnaire.json")
plans = json.loads((ROOT / "plans.json").read_text(encoding="utf-8"))
INTAKES_PATH = Path(
  os.environ.get(
    "INTAKES_PATH",
    "/tmp/intakes.json" if os.environ.get("VERCEL") else str(ROOT / "intakes.json"),
  )
)
app = FastAPI(title="Health questionnaire API", version="1.0.0")
sessions: dict[str, dict[str, Any]] = {}


def skipped_question_ids(diseases: list[str]) -> set[str]:
  skipped = set()
  for disease in diseases:
    if disease == "Diabetes":
      skipped.update(
        question["id"] for question in questionnaire.questions
        if question["id"].startswith("diabetes_") or question["id"].startswith("hba1c")
      )
    if disease == "Hypertension":
      skipped.update(
        question["id"] for question in questionnaire.questions
        if question["id"].startswith("hypertension_")
        or question["id"] in {"systolic_bp", "diastolic_bp", "bp_within_90_days"}
      )
  return skipped


def member_ids(session: dict[str, Any]) -> list[str]:
  return [member["member_id"] for member in session["intake"]["members"]]


def active_member(session: dict[str, Any]) -> dict[str, Any] | None:
  active_id = session.get("active_member_id")
  return next(
    (member for member in session["intake"]["members"] if member["member_id"] == active_id),
    None,
  )


def active_question(session: dict[str, Any]) -> dict[str, Any] | None:
  if not session.get("active_member_id") or session["member_stopped"].get(session["active_member_id"]):
    return None
  return questionnaire.next_question(
    session["answers"], skipped_question_ids(session["skipped_diseases"])
  )


class AnswerRequest(BaseModel):
  question_id: str
  value: Any = Field(default=None)


class Member(BaseModel):
  relation: str = Field(min_length=1)
  name: str = Field(min_length=1)
  gender: str = Field(min_length=1)
  dob: date


class IntakeRequest(BaseModel):
  product: str
  sum_assured: float = Field(gt=0)
  tenure: int = Field(gt=0)
  proposed_start_date: date
  expiry_date: date
  members: list[Member] = Field(min_length=1)

  @field_validator("product")
  @classmethod
  def product_must_exist(cls, value: str) -> str:
    if value not in plans.get("products", []):
      raise ValueError("Please choose an available product.")
    return value

  @model_validator(mode="after")
  def validate_dates(self) -> "IntakeRequest":
    try:
      expected_expiry = self.proposed_start_date.replace(
        year=self.proposed_start_date.year + self.tenure
      )
    except ValueError as exc:
      if self.proposed_start_date.month != 2 or self.proposed_start_date.day != 29:
        raise ValueError("The proposed start date cannot be used with this tenure.") from exc
      expected_expiry = self.proposed_start_date.replace(
        year=self.proposed_start_date.year + self.tenure, day=28
      )
    if self.expiry_date != expected_expiry:
      raise ValueError("Expiry date must match the proposed start date and tenure.")
    if any(member.dob >= self.proposed_start_date for member in self.members):
      raise ValueError("Each date of birth must be before the proposed start date.")
    return self


def question_payload(question: dict[str, Any] | None) -> dict[str, Any] | None:
  if question is None:
    return None
  return {key: question[key] for key in ("id", "type", "text") } | {
    "options": question.get("options", [])
  }


def decision(answers: dict[str, Any], intake: dict[str, Any]) -> tuple[str, str]:
  return decide(answers, intake)


def write_intakes(records: list[dict[str, Any]]) -> None:
  global INTAKES_PATH
  try:
    INTAKES_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
  except OSError as exc:
    if exc.errno != errno.EROFS or INTAKES_PATH == Path("/tmp/intakes.json"):
      raise
    INTAKES_PATH = Path("/tmp/intakes.json")
    INTAKES_PATH.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def persist_session(session: dict[str, Any]) -> None:
  if not INTAKES_PATH.exists():
    return
  records = json.loads(INTAKES_PATH.read_text(encoding="utf-8"))
  for record in records:
    if record.get("intake_id") == session["intake"]["intake_id"]:
      record["transcript"] = session["messages"]
      record["member_answers"] = session["member_answers"]
      record["member_results"] = session.get("member_results", {})
      break
  write_intakes(records)


def state(session_id: str) -> dict[str, Any]:
  session = sessions[session_id]
  active_stopped = session.get("active_member_id") in session["member_stopped"]
  question = active_question(session)
  question = None if session["stopped"] or active_stopped else question
  early = None if session["stopped"] or active_stopped else early_nstp(
    session["answers"], set(session["skipped_diseases"])
  )
  member = active_member(session)
  if early and member:
    early = early | {"member_id": member["member_id"], "member_name": member["name"]}
  completed_members = [
    member_id for member_id in member_ids(session)
    if member_id in session["member_stopped"]
    or not session["member_answers"].get(member_id) is None
    and questionnaire.next_question(
      session["member_answers"].get(member_id, {}),
      skipped_question_ids(session["member_skipped"].get(member_id, [])),
    ) is None
  ]
  all_members_complete = len(completed_members) == len(member_ids(session))
  for member_id in completed_members:
    if member_id in session["member_results"]:
      continue
    member = next(member for member in session["intake"]["members"] if member["member_id"] == member_id)
    member_intake = session["intake"] | {"members": [member]}
    if member_id in session["member_stopped"]:
      session["member_results"][member_id] = {
        "outcome": "NSTP",
        "reason": session["member_stopped"][member_id],
        "evidence": decision_evidence(session["member_answers"].get(member_id, {}), member_intake),
      }
    else:
      outcome_value, reason_value = decision(session["member_answers"][member_id], member_intake)
      session["member_results"][member_id] = {
        "outcome": outcome_value,
        "reason": reason_value,
        "evidence": decision_evidence(session["member_answers"][member_id], member_intake),
      }
  if session["stopped"]:
    outcome, reason = "NSTP", session["stop_reason"]
  elif question is None and session.get("active_member_id") and all_members_complete:
    nstp_members = [
      member["name"] for member in session["intake"]["members"]
      if session["member_results"].get(member["member_id"], {}).get("outcome") == "NSTP"
    ]
    if nstp_members:
      outcome = "NSTP"
      reason = f"NSTP member(s): {', '.join(nstp_members)}."
    else:
      outcome, reason = "STP", "All member questionnaires are complete and eligible for straight-through processing."
  else:
    outcome, reason = None, None
  return {
    "session_id": session_id,
    "messages": session["messages"],
    "question": question_payload(question),
    "answered": len(session["answers"]),
    "total": len(questionnaire.questions),
    "complete": session["stopped"] or all_members_complete,
    "outcome": outcome,
    "reason": reason,
    "intake": session["intake"],
    "member_results": session["member_results"],
    "active_member_id": session.get("active_member_id"),
    "active_member": active_member(session),
    "needs_member_selection": not session["stopped"] and not all_members_complete and (
      not session.get("active_member_id") or question is None
    ),
    "members": [
      {
        "member_id": member["member_id"],
        "name": member["name"],
        "relation": member["relation"],
        "completed": member["member_id"] in completed_members,
        "result": session.get("member_results", {}).get(member["member_id"]),
      }
      for member in session["intake"]["members"]
    ],
  }


def calculate_age(dob: date, as_of: date) -> int:
  return as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))


def save_intake(intake: IntakeRequest) -> dict[str, Any]:
  record = intake.model_dump(mode="json")
  record["created_at"] = date.today().isoformat()
  record["members"] = [
    member | {
      "member_id": secrets.token_urlsafe(8),
      "age": calculate_age(date.fromisoformat(member["dob"]), intake.proposed_start_date),
    }
    for member in record["members"]
  ]
  existing = []
  if INTAKES_PATH.exists():
    existing = json.loads(INTAKES_PATH.read_text(encoding="utf-8"))
  record["intake_id"] = secrets.token_urlsafe(12)
  existing.append(record)
  write_intakes(existing)
  return record


def new_session(intake: dict[str, Any]) -> str:
  session_id = secrets.token_urlsafe(16)
  sessions[session_id] = {
    "answers": {},
    "intake": intake,
    "member_answers": {},
    "member_skipped": {},
    "member_stopped": {},
    "member_results": {},
    "active_member_id": None,
    "skipped_diseases": [],
    "stopped": False,
    "stop_reason": None,
    "messages": [{"role": "assistant", "content": "Welcome. I will guide you through a short health questionnaire."}],
  }
  return session_id


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
  return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/sessions")
def create_session(intake: IntakeRequest) -> dict[str, Any]:
  session_id = new_session(save_intake(intake))
  persist_session(sessions[session_id])
  return state(session_id)


@app.get("/api/plans")
def get_plans() -> dict[str, list[str]]:
  return {"products": plans.get("products", [])}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  return state(session_id)


@app.post("/api/sessions/{session_id}/members/{member_id}/select")
def select_member(session_id: str, member_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  session = sessions[session_id]
  if member_id not in member_ids(session):
    raise HTTPException(status_code=404, detail="Member not found")
  if member_id in session["member_stopped"] or (member_id in session["member_answers"] and questionnaire.next_question(
    session["member_answers"][member_id],
    skipped_question_ids(session["member_skipped"].get(member_id, [])),
  ) is None):
    raise HTTPException(status_code=409, detail="This member's questionnaire is already complete")
  if session.get("active_member_id"):
    session["member_answers"][session["active_member_id"]] = session["answers"]
    session["member_skipped"][session["active_member_id"]] = session["skipped_diseases"]
  session["active_member_id"] = member_id
  session["answers"] = session["member_answers"].setdefault(member_id, {})
  session["skipped_diseases"] = session["member_skipped"].setdefault(member_id, [])
  member = active_member(session)
  session["messages"].append({"role": "assistant", "member_id": member_id, "content": f"Now answering for {member['name']} ({member['relation']})."})
  persist_session(session)
  return state(session_id)


@app.post("/api/sessions/{session_id}/answers")
def submit_answer(session_id: str, request: AnswerRequest) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  session = sessions[session_id]
  current = active_question(session)
  if current is None:
    raise HTTPException(status_code=409, detail="Select a member before answering")
  if current is None:
    raise HTTPException(status_code=409, detail="This questionnaire is already complete")
  if request.question_id != current["id"]:
    raise HTTPException(status_code=409, detail="This question is no longer current")
  try:
    parsed_value = questionnaire.answer(request.question_id, request.value)
  except QuestionnaireError as exc:
    raise HTTPException(status_code=422, detail=str(exc)) from exc
  session["answers"][request.question_id] = parsed_value
  session["member_answers"][session["active_member_id"]] = session["answers"]
  session["messages"].append({"role": "user", "member_id": session["active_member_id"], "content": display_value(parsed_value)})
  next_question = questionnaire.next_question(
    session["answers"], skipped_question_ids(session["skipped_diseases"])
  )
  if next_question:
    session["messages"].append({"role": "assistant", "member_id": session["active_member_id"], "content": next_question["text"]})
  else:
    member = active_member(session)
    member_intake = session["intake"] | {"members": [member]}
    outcome, reason = decision(session["answers"], member_intake)
    member_intake = session["intake"] | {"members": [member]}
    session["member_results"][session["active_member_id"]] = {
      "outcome": outcome,
      "reason": reason,
      "evidence": decision_evidence(session["answers"], member_intake),
    }
    session["messages"].append({"role": "assistant", "member_id": session["active_member_id"], "content": f"Health questions for {active_member(session)['name']} are complete."})
  persist_session(session)
  return state(session_id)


@app.post("/api/sessions/{session_id}/continue-after-nstp")
def continue_after_nstp(session_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  session = sessions[session_id]
  early = early_nstp(session["answers"], set(session["skipped_diseases"]))
  if early is None or early["disease"] in session["skipped_diseases"]:
    raise HTTPException(status_code=409, detail="There is no pending early NSTP decision")
  disease = early["disease"]
  session["skipped_diseases"].append(disease)
  session["messages"].append({"role": "assistant", "member_id": session["active_member_id"], "content": f"Continuing with the remaining questions for {active_member(session)['name']}. {disease} questions have been skipped."})
  next_question = questionnaire.next_question(
    session["answers"], skipped_question_ids(session["skipped_diseases"])
  )
  if next_question:
    session["messages"].append({"role": "assistant", "member_id": session["active_member_id"], "content": next_question["text"]})
  persist_session(session)
  return state(session_id)


@app.post("/api/sessions/{session_id}/stop-after-nstp")
def stop_after_nstp(session_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  session = sessions[session_id]
  early = early_nstp(session["answers"], set(session["skipped_diseases"]))
  if early is None:
    raise HTTPException(status_code=409, detail="There is no pending early NSTP decision")
  member = active_member(session)
  reason = f"{member['name']} was classified as NSTP for {early['disease']}. {early['reason']}"
  session["member_stopped"][session["active_member_id"]] = reason
  session["member_results"][session["active_member_id"]] = {"outcome": "NSTP", "reason": reason}
  session["messages"].append({"role": "assistant", "member_id": session["active_member_id"], "content": reason})
  persist_session(session)
  return state(session_id)


@app.post("/api/sessions/{session_id}/reset")
def reset_session(session_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  intake = sessions[session_id]["intake"]
  sessions[session_id] = {
    "answers": {},
    "intake": intake,
    "member_answers": {},
    "member_skipped": {},
    "member_stopped": {},
    "member_results": {},
    "active_member_id": None,
    "skipped_diseases": [],
    "stopped": False,
    "stop_reason": None,
    "messages": [{"role": "assistant", "content": "Fresh start. I will guide you through a short health questionnaire."}],
  }
  persist_session(sessions[session_id])
  return state(session_id)


def display_value(value: object) -> str:
  if isinstance(value, list):
    return ", ".join(value)
  if isinstance(value, bool):
    return "Yes" if value else "No"
  return str(value)