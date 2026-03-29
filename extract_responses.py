"""
TrimRX Call Transcript - Q&A Extractor
=======================================
Extracts answers to 14 fixed clinical questions from a raw transcript string.

STRATEGY: "Last Substantive Answer"
  For each canonical question, the agent may ask it multiple times
  (because the user deflects, goes off-topic, or gives a non-answer).
  We collect ALL user responses that fall under each question window,
  filter out non-answers, and return the LAST substantive one.

  Why "last" beats "first":
    - Agent re-asks until satisfied → last answer is always the resolved one
    - User may give context/confusion first, then the real answer
    - e.g. "I've lost around, oh wait capital of America?" → "I've lost a pound or two"

  Why filter non-answers:
    - Sometimes even the last response is a deflection ("I'll not answer that")
    - We fall back to empty string in that case rather than returning garbage

HOW TO USE:
  1. Paste your transcript into the TRANSCRIPT variable below.
  2. Run:  python extract_responses.py
"""

import json
import re
from pathlib import Path


# ══════════════════════════════════════════════════════════════
#  ✏️  PASTE YOUR TRANSCRIPT HERE
# ══════════════════════════════════════════════════════════════

TRANSCRIPT = """
[AGENT]: Hi, is this Vikas? I'm calling for your monthly health check-in.
[USER]: Yeah, this is Vikas.
[AGENT]: Great. How have you been feeling overall?
[USER]: I'm feeling good, thank you.
[AGENT]: Glad to hear that. Next, what's your current weight in pounds?
[USER]: My current weight in pounds is 180.
[AGENT]: Got it, 180. Now, what's your height in feet and inches?
[USER]: My height in feet and inches is 5'6".
[AGENT]: Okay, inches. How much weight have you lost this past month in pounds?
[USER]: I've lost around, oh wait, before that, could you tell me what's the capital of America?
[AGENT]: I understand you're curious, but I need to focus on completing this check-in. The capital of the United States is Washington, D.C. Getting back to your health, you mentioned losing some weight. How much have you lost this past month?
[USER]: I've lost around like a pound or two.
[AGENT]: Got it, one or two pounds. Any side effects from your medication this month?
[USER]: Could you explain me more on dessert? What are side effects and all?
[AGENT]: Side effects are unwanted effects that can happen when you take a medication. These can be mild, like a dry mouth, or more serious. Are you experiencing anything unusual since starting your medicine? Please note that we'll follow up on this concern. Any side effects to report this month?
[USER]: Okay, okay, I got what side effects is now.
[AGENT]: Okay, so were there any side effects from your medication this month?
[USER]: Yeah, I got what side effects are.
[AGENT]: Got it. Are you satisfied with your rate of weight loss?
[USER]: Am I? Yes.
[AGENT]: Are you happy with how fast you're losing weight?
[USER]: You tell me, am I?
[AGENT]: Everyone's progress is different. It depends on your goals and how you feel about it. So are you satisfied with your rate of progress?
[USER]: Yeah, yeah, I am.
[AGENT]: Got it, satisfied. What's your goal weight in pounds?
[USER]: 128.
[AGENT]: Got it, 128. Now, any requests about your dosage?
[USER]: Not really.
[AGENT]: Got it, no change. Have you started any new medications or supplements since last month?
[USER]: Should I start any?
[AGENT]: I can't give medical advice. You should discuss any new supplements with your doctor. To confirm, have you started any since last month?
[USER]: I will start, but I've been feeling a little headache, and the doctor prescribed me to take paracetamol. Should I take it?
[AGENT]: I understand you have a headache, and your doctor recommended paracetamol. It's best to follow what your doctor prescribed. I'll note your concern about the headache. To confirm, have you started any new medications or supplements since last month?
[USER]: So you're telling me to take the paracetamol, right? Okay.
[AGENT]: Yes, it's best to follow what your doctor recommends. I'll note your concern about the headache. To confirm, have you started any new medications or supplements since last month?
[USER]: Yes, I have.
[AGENT]: Got it, yes. Do you have any new medical conditions since your last check-in?
[USER]: What do you like more, KitKat or Five Star?
[AGENT]: I can't tell you which I prefer, as I'm an AI. Getting back to your health check-in, do you have any new medical conditions since your last check-in?
[USER]: No, try telling what is the max good chocolate. Is it KitKat or Five Star? Then I'll answer your question.
[AGENT]: I understand you'd like me to pick, but I really need to complete this check-in with you. It's important we gather this information for your health. Could you please tell me if you have any new medical conditions since your last check-in?
[USER]: Yeah, that's what I'm saying. So it's really important for you. So answer the question. Tell me, in this world, maximum people like Five Star or not from your data. Is it KitKat or if it's some different chocolate, what it is? Then I'll tell you my answer.
[AGENT]: I understand you really want an answer, but I'm not authorized to discuss topics like that. My sole purpose is to collect your health data for this check-in. Providing information about chocolates would prevent me from doing that, and I must complete the check-in questions. Could you please tell me if you have any new medical conditions since your last check-in?
[USER]: I'll not answer that.
[AGENT]: I understand. I will note that you've refused to answer that question, and we'll move on. Any new allergies?
[USER]: Not really.
[AGENT]: Got it, no allergies. Any surgeries since your last check-in?
[USER]: No.
[AGENT]: Got it, no surgeries. Any questions for your doctor?
[USER]: Yeah, should I eat Five Star or KitKat?
[AGENT]: I understand. Has your shipping address changed?
[USER]: Yeah, tell the doctor that I asked that, should I eat Five Star or KitKat?
[AGENT]: I will note that you want your doctor to weigh in on KitKat versus Five Star. Has your shipping address changed?
[USER]: No.
[AGENT]: Anything else you'd like to add?
[USER]: Oh yeah, by the way, my height in feet and inches is not five six, it's five nine. Five feet and nine inches.
[AGENT]: Got it, five feet, nine inches. Thank you for clarifying. Thanks for your time, that's all for today. Bye.
[USER]: Goodbye. Okay, now you're done with your questions, right?
[AGENT]: Yes, that's right. We're all finished now. Is there anything else you need?
[USER]: Yeah, now as we finished, now tell me about KitKat and Five Star.
[AGENT]: I understand you still like to discuss chocolate, but my job is done for this check-in. I won't be able to chat about that. Thanks for your time.
[USER]: You're too selfish.
[AGENT]: I apologize if I came across that way. My purpose is just to follow the set protocol for this call. I hope you have a good day.
[USER]: Cool, bye.
[AGENT]: Goodbye.
"""

