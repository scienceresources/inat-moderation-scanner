"""
Local, offline toxicity classifier -- runs entirely inside the GitHub
Actions job, no API key, no account, no billing, no third-party service.

Uses unitary/unbiased-toxic-roberta
(https://huggingface.co/unitary/unbiased-toxic-roberta), a RoBERTa model
fine-tuned on Jigsaw's "Unintended Bias in Toxicity Classification"
dataset. That dataset -- and this model -- exist specifically to fix a
failure mode of the *original* Jigsaw Toxic Comment dataset (and models
trained on it, e.g. martin-ha/toxic-comment-model, which is what this
scanner used to run): those models learn to associate ordinary mentions
of identity/descriptor terms (male, female, black, white, etc.) with
toxicity, because in their training data those words correlated with
actual harassment. On a nature-ID site where comments constantly describe
animal sex and coloring ("the male has a black and white striped
abdomen"), that bias produces a wall of false positives. This model was
trained explicitly to not do that.

It's a MULTI-LABEL model -- toxicity, severe_toxicity, obscene, threat,
insult, identity_attack, sexual_explicit are independent probabilities,
not a single softmax pair -- so the pipeline is built with
function_to_apply="sigmoid" below. Using the default (softmax) would
silently produce nonsense scores.

Honest limitation: still an imperfect classifier, and like most toxicity
classifiers it remains more sensitive to profanity used as-is than to
context/tone (self-deprecating "damn, I'm dumb" can still score higher
than it should). Treat every flag as "worth a human look", never as a
verdict.

First run downloads the model (~500MB) from Hugging Face and caches it
locally; the GitHub Actions workflow caches that download directory
between runs so it's not re-fetched every 6 hours.
"""

import os

def _env_or_default(name, default, cast=str):
    """Falls back to default for both a missing env var and an empty-string
    one -- the latter is what an unset GitHub Actions repo variable becomes
    (${{ vars.X }} -> "") when passed through to env:, not a truly absent
    variable."""
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return cast(val)


MODEL_NAME = _env_or_default("TOXICITY_MODEL", "unitary/unbiased-toxic-roberta")
TOXIC_THRESHOLD = _env_or_default("TOXIC_THRESHOLD", 0.65, float)

# Labels this model scores independently (sigmoid, not softmax) that are
# worth flagging on their own -- e.g. a comment could score low on
# "toxicity" overall but high specifically on "identity_attack". Each
# gets the same TOXIC_THRESHOLD unless overridden via env vars below.
FLAGGED_LABELS = ["toxicity", "severe_toxicity", "obscene", "threat", "insult", "identity_attack"]

_pipeline = None
_load_failed = False


def _get_pipeline():
    """Lazily load and cache the model pipeline. Returns None (and sets
    _load_failed) if the model can't be loaded at all -- e.g. no network
    access to Hugging Face on a fresh runner, disk issues, etc. Callers
    should treat None as "fall back to the local wordlist filter"."""
    global _pipeline, _load_failed
    if _pipeline is not None or _load_failed:
        return _pipeline

    try:
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
            TextClassificationPipeline,
        )

        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
        _pipeline = TextClassificationPipeline(
            model=model, tokenizer=tokenizer, top_k=None, truncation=True,
            function_to_apply="sigmoid",  # multi-label: independent probabilities, not softmax
        )
    except Exception as e:  # noqa: BLE001 -- any load failure -> fall back
        print(f"WARNING: could not load local toxicity model ({e}). "
              "Falling back to the wordlist filter for this run.")
        _load_failed = True
        _pipeline = None

    return _pipeline


def enabled():
    """True if the model is available (or hasn't been tried yet)."""
    return not _load_failed


def score(text):
    """
    Returns a dict like {"toxic": 0.87, "non_toxic": 0.13} on success, or
    None if the model isn't available or the text is empty -- callers
    should fall back to the local wordlist filter in that case.
    """
    if not text or not text.strip():
        return None

    pipe = _get_pipeline()
    if pipe is None:
        return None

    try:
        # top_k=None returns scores for every label instead of just the
        # top one, e.g. [[{"label": "toxic", "score": 0.9}, {"label":
        # "non_toxic", "score": 0.1}]]
        result = pipe(text[:2000])[0]
        return {item["label"].lower(): item["score"] for item in result}
    except Exception as e:  # noqa: BLE001 -- a bad single comment shouldn't kill the run
        print(f"  WARNING: local model failed to score a comment ({e}); "
              "falling back to wordlist for this one.")
        return None


def flagged_reasons(scores):
    """Given a scores dict, return a list like ['toxicity=0.87',
    'insult=0.71'] for every FLAGGED_LABELS entry over threshold, or []
    if none crossed. (Plural -- unlike the old single toxic/non_toxic
    model, this one scores several labels independently, so a comment
    can be flagged for identity_attack without a high overall toxicity
    score, or vice versa.)"""
    if not scores:
        return []
    reasons = []
    for label in FLAGGED_LABELS:
        val = scores.get(label)
        if val is not None and val >= TOXIC_THRESHOLD:
            reasons.append(f"{label}={val:.2f}")
    return reasons