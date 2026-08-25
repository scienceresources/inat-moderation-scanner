"""
Full-thread guideline review: given everything on one observation (all
comments + all identifications, not just what's new), ask an LLM to judge
it against iNat's actual community guidelines and flag anything worth a
human curator's attention -- spam patterns, harassment, bad-faith IDs,
sockpuppet-looking behavior -- that a single isolated comment can't show.

This is deliberately separate from, and additional to, the fast local
toxicity model in local_model.py: that one still screens every new
comment for free and instantly. This one is slower and budget-limited
(see rate_budget.py) but reasons over the whole thread against real
guideline text instead of scoring one sentence in isolation.
"""

from guidelines import GUIDELINES_SUMMARY

VALID_CATEGORIES = {
    "spam_promotional",
    "hate_or_harassment",
    "threats_or_sexual_content",
    "sockpuppet_or_vote_manipulation",
    "deliberately_false_id",
    "machine_generated_spam",
    "doxxing",
    "off_topic_thread_takeover",
    "discriminatory_conduct",
    "other_guideline_violation",
}

SYSTEM_PROMPT = f"""You are a triage assistant for iNaturalist curators. You review the \
full comment and identification history on one observation and flag ONLY \
things that would actually be worth a human moderator's attention under \
iNat's real community guidelines. You never take action yourself -- you \
only flag for human review.

{GUIDELINES_SUMMARY}

Respond with ONLY a JSON object, no other text, matching exactly this shape:
{{
  "flag": true or false,
  "categories": [list of zero or more of: {sorted(VALID_CATEGORIES)}],
  "flagged_user": "<the specific username most responsible, or null if it's not about one specific user>",
  "reasoning": "<one or two plain sentences on why, written for a curator who hasn't read the thread yet>",
  "score": <float 0.0-1.0, roughly "how urgently should a curator look at this" -- \
weigh both confidence and severity; a clear-cut but low-stakes finding (e.g. an \
off-topic thread takeover) should land low/moderate, not near 0, since it's still \
worth queuing for a look, just not an urgent one. 0 means not concerning at all>
}}

If nothing in the thread crosses the line described above, return \
flag: false, categories: [], score close to 0. Do not flag ordinary \
identification disagreement, bluntness, or beginners being wrong."""


def _format_thread(snapshot):
    lines = [
        f"Observation #{snapshot['observation_id']} ({snapshot['species_guess']}), "
        f"observed/posted by {snapshot['observation_owner'] or 'unknown'}.",
        "",
        "COMMENTS (chronological):",
    ]
    if not snapshot["comments"]:
        lines.append("  (none)")
    for c in snapshot["comments"]:
        lines.append(f"  [{c['created_at']}] {c['user']}: {c['body']}")

    lines.append("")
    lines.append("IDENTIFICATIONS (chronological):")
    if not snapshot["identifications"]:
        lines.append("  (none)")
    for i in snapshot["identifications"]:
        note = f' -- "{i["body"]}"' if i["body"] else ""
        cat = f" [{i['category']}]" if i["category"] else ""
        lines.append(f"  [{i['created_at']}] {i['user']} identified as {i['taxon']}{cat}{note}")

    return "\n".join(lines)


def review_thread(client, snapshot):
    """Calls the LLM on one observation's full thread. Returns a
    validated verdict dict, or None if the call failed or returned
    something unusable -- callers should re-queue on None, not treat it
    as "not flagged"."""
    user_prompt = _format_thread(snapshot)
    result = client.chat_json(SYSTEM_PROMPT, user_prompt)
    if result is None:
        return None

    try:
        flag = bool(result.get("flag"))
        categories = [c for c in (result.get("categories") or []) if c in VALID_CATEGORIES]
        reasoning = str(result.get("reasoning") or "").strip()
        score = float(result.get("score") or 0.0)
        score = max(0.0, min(1.0, score))
        flagged_user = result.get("flagged_user")
        if not isinstance(flagged_user, str) or not flagged_user.strip():
            flagged_user = None
    except (TypeError, ValueError):
        return None

    # A flag with no categories and near-zero score is almost certainly
    # the model being inconsistent, not a real finding -- treat as clean
    # rather than surfacing an empty-looking backlog item.
    if flag and not categories and score < 0.2:
        flag = False

    return {
        "flag": flag,
        "categories": categories,
        "reasoning": reasoning,
        "score": score,
        "flagged_user": flagged_user,
    }