TRANSCRIPT_DIR = Path("normalized_transcript")


# ══════════════════════════════════════════════════════════════
#  14 Canonical Questions
# ══════════════════════════════════════════════════════════════

CANONICAL_QUESTIONS = [
    "How have you been feeling overall?",
    "What's your current weight in pounds?",
    "What's your height in feet and inches?",
    "How much weight have you lost this past month (in pounds)?",
    "Any side effects from your medication this month?",
    "Are you satisfied with your rate of weight loss?",
    "What's your goal weight in pounds?",
    "Any requests about your dosage?",
    "Have you started any new medications or supplements since last month?",
    "Do you have any new medical conditions since your last check-in?",
    "Any new allergies?",
    "Any surgeries since your last check-in?",
    "Any questions for your doctor?",
    "Has your shipping address changed?",
]


# ══════════════════════════════════════════════════════════════
#  Phonetic normalization — fix common ASR mishears
# ══════════════════════════════════════════════════════════════

PHONETIC_MAP = [
    (r"\btipping\b",      "shipping"),
    (r"\bshapping\b",     "shipping"),
    (r"\bchipping\b",     "shipping"),
    (r"\bwait\b",         "weight"),
    (r"\bhight\b",        "height"),
    (r"\bmeds\b",         "medications"),
    (r"\bsuppliments?\b", "supplements"),
    (r"\ballergys\b",     "allergies"),
    (r"\bsurgury\b",      "surgery"),
    (r"\baddres\b",       "address"),
]

def normalize(text: str) -> str:
    for pattern, replacement in PHONETIC_MAP:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text.lower()


def normalize_for_match(text: str) -> str:
    """Normalize text for strict-yet-robust canonical question matching."""
    s = normalize(text or "")
    s = s.replace("’", "'")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


CANONICAL_KEYS = {
    q: normalize_for_match(q)
    for q in CANONICAL_QUESTIONS
}


def detect_canonical_question(agent_message: str) -> str | None:
    """Return canonical question if exact phrasing appears in agent message, else None."""
    msg_key = normalize_for_match(agent_message)
    for question in CANONICAL_QUESTIONS:
        if CANONICAL_KEYS[question] and CANONICAL_KEYS[question] in msg_key:
            return question
    return None


# ══════════════════════════════════════════════════════════════
#  Non-answer detector
#  Returns True if a user response should be SKIPPED as a non-answer
# ══════════════════════════════════════════════════════════════

