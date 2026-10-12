from __future__ import annotations

from pathlib import Path

import streamlit as st

from questionnaire_engine import Questionnaire, QuestionnaireError


QUESTIONNAIRE_PATH = Path(__file__).with_name("questionnaire.json")
questionnaire = Questionnaire.from_file(QUESTIONNAIRE_PATH)

st.set_page_config(page_title="Health questionnaire", page_icon="+", layout="centered")
st.title("Health questionnaire")
st.caption("A guided, deterministic underwriting questionnaire")


def _display_value(value: object) -> str:
  if isinstance(value, list):
    return ", ".join(value)
  if isinstance(value, bool):
    return "Yes" if value else "No"
  return str(value)


def reset() -> None:
  st.session_state.answers = {}
  st.session_state.messages = [
    {
      "role": "assistant",
      "content": "I will ask a few health questions. Please answer each one to continue.",
    }
  ]
  st.session_state.complete = False
  st.session_state.error = None
  first_question = questionnaire.next_question({})
  if first_question:
    st.session_state.messages.append({"role": "assistant", "content": first_question["text"]})


if "answers" not in st.session_state:
  reset()

with st.sidebar:
  st.subheader("Session")
  answered = len(st.session_state.answers)
  total = len(questionnaire.questions)
  st.progress(min(answered / total, 1.0), text=f"{answered} answered")
  if st.button("Start over", use_container_width=True):
    reset()
    st.rerun()

for message in st.session_state.messages:
  with st.chat_message(message["role"]):
    st.write(message["content"])


if st.session_state.complete:
  st.stop()

current_question = questionnaire.next_question(st.session_state.answers)
if current_question is None:
  result = questionnaire.outcome(st.session_state.answers)
  st.session_state.messages.append(
    {"role": "assistant", "content": f"Based on your answers, the deterministic outcome is **{result}**."}
  )
  st.session_state.complete = True
  st.rerun()

if st.session_state.error:
  st.error(st.session_state.error)

question_type = current_question["type"]
with st.form(f"answer_{current_question['id']}"):
  value = None
  if question_type == "multiselect":
    value = st.multiselect("Select all that apply", current_question["options"])
  elif question_type == "select":
    value = st.radio("Choose one", current_question["options"], index=None)
  elif question_type == "boolean":
    value = st.radio("Choose one", ["Yes", "No"], index=None)
  elif question_type == "number":
    value = st.number_input("Enter a whole number", step=1, format="%d")
  elif question_type == "decimal":
    value = st.number_input("Enter a number", step=0.1, format="%.2f")
  elif question_type == "date":
    value = st.date_input("Choose a date", value=None)
  else:
    value = st.text_input("Your answer")

  submitted = st.form_submit_button("Continue", type="primary", use_container_width=True)

if submitted:
  if question_type == "boolean":
    value = None if value is None else value == "Yes"
  try:
    parsed_value = questionnaire.answer(current_question["id"], value)
  except QuestionnaireError as exc:
    st.session_state.error = str(exc)
    st.rerun()
  else:
    st.session_state.error = None
    st.session_state.answers[current_question["id"]] = parsed_value
    st.session_state.messages.append({"role": "user", "content": _display_value(parsed_value)})
    next_question = questionnaire.next_question(st.session_state.answers)
    if next_question:
      st.session_state.messages.append({"role": "assistant", "content": next_question["text"]})
    st.rerun()