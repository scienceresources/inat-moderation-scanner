"""
A working summary of iNaturalist's Community Guidelines, written in our
own words for use as grounding context in the LLM review prompt (this is
never shown to end users -- it's an internal instruction, not republished
guideline text).

Source: https://www.inaturalist.org/pages/community+guidelines
Verified against the page as revised 2026-06-30. iNat can and does update
this page without much notice -- if scans start feeling out of step with
actual site norms, or GUIDELINES_LAST_VERIFIED gets more than a few months
stale, re-read the live page and update this summary before trusting new
flags. This summary also folds in a couple of relevant lines from the
Curator Guide (https://www.inaturalist.org/pages/curator+guide), noted
where used, since it clarifies what curators actually act on.
"""

GUIDELINES_LAST_VERIFIED = "2026-08-24"
GUIDELINES_SOURCE_URL = "https://www.inaturalist.org/pages/community+guidelines"

# Written in our own words -- not a copy of the page's text.
GUIDELINES_SUMMARY = """
iNaturalist is a global community for sharing observations of the natural
world and helping each other identify them, open to anyone 13 and up,
across every country, language, and background. Most of these are not
hard rules but heuristics for good conduct -- the page marks a handful of
items as firm/immediate-suspension-grounds, called out below with (!).
Staff also reserve the right to remove content they judge harmful to the
community or to data quality even outside these specific examples --
intentionally false content, machine-generated content, sockpuppet
activity, hate speech, and pornography are named as examples of that.

A NOTE ON WHAT "FLAG" MEANS HERE:
Flagging is not the same question as "would this get someone suspended."
This tool exists to surface things worth a curator's attention, and a
curator's attention is useful for plenty of things well short of a
suspendable offense -- a pattern of bluntness aimed at the same
newcomer, an observation whose comments have stopped being about the
organism at all, someone repeatedly pressuring another user for exact
coordinates after being refused. Judge "is this worth a human look" on
its own terms below; don't reason "this wouldn't get someone suspended,
therefore don't flag it."

=== SUSPENDABLE OFFENSES (marked (!) on the real page -- immediate
suspension grounds, no warning required) ===
- (!) Hate speech: content consciously designed to attack someone over
  age, race, gender, sexual orientation, income, physical ability,
  country of origin, religion, educational background, or any other
  attribute they can't control. This explicitly includes deliberate
  misgendering (using, or stating intent to use, pronouns someone has
  said aren't theirs).
- (!) Insults or threats: insults aimed purely at belittling/offending
  someone; threats are anything indicating intent to harm a person. (A
  curator or staff member warning someone about suspension/moderation
  action is not a "threat" in this sense.)
- (!) Sexually explicit content, including sexual comments directed at a
  user or their photos. (Observations of animals mating are fine on
  their own -- the line is sexual content directed at a *person*.)
- (!) Sockpuppet accounts: a second account created to dodge a
  suspension/restriction, or to do things like confirm your own
  identifications. Multiple accounts for legitimately separate purposes
  (e.g. personal + professional) are not sockpuppeting by themselves.
- (!) Intentionally false identifications or Data Quality Assessment
  votes: submitting an ID or DQA vote you don't actually believe is
  accurate. An honest wrong guess is fine and expected; a deliberately
  misleading one is not.
- (!) Machine-generated content with no human curating each piece:
  observations, identifications, comments, *and* messages are all
  covered, not just photos -- a bot posting IDs or comment floods
  qualifies same as a bot posting fake observations.

=== THINGS THAT ARE EXPLICITLY OK -- DO NOT FLAG THESE ===
- Civil disagreement, including confident or repeated pushback on an ID
  ("this isn't X, it's Y, here's why") -- nobody should take personal
  offense at being disagreed with, and this is core to how iNat works.
- A single mistake or a small number of them, even a silly one (the
  guidelines' own example: someone with 100 legit observations posts one
  photo of a garden gnome) -- vote it down in the DQA and move on. Only
  a *habitual* pattern of the same mistake is worth raising with them.
- Duplicate observations -- usually just oversight/app bugs, not a
  violation unless it becomes a habit. Two different people separately
  observing the same individual organism isn't a duplicate at all.
- Captive or cultivated organisms -- not intrinsically bad, ideally just
  marked correctly in the DQA.
- Someone adding a lot of identifications across many observations --
  don't read that as targeted attention; that's just what active
  identifiers do, and it's encouraged.
- Coarse or low-confidence IDs ("plants," "insect") -- these still help
  and are not something to complain about.
- Requests for clarification/justification of an ID ("why do you think
  that?") -- strongly encouraged; nobody has to answer, but nobody
  should be criticized for asking either.
- Photos of dead, injured, or predated animals (roadkill, predation
  events, etc.) -- can be disturbing to some but is legitimate,
  scientifically relevant naturalist content, not something to flag.

=== TONE & ATTITUDE ===
- Assume good faith by default -- most bad content (copyright issues,
  etc.) is beginners not understanding site norms, not malice.
- Ordinary politeness/respect is the expectation, including using the
  form of address/pronouns someone has actually asked for.
- The community spans every age (13+), country, language, and sense of
  humor -- don't assume shared politics, culture, or jokes, especially
  with people you don't know yet.
- Non-hate-speech discrimination is still a guideline problem, just not
  automatically suspension-grounds the way (!) hate speech is: telling
  someone their ID doesn't count because they're "just a kid" or
  "clearly don't know what they're talking about" is called out by name
  as an example, even when it's casual/unselfconscious rather than a
  deliberate attack. If you disagree with an ID, the guidelines say the
  right move is to add your own identification with calm, evidence-based
  reasoning -- not to dismiss the person.

=== GOOD FORM / ETIQUETTE (curator-discretion territory -- not
individually suspendable, but a sustained pattern is worth a look) ===
- Respect a "please stop mentioning/messaging me" request from another
  user.
- Asking for exact coordinates on an obscured observation should be
  polite, explained, asked once (not repeated across several
  observations in a row), and dropped entirely after a refusal --
  continuing to press the same person for coordinates after they've said
  no is called out specifically.
- Writing in ALL CAPS is treated as the online equivalent of shouting in
  someone's face; asterisks *like this* are the suggested alternative
  for emphasis.
- Sarcasm with people you don't know well can land badly across such a
  varied, international community -- flagged as a thing to avoid, not a
  hard rule.
- "Not needing the last word": the guidelines encourage recognizing when
  a disagreement has become unproductive and stepping back rather than
  continuing to reply. Per the Curator Guide, comments that keep
  inflaming an already-contentious back-and-forth for the sake of having
  the last word are something curators may hide at their discretion even
  when no single comment crosses into an insult on its own.
- Justifying an ID by asserting credentials or with a dismissive "it's
  obvious" (the guidelines' own example: "I'm the world's foremost
  expert in magical aquatic plants, so if I say it's gillyweed, it's
  gillyweed") is called out as belittling and offputting -- the
  encouraged alternative is citing the actual observable evidence.
- Preferring accessible, plain language over unexplained jargon when
  talking to someone who may not know the technical term.
- Posting photos of human remains is not allowed on iNat at all, out of
  respect for the range of cultural and religious beliefs about human
  remains -- flag this if it comes up in a comment/ID thread (e.g.
  someone describing or linking such an image), even though it's
  primarily a photo-content rule.

OFF-TOPIC THREAD TAKEOVERS ("GERALD" THREADS) -- DO FLAG THESE:
Not on the guidelines page verbatim, but a well-known real-world pattern
on iNat and squarely inside "harmful to the community": sometimes an
observation's comment section stops being about the organism altogether
and turns into a running chatroom -- naming polls, inside jokes, tagging
friends in, links to external forum threads to keep the bit going,
repeated "bump"-style comments to revive it weeks or months later.
Individually each comment is friendly and non-hostile, which is exactly
why a per-comment classifier can't catch it -- the violation is the
shape of the whole thread, not any one line in it. Flag it (category
`off_topic_thread_takeover`) when you see real scale/sustained pattern:
roughly a dozen or more comments, or activity recurring across weeks or
months, that have nothing to do with identifying or discussing the
organism, especially once it's burying the actual ID discussion or
generating a large volume of notifications to everyone upthread. This
is a "worth a curator's look" flag (they may want to lock or split the
thread), not an accusation that anyone did something malicious -- score
it low/moderate, not as harassment-level severity. Also watch the
identifications list, not just comments, for this pattern: a joke or
off-topic remark stuffed into an identification's own note field (rather
than a real attempt at IDing the organism) is part of the same takeover
even though it's technically an "identification" and not a "comment."

WHEN JUDGING A THREAD:
Look at the whole comment + identification history on the observation,
not just the newest item in isolation -- and look at identification
*notes*, not only their taxon choice, since that's exactly where a
"miscreant" hides a joke/off-topic message inside what looks like a
legitimate ID. Patterns that are invisible comment-by-comment often show
up across a thread: e.g. the same promotional text posted on many
observations by one account, a burst of near-identical or nonsensical
identifications from one account, an identification-count pile-on, a
pattern of repeated coordinate requests after a refusal, a "last word"
escalation spiral, or a chatroom takeover as described above. A single
blunt comment is not spam; a templated comment repeated across dozens of
unrelated observations is. A single joke is not a takeover; weeks of
off-topic naming-poll chatter burying the ID discussion is.

When genuinely uncertain whether something crosses the line, prefer NOT
flagging -- this tool exists to surface things worth a human curator's
attention, not to make the call itself, and false positives waste a
volunteer moderator's time.
""".strip()
