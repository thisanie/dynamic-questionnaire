from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from questionnaire_engine import Questionnaire, QuestionnaireError


ROOT = Path(__file__).parent
questionnaire = Questionnaire.from_file(ROOT / "questionnaire.json")
app = FastAPI(title="Health questionnaire API", version="1.0.0")
sessions: dict[str, dict[str, Any]] = {}


class AnswerRequest(BaseModel):
  question_id: str
  value: Any = Field(default=None)


def question_payload(question: dict[str, Any] | None) -> dict[str, Any] | None:
  if question is None:
    return None
  return {key: question[key] for key in ("id", "type", "text") } | {
    "options": question.get("options", [])
  }


def decision(answers: dict[str, Any]) -> tuple[str, str]:
  for rule in questionnaire.definition.get("rules", []):
    if all(
      questionnaire._condition_matches(field, expected, answers.get(field))
      for field, expected in rule["if"].items()
    ):
      reasons = []
      for field, expected in rule["if"].items():
        question = questionnaire.question(field)
        actual = answers.get(field)
        if isinstance(expected, dict) and "exists" in expected:
          reasons.append(f"{question['text']} was answered")
        elif isinstance(expected, dict):
          operator = {"lte": "at or below", "lt": "below", "gte": "at or above", "gt": "above"}
          name, target = next(iter(expected.items()))
          reasons.append(f"{question['text']} was {actual}, which is {operator[name]} {target}")
        else:
          reasons.append(f"{question['text']} was {display_value(actual)}")
      return rule["outcome"], " and ".join(reasons) + "."
  return "STP", "No underwriting rule was triggered, so the default outcome applies."


def state(session_id: str) -> dict[str, Any]:
  session = sessions[session_id]
  question = questionnaire.next_question(session["answers"])
  outcome, reason = decision(session["answers"]) if question is None else (None, None)
  return {
    "session_id": session_id,
    "messages": session["messages"],
    "question": question_payload(question),
    "answered": len(session["answers"]),
    "total": len(questionnaire.questions),
    "complete": question is None,
    "outcome": outcome,
    "reason": reason,
  }


def new_session() -> str:
  session_id = secrets.token_urlsafe(16)
  sessions[session_id] = {
    "answers": {},
    "messages": [{"role": "assistant", "content": "Welcome. I will guide you through a short health questionnaire."}],
  }
  return session_id


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
  return FileResponse(ROOT / "static" / "index.html")


@app.post("/api/sessions")
def create_session() -> dict[str, Any]:
  return state(new_session())


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  return state(session_id)


@app.post("/api/sessions/{session_id}/answers")
def submit_answer(session_id: str, request: AnswerRequest) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  session = sessions[session_id]
  current = questionnaire.next_question(session["answers"])
  if current is None:
    raise HTTPException(status_code=409, detail="This questionnaire is already complete")
  if request.question_id != current["id"]:
    raise HTTPException(status_code=409, detail="This question is no longer current")
  try:
    parsed_value = questionnaire.answer(request.question_id, request.value)
  except QuestionnaireError as exc:
    raise HTTPException(status_code=422, detail=str(exc)) from exc
  session["answers"][request.question_id] = parsed_value
  session["messages"].append({"role": "user", "content": display_value(parsed_value)})
  next_question = questionnaire.next_question(session["answers"])
  if next_question:
    session["messages"].append({"role": "assistant", "content": next_question["text"]})
  else:
    outcome, reason = decision(session["answers"])
    session["messages"].append({"role": "assistant", "content": f"Your deterministic outcome is {outcome}. Reason: {reason}"})
  return state(session_id)


@app.post("/api/sessions/{session_id}/reset")
def reset_session(session_id: str) -> dict[str, Any]:
  if session_id not in sessions:
    raise HTTPException(status_code=404, detail="Session not found")
  sessions[session_id] = {
    "answers": {},
    "messages": [{"role": "assistant", "content": "Fresh start. I will guide you through a short health questionnaire."}],
  }
  return state(session_id)


def display_value(value: object) -> str:
  if isinstance(value, list):
    return ", ".join(value)
  if isinstance(value, bool):
    return "Yes" if value else "No"
  return str(value)