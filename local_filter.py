"""
Fast, offline last-resort filter using the `better-profanity` package's
maintained wordlist (https://pypi.org/project/better-profanity/). This
exists as a backup for when the local toxicity model (local_model.py) is
unavailable (failed to download/load) -- deliberately not hand-rolled
here, since an actively maintained community list beats a one-off list
written for this repo. It only catches exact profanity/slur matches, not
context-dependent insults or harassment -- that's what the model is for.
"""

from better_profanity import profanity

profanity.load_censor_words()


def local_flag(text):
    """Returns True if the text trips the local wordlist filter."""
    if not text or not text.strip():
        return False
    return profanity.contains_profanity(text)
