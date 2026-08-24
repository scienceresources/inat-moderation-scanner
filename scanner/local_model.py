"""
Local, offline toxicity classifier -- runs entirely inside the GitHub
Actions job, no API key, no account, no billing, no third-party service.

Uses martin-ha/toxic-comment-model (https://huggingface.co/martin-ha/toxic-comment-model),
a DistilBERT model fine-tuned on the Jigsaw Toxic Comment dataset. Unlike a
wordlist, it scores based on the meaning/tone of the whole sentence, so it
can catch harassment and insults that don't contain any profanity or
slurs at all (e.g. "why would you even post this, it's horrible").

Honest limitation: it's still an imperfect classifier. It's strong on
harassment/insults phrased in familiar hostile ways, and comparatively
weak on very dry, understated passive-aggression that carries no hostile
vocabulary -- there's no free, fully-automated tool that reliably gets
that right. Treat every flag as "worth a human look", never as a verdict.

First run downloads the model (~270MB) from Hugging Face and caches it
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


MODEL_NAME = _env_or_default("TOXICITY_MODEL", "martin-ha/toxic-comment-model")
TOXIC_THRESHOLD = _env_or_default("TOXIC_THRESHOLD", 0.65, float)

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
            model=model, tokenizer=tokenizer, top_k=None, truncation=True
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
    """Given a scores dict, return a list like ['toxic=0.87'] if the
    toxic score is over threshold, or [] otherwise."""
    if not scores:
        return []
    toxic_score = scores.get("toxic")
    if toxic_score is not None and toxic_score >= TOXIC_THRESHOLD:
        return [f"toxic={toxic_score:.2f}"]
    return []