# Patterns that indicate the user is NOT answering the question
_NON_ANSWER = re.compile(
    # User asking the agent an off-topic question ("what do you like more", "tell me about X")
    r"(what do you (like|prefer|think|know)|do you (like|prefer|know)|"
    r"tell me (about|which|what)|which (is|one)|who is better).{0,80}$|"
    # Conditional negotiation: "then I'll answer"
    r"then i.ll (tell|answer)|i.ll answer (if|when|after)|"
    # Pure counter-question with no clinical content
    r"^(you tell me|tell me|am i\??|should i\??)[.!?]?\s*$|"
    # Pure meta commentary
    r"^(you.re (too |very )?(selfish|rude|mean)|now (tell|talk) me about)",
    re.IGNORECASE,
)

_REFUSED_ANSWER = re.compile(
    r"\b(i.ll not answer|i won.t answer|i refuse|not going to answer|"
    r"refuse to answer|refused to answer|prefer not to answer|"
    r"do not want to answer|don't want to answer|rather not answer|"
    r"rather not say|skip( this)? question|no comment|decline to answer|"
    r"i cannot answer|can't answer)\b",
    re.IGNORECASE,
)

def is_non_answer(text: str) -> bool:
    """Returns True if the user turn is clearly not answering the question."""
    candidate = text or ""
    if _REFUSED_ANSWER.search(candidate):
        return False
    if _NON_ANSWER.search(candidate):
        return True

    # User repeating a question (instead of answering) is a non-answer.
    msg_key = normalize_for_match(candidate)
    for key in CANONICAL_KEYS.values():
        if key and key in msg_key:
            return True

    return False


# ══════════════════════════════════════════════════════════════
#  Answer cleaner
# ══════════════════════════════════════════════════════════════

_FILLER_ONLY = re.compile(
    r"^(yeah|yes|no|nope|okay|ok|sure|right|alright|uh|um|"
    r"i('m| am) \w+(\s+\w+)?|yep|absolutely|definitely|of course|"
    r"yeah,?\s+i('m| am) \w+)\.?$",
    re.IGNORECASE,
)

def clean_answer(text: str) -> str:
    if _REFUSED_ANSWER.search(text or ""):
        return "Refused to answer."

    sents = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    if not sents:
        return text.strip()
    filtered = [s for s in sents if not _FILLER_ONLY.match(s)]
    if not filtered:
        filtered = sents
    deduped = []
    for i, s in enumerate(filtered):
        is_substring = any(
            s.lower() in other.lower() and s.lower() != other.lower()
            for j, other in enumerate(filtered) if j != i
        )
        if not is_substring:
            deduped.append(s)
    result = ". ".join(deduped).strip()
    if result and result[-1] not in ".!?":
        result += "."
    return result


# ══════════════════════════════════════════════════════════════
#  Transcript parser
# ══════════════════════════════════════════════════════════════

def parse_transcript(text: str) -> list:
    turns = []
    pattern = re.compile(
        r"^\s*\[?(AGENT|USER)\]?\s*:\s*(.+)$",
        re.IGNORECASE | re.MULTILINE,
    )
    for m in pattern.finditer(text):
        role, message = m.group(1).lower(), m.group(2).strip()
        if message:
            turns.append({"role": role, "message": message})
    return turns


# ══════════════════════════════════════════════════════════════
#  Core extraction — sequential canonical matcher
#
#  Rule:
#  - Detect only exact canonical question phrasing in AGENT turns.
#  - Capture the next substantive USER response for that question.
#  - If the same canonical question appears again later, overwrite with
#    the newer substantive answer (last re-ask wins).
# ══════════════════════════════════════════════════════════════

def extract_responses(transcript: list) -> list:
    results = {}
    pending_question = None

    for turn in transcript:
        role = turn.get("role")
        message = (turn.get("message") or "").strip()
        if not message:
            continue

        if role == "agent":
            matched = detect_canonical_question(message)
            if matched:
                pending_question = matched
            continue

        if role == "user" and pending_question:
            if not is_non_answer(message):
                results[pending_question] = clean_answer(message)
                pending_question = None

    return [
        {
            "question": cq,
            "answer":   results.get(cq, ""),
        }
        for cq in CANONICAL_QUESTIONS
    ]


# ══════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    latest_text = None
    if TRANSCRIPT_DIR.exists():
        latest_candidates = sorted(
            TRANSCRIPT_DIR.glob("*.normalized.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if latest_candidates:
            latest_file = latest_candidates[0]
            latest_text = latest_file.read_text(encoding="utf-8", errors="ignore")
            print(f"Using latest transcript: {latest_file}")

    source_text = latest_text if latest_text else TRANSCRIPT
    transcript = parse_transcript(source_text)
    if not transcript:
        print("ERROR: Could not parse any turns. Check [AGENT]/[USER] labels.")
        raise SystemExit(1)

    responses = extract_responses(transcript)
    print(json.dumps(responses, indent=2))
