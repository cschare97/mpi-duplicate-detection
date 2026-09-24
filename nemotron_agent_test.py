"""
nemotron_agent_test.py

Checks that a local model served by Ollama can call the check_duplicate
tool (the same function exposed by mpi_dedup_server.py) and pass the record
values through correctly.

What this measures, per test prompt:
  1. Did the model call the tool at all?
  2. Were the 8 arguments it sent exactly the values written in the prompt?
  3. Does the tool result for the model's arguments equal a direct call to
     match_patients with the true values?

What it does NOT measure: whether the model judges duplicates well. The
score and verdict come from patient_match.py, not from the model. The model
only chooses to call the tool and relays the arguments and the answer.

All records below are invented. No real patient data.

Run:
    python3 nemotron_agent_test.py
Optional: OLLAMA_MODEL=<tag from `ollama list`> python3 nemotron_agent_test.py
"""

import json
import os

import ollama
from patient_match import match_patients

MODEL = os.environ.get("OLLAMA_MODEL", "nemotron-3.5-lightning")

FIELDS = ["last_name", "first_name", "dob", "ssn"]
ARG_NAMES = [f"{f}_{s}" for s in ("a", "b") for f in FIELDS]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_duplicate",
            "description": "Compare two patient records and score how likely they are the same person.",
            "parameters": {
                "type": "object",
                "properties": {name: {"type": "string"} for name in ARG_NAMES},
                "required": ARG_NAMES,
            },
        },
    }
]

# Invented test pairs (label, record A, record B).
CASES = [
    (
        "near-match last name, different SSN",
        {"last_name": "Whitlock", "first_name": "Maren", "dob": "Feb. 17, 1979", "ssn": "999-11-2233"},
        {"last_name": "Whitlok", "first_name": "Maren", "dob": "Feb. 17, 1979", "ssn": "999-44-5566"},
    ),
    (
        "nickname, same SSN",
        {"last_name": "Okafor", "first_name": "Daniel", "dob": "Aug. 3, 1990", "ssn": "999-77-1234"},
        {"last_name": "Okafor", "first_name": "Dan", "dob": "Aug. 3, 1990", "ssn": "999-77-1234"},
    ),
    (
        "SSN digit transposition",
        {"last_name": "Nandakumar", "first_name": "Priya", "dob": "Jun. 9, 2001", "ssn": "999-52-6781"},
        {"last_name": "Nandakumar", "first_name": "Priya", "dob": "Jun. 9, 2001", "ssn": "999-52-6718"},
    ),
    (
        "clearly different people",
        {"last_name": "Grant", "first_name": "Hollis", "dob": "Mar. 30, 1965", "ssn": "999-20-4471"},
        {"last_name": "Brandt", "first_name": "Tessa", "dob": "Nov. 12, 2010", "ssn": "999-83-9052"},
    ),
]


def build_prompt(a: dict, b: dict) -> str:
    def fmt(r):
        return f"{r['last_name']}, {r['first_name']}, DOB {r['dob']}, SSN {r['ssn']}"
    return f"Are these the same patient? Record A: {fmt(a)}. Record B: {fmt(b)}."


def expected_args(a: dict, b: dict) -> dict:
    out = {f"{f}_a": a[f] for f in FIELDS}
    out.update({f"{f}_b": b[f] for f in FIELDS})
    return out


def call_check_duplicate(**kw) -> dict:
    rec_a = {f: kw[f"{f}_a"] for f in FIELDS}
    rec_b = {f: kw[f"{f}_b"] for f in FIELDS}
    r = match_patients(rec_a, rec_b)
    return {"score": r.score, "verdict": r.verdict, "reasons": r.reasons}


def run_case(label: str, a: dict, b: dict) -> dict:
    prompt = build_prompt(a, b)
    outcome = {"label": label, "called": False, "args_exact": False, "result_matches_direct": False}
    direct = call_check_duplicate(**expected_args(a, b))
    outcome["direct"] = direct

    messages = [{"role": "user", "content": prompt}]
    response = ollama.chat(model=MODEL, messages=messages, tools=TOOLS)
    msg = response["message"]

    calls = msg.get("tool_calls") or []
    if not calls:
        outcome["note"] = f"no tool call; model said: {msg.get('content')!r}"
        return outcome

    call = calls[0]
    if call["function"]["name"] != "check_duplicate":
        outcome["note"] = f"unexpected tool: {call['function']['name']}"
        return outcome

    args = call["function"]["arguments"]
    if isinstance(args, str):
        args = json.loads(args)
    args = {k: str(v).strip() for k, v in dict(args).items()}
    outcome["called"] = True
    outcome["model_args"] = args
    outcome["args_exact"] = args == expected_args(a, b)

    try:
        result = call_check_duplicate(**args)
    except KeyError as e:
        outcome["note"] = f"model omitted argument {e}"
        return outcome

    outcome["tool_result"] = result
    outcome["result_matches_direct"] = result == direct

    messages.append(msg)
    messages.append({"role": "tool", "content": json.dumps(result)})
    followup = ollama.chat(model=MODEL, messages=messages)
    outcome["final_answer"] = followup["message"]["content"]
    return outcome


def main():
    print(f"model: {MODEL}\n")
    results = []
    for label, a, b in CASES:
        print(f"--- {label}")
        r = run_case(label, a, b)
        results.append(r)
        print(f"  tool called:            {r['called']}")
        print(f"  args exact:             {r['args_exact']}")
        print(f"  result == direct call:  {r['result_matches_direct']}")
        print(f"  direct call result:     {r['direct']['verdict']} (score {r['direct']['score']})")
        if "note" in r:
            print(f"  note: {r['note']}")
        if not r["args_exact"] and "model_args" in r:
            print(f"  model args: {r['model_args']}")
        if "final_answer" in r:
            print(f"  model's final answer: {r['final_answer']}")
        print()

    n = len(results)
    print("=" * 60)
    print(f"tool called:             {sum(r['called'] for r in results)}/{n}")
    print(f"args exact:              {sum(r['args_exact'] for r in results)}/{n}")
    print(f"result == direct call:   {sum(r['result_matches_direct'] for r in results)}/{n}")
    print("Model output varies between runs; report the run you actually did.")


if __name__ == "__main__":
    main()
