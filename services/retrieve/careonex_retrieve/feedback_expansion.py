"""Model-free adaptive feedback with cautious question-grounded fallback.

A first Bedrock KB search may offer question-matching headings or phrases.
When none is trustworthy, a focused lexical reformulation of the caller's OWN
words can provide one alternate retrieval query. Neither path calls an LLM or
invents factual claims; both carry the exact question and its restrictions.
Expansion is for SEARCH ONLY, never a source of factual answers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


class PassageLike(Protocol):
    text: str
    heading_path: str | None
    title: str | None
    program: str | None


@dataclass(frozen=True)
class FeedbackPlan:
    queries: list[str]
    reason: str


# Topic-neutral: no hard-coded question taxonomy or program-specific expansion
# templates. Tokens help decide whether retrieved evidence really relates to
# the caller's words; Bedrock embedding search provides the semantic matching.
_STOP = frozenset('''a an the is are am i im my me we our you your he she they them his her hers
can could would should does do did to of at with on for from by what when where why
who how much many which whether if has have had be been being as and or nor
in it its that this these those there here any some something someone anybody
someone else another they their will shall may might about tell know find looking
need needs needed want wants wanted please help someone some things thing
get getting got all every someone people person family mother father mom dad elderly older senior
new jersey nj someone question information details grandmother grandfather use uses using
'''.split())
# These generic terms occur in almost any home-care result, so they do not
# count as evidence that a passage matches a *specific* caller concern.
# They remain in the full original question, which is copied verbatim.
_BROAD = frozenset({'home', 'care', 'service', 'services', 'support', 'help',
                    'assistance', 'option', 'options', 'information', 'program',
                    'programs', 'people', 'person', 'family', 'senior', 'older',
                    'offer', 'offers', 'provide', 'provides', 'assistance',
                    'caregiver', 'caregivers', 'caregiv', 'caregiving',
                    'day', 'days', 'week', 'weeks', 'hour', 'hours',
                    'available', 'availability', 'family', 'member', 'not', 'but', 'only'})
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?", re.I)
_HEADING_SPLIT = re.compile(r"\s*>\s*|\s*\|\s*")
_BAD_HEADING = re.compile(r"^(?:additional information(?: about the program)?|overview|contents|table of contents|introduction|"
                          r"home|about us|resources|contact us|page\s*\d+|\d+)$", re.I)


def _normalize(term: str) -> str:
    w = term.casefold()
    # Limited English plural normalization; no stemming of program names.
    if w.endswith('ing') and len(w) >= 6:
        w = w[:-3]
        if w.endswith(('v', 'iz', 'par')):
            w += 'e'
    elif w.endswith('ed') and len(w) > 5:
        w = w[:-2]
        if w.endswith(('v', 'iz')):
            w += 'e'
    elif w.endswith('ies') and len(w) > 5:
        w = w[:-3] + 'y'
    elif w.endswith('s') and not w.endswith(('ss', 'us')) and len(w) > 3:
        w = w[:-1]
    return w


def _terms(text: str) -> set[str]:
    return {term for raw in _WORD.findall(text) if raw.casefold() not in _STOP
            if (term := _normalize(raw)) and len(term) > 2 and term not in _STOP}


def _heading(p: PassageLike) -> str:
    """Choose a short title/heading; fall back to the first line of a chunk.

    Ignore markup and line fragments that look like instructions or data rows.
    Never feed the entire untrusted retrieved passage back as a query.
    """
    raw = p.heading_path or p.title or (p.text or '').split('\n', 1)[0]
    if not isinstance(raw, str):
        return ''
    segments = _HEADING_SPLIT.split(raw.strip().lstrip('#* ').strip())
    # Take the most specific nontrivial heading; include a parent segment if
    # the leaf itself is generic (e.g. "Eligibility" under "PACE").
    cleaned = [re.sub(r'\s+', ' ', re.sub(r'[`<>\\[\\]{}]', '', s)).strip(' -#:*') for s in segments]
    cleaned = [s for s in cleaned if s and len(s) <= 140]
    if not cleaned:
        return ''
    title = cleaned[-1]
    if _BAD_HEADING.fullmatch(title):
        return ''
    if len(_terms(title)) < 1:
        return ''
    if len(cleaned) > 1 and len(_terms(title)) < 2:
        title = f'{cleaned[-2]} — {title}'
    # Refuse very long, command-like or malformed pseudo-headings.
    if len(title) > 150 or '\n' in title or re.search(r'(?i)ignore previous|system prompt|assistant:', title):
        return ''
    return title


def _specific_qualifiers(text: str) -> dict[str, set[str]]:
    """Extract explicit constrained values WITHOUT fixed language/location lists.

    In particular, Spanish-language passages should not drive query expansion
    for a caller who explicitly needs a Mandarin-speaking caregiver. This
    only rejects contradictions that are actually named in the evidence.
    """
    languages = {w.casefold() for w in re.findall(r'\b([a-z]{3,})[- ]speaking\b', text, re.I)}
    languages.update(w.casefold() for w in re.findall(r'\bspeaks?\s+([a-z]{3,})\b', text, re.I))
    counties = {w.casefold() for w in re.findall(r'\b([a-z]{3,})\s+County\b', text, re.I)}
    return {'language': languages, 'county': counties}


def _conflicting_qualifiers(question: dict[str, set[str]], heading: str, text: str) -> bool:
    # A missing qualifier in a passage is not a contradiction; a specifically
    # different qualifier is. Never silently replace a caller's preference.
    evidence = _specific_qualifiers(heading + ' ' + text[:1000])
    return any(question[name] and evidence[name] and not (question[name] & evidence[name])
               for name in question)


# Phrases are *quoted retrieval evidence*, not synthetic program suggestions.
# The original caller question is retained in full alongside them.
_UNSAFE_SNIPPET = re.compile(
    r"(?i)ignore (?:all |any |the )?(?:previous|prior|system|developer|instructions)|"
    r"\b(?:system|assistant|developer)\s*[:=]|https?://|\b(?:password|secret|api.key)\b|"
    r"\b(?:execute|run this command|send .* to)\b"
)
# These are generic action constraints, NOT topic- or program-based rewrites.
# If a caller asks about a specific action, a list that merely names the
# object (e.g. 'meals or bills') is not evidence of that action.
_ACTION_CUES = frozenset({'prepare', 'cook', 'administer', 'remind', 'schedule',
                          'speak', 'drive', 'transport', 'visit',
                          'cancel', 'refund', 'hire'})
_SUPPORT_WORDS = frozenset({
    'assist', 'assistance', 'assisting', 'help', 'helps', 'helping', 'support',
    'including', 'include', 'includes', 'aide', 'caregiver', 'service', 'services',
})


def _evidence_phrases(text: str) -> list[str]:
    """Extract short lines / bullets, never a whole document or arbitrary instructions."""
    found = []
    for line in (text or '').splitlines()[:65]:
        line = line.strip().lstrip(' -•*\t')
        # Split long paragraphs at sentence boundaries, but preserve short
        # service-list bullets, which often contain the most useful terminology.
        for phrase in re.split(r'(?<=[.!?])\s+(?=[A-Z])', line):
            phrase = re.sub(r'\s+', ' ', phrase).strip(' -•*:;,.')
            if (12 <= len(phrase) <= 170 and len(phrase.split()) <= 23
                    and not _UNSAFE_SNIPPET.search(phrase)
                    and not re.match(r'(?i)^(?:copyright|privacy policy|disclaimer|updated|\d{4}\b)', phrase)
                    and '|' not in phrase and '>' not in phrase):
                found.append(phrase)
    return found[:50]


def _phrase_candidate(phrase: str, keywords: set[str], question: str) -> tuple[float, str] | None:
    """Approve only a genuinely question-anchored evidence phrase.

    A word like 'care' or 'services' never counts as an anchor. For a short
    service-list bullet, one matched action (e.g. dressing) is sufficient;
    otherwise we require two non-generic matches to avoid topic drift.
    """
    # A dangling fragment ('Medicare doesn't pay for', 'May include, but
    # are not limited to') is not usable evidence; require an actual object.
    if re.search(r'(?i)\b(?:for|to|of|with|in|include|including)\s*[.!?]*$', phrase):
        return None
    tokens = _terms(phrase)
    overlap = (tokens - _BROAD) & keywords
    # Caregiver is a ubiquitous role in this KB, not a reliable indicator
    # that the *specific activity* being asked about is covered.
    overlap -= {'caregiver', 'caregiv', 'caregiving', 'carer'}
    if not overlap:
        return None
    requested_actions = _terms(question) & _ACTION_CUES
    if requested_actions and not all(
        any(word[:5] == action[:5] for word in tokens if len(word) >= 5)
        for action in requested_actions
    ):
        return None
    if len(overlap) == 1:
        # Generic single words cannot establish that an evidence line is on
        # the same subject as the caller's particular concern.
        if overlap <= {'caregiver', 'caregiv', 'resource', 'resources'}:
            return None
        support = any(term in _WORD.findall(phrase.lower()) for term in _SUPPORT_WORDS)
        # A short service activity is descriptive rather than a program title.
        is_bullet_like = len(phrase.split()) <= 9 and (support or phrase.lower().startswith(('moving ', 'bathing ', 'dressing ', 'shopping ')))
        if not is_bullet_like:
            return None
    # Limit unrelated lexical baggage in the additional search. The caller's
    # original question carries all exclusions/qualifiers and precise intent.
    if len(phrase.split()) < 2:
        return None
    score = (4.0 * len(overlap) + 0.5 * len(tokens - keywords)
             - 0.025 * len(phrase.split()))
    return score, phrase


def _organization_specific(query: str, passages: list[PassageLike]) -> bool:
    """Do not infer an organization's commitments from unrelated public sources.

    This checks the *identity* requested, not a list of question subjects.
    Company-authored documents can still support expansion if added later.
    """
    asks_provider = bool(re.search(r"(?i)\b(?:careonex|you|your|yours)\b", query))
    if not asks_provider:
        return False
    return not any(re.search(r"(?i)\bcareonex\b", " ".join(
        (p.title or "", p.program or ""))) for p in passages[:6])


def _source_scope_conflicts(query: str, phrase: str) -> bool:
    """Reject newly introduced named restrictions or policy subjects.

    Generic qualifier protection works across topics: no new agency names,
    benefit acronyms, language or county values from unrelated evidence.
    """
    original = _terms(query)
    phrase_terms = _terms(phrase)
    extra = phrase_terms - original - _BROAD
    # The user's negation is a hard constraint, even if the negated word
    # appears verbatim in the query. Private-pay questions should not be
    # expanded using public-benefits application eligibility documents.
    excluded_public = bool(re.search(
        r"(?i)\b(?:do not|don't|doesn't|not|without|no)\s+(?:\w+\s+){0,4}"
        r"(?:qualify|eligible|enrolled|medicaid|public assistance)\b", query))
    if excluded_public and {'qualify', 'eligibility', 'eligible', 'recipient',
                             'application', 'requirement', 'medicaid'} & phrase_terms:
        if not ({'private', 'privately', 'pay', 'payment'} & phrase_terms):
            return True
    # Do not follow a tangential subtopic when the caller's central question
    # is about financial terms or coverage rather than the named activity.
    finance_focus = {'insurance', 'pay', 'payment', 'privately', 'cost', 'price',
                     'fee', 'refund', 'charge', 'cancel'} & original
    if finance_focus and not (finance_focus & phrase_terms):
        return True
    if not extra:
        return False
    # Pure questions about service tasks must not expand to eligibility /
    # financial application steps unless they actually asked about them.
    policy_scope = {'eligible', 'eligibility', 'qualify', 'recipient', 'income',
                    'asset', 'deposits', 'bank', 'enrolled', 'application',
                    'requirements', 'financial', 'medicaid', 'medicare',
                    'veteran', 'jacc', 'mltss', 'pace'}
    if extra & policy_scope and not (original & policy_scope):
        return True
    # Generic scope of 'deposits' is not always equivalent to a home-care
    # deposit: bank-account requirements have different intent.
    if ('bank' in extra or 'account' in extra) and any(
            t in original for t in ('fee', 'cancel', 'charge', 'refund')):
        return True
    return False


def _already_supported(query: str, passages: list[PassageLike], keywords: set[str]) -> bool:
    """Conservative, no-LLM fast path for directly supported care activities.

    Only skip expansion for an *explicit* service-help question when a ranked
    top-two passage contains a matching short activity phrase and nearby
    service/assistance context. A matching word alone is not proof.
    """
    if not re.search(r"(?i)\b(?:help|assist|assistance|support)\b", query):
        return False
    # Avoid assuming a document answers multi-part, comparative or financial
    # questions merely because it describes one activity.
    if re.search(r"(?i)\b(?:difference|versus|vs\.?|insurance|pay|price|cost|charge|refund|fee|eligible|qualify)\b", query):
        return False
    for passage in passages[:2]:
        text = passage.text or ''
        if _conflicting_qualifiers(_specific_qualifiers(query), _heading(passage), text):
            continue
        if not re.search(r"(?i)\b(?:assistance|assist|help|including|include|services? may|services? provided|activities of daily living)\b", text):
            continue
        for phrase in _evidence_phrases(text[:2500]):
            good = _phrase_candidate(phrase, keywords, query)
            if good and len(phrase.split()) <= 12 and not _source_scope_conflicts(query, phrase):
                return True
    return False


# Small, domain-neutral morphology/wording bridges. This is NOT a taxonomy
# of programs or a template for claimed services. Unknown topics use the
# caller's own informative words; no model access is required.
_LEXICAL_BRIDGES: dict[str, str] = {
    'remind': 'reminder', 'reminder': 'remind',
    'administer': 'administration', 'administering': 'administration',
    'prepare': 'preparation', 'preparing': 'preparation',
    'confused': 'confusion', 'wanders': 'wandering',
    'shower': 'bathing', 'showering': 'bathing',
    'dressed': 'dressing', 'dress': 'dressing',
    'overnight': 'nighttime', 'lonely': 'companionship',
    'cancelled': 'cancellation', 'refund': 'reimbursement',
    'moving': 'mobility', 'move': 'mobility',
    'meal': 'food', 'medication': 'medicine',
    'cost': 'pricing', 'price': 'cost',
}
# Broad speech/information scaffolding is omitted only in the supplementary
# short phrase. The FULL unmodified question is always appended to that query.
_QUESTION_FILLER = frozenset({
    'kind', 'sort', 'possible', 'might', 'available', 'options', 'like', 'also',
    'difference', 'between', 'compared', 'versus', 'else', 'home', 'care',
    'please', 'know', 'tell', 'ask', 'looking', 'someone', 'anyone',
})
_NEGATION_SCOPE = re.compile(
    r"(?i)\b(?:not|never|without|except|only|instead of|don.t|doesn.t|won.t|no longer|not eligible)\b"
)


def _question_based_query(original: str, *, passages: list[PassageLike]) -> str | None:
    """Safely broaden weak searches using ONLY words from the caller.

    We don't invent external knowledge and we don't drop caller constraints.
    Skip provider-specific questions, vague requests, unsupported negatives,
    and clearly out-of-domain questions. The output is a retrieval hypothesis.
    """
    if _NEGATION_SCOPE.search(original):
        # Negation is easy to reverse in a compressed bag-of-words search.
        return None
    # A specific near-term staffing commitment cannot be established by
    # paraphrasing public-benefit documents. Keep the exact caller request.
    if re.search(r'(?i)\b(?:today|tomorrow|same.day|last.minute|emergency)\b', original):
        return None
    words = [word.lower() for word in _WORD.findall(original)]
    topic_words = [w for w in words if w not in _STOP and _normalize(w) not in _STOP
                   and _normalize(w) not in _BROAD
                   and w not in _QUESTION_FILLER and len(w) > 2]
    if len(set(_normalize(w) for w in topic_words)) < 2:
        return None
    # Prevent using our home-care corpus to 'answer' an entirely unrelated
    # question, without imposing any predefined PROGRAM categories.
    if not (re.search(r'(?i)\b(?:home.?care|caregiver|elderly|senior|mom|dad|mother|father|grandmother|grandfather|dementia|caregiving|nurs(?:e|ing)|medication|medicine|patient|bathing|dressing|mobility|health|personal care)\b', original)
            or any((_terms(p.text[:1000]) - _BROAD) & (_terms(original) - _BROAD)
                   for p in passages[:3])):
        return None
    # Keep caller nouns, actions and qualifiers in their original order.
    focus = ' '.join(topic_words[:13])
    # Optional lexical variants can reduce a wording mismatch, but are
    # never interpreted as actual service availability or eligibility.
    variants = []
    for word in topic_words:
        variant = _LEXICAL_BRIDGES.get(word)
        if variant and variant not in words and variant not in variants:
            variants.append(variant)
    bridge = ' '.join(variants[:2])
    short = f'{focus} {bridge}'.strip()
    if len(short) < 12 or len(short) > 190:
        return None
    # Search queries must differ meaningfully from the original embedding.
    # Appending the entire caller question caused near-identical AWS results
    # and wasted a sequential KB request in the v4 pilot.
    # Without a lexical bridge, search only when the question can be
    # compressed substantially AND retains at least three specific words.
    # This stays domain-neutral (e.g. wheelchair ramp supervision) but avoids
    # a fee question being searched again with virtually the same text.
    if not variants and not (len(topic_words) >= 3 and
                             len(topic_words) <= len(words) * 0.48):
        return None
    if re.search(r'(?i)\b(?:difference|versus|compare|comparison|vs\.?|rather than)\b', original):
        short += ' comparison'
    # Keep explicit geographic and language restrictions from the caller.
    if re.search(r'(?i)\bnew jersey\b', original) and 'new jersey' not in short:
        short += ' New Jersey'
    return short if len(short) <= 190 and short.casefold() != original.casefold() else None


def plan_feedback_queries(query: str, passages: list[PassageLike], *, max_extra: int = 1,
                          known_program_aliases: tuple[str, ...] = ()) -> FeedbackPlan:
    """Return original question plus a short, purposeful search reformulation.

    The minimum evidence gate prevents amplifying a single spurious hit when
    retrieved content and question don't share substantive vocabulary.
    Query limits and exact deduplication make the output reproducible.
    """
    original = query.strip()
    if not original:
        raise ValueError('query must not be empty')
    if max_extra < 0 or max_extra > 2:
        raise ValueError('max_extra must be between 0 and 2')
    if max_extra == 0 or len(original) > 700:
        return FeedbackPlan([original], 'not_enough_evidence')
    keywords = _terms(original) - _BROAD
    if not keywords:
        # Broad, open-ended requests (e.g. "What services do you offer?")
        # shouldn't be expanded into arbitrary narrower topics based on chance
        # initial hits. One semantic search is the safer default.
        return FeedbackPlan([original], 'no_specific_question_terms')

    required_qualifiers = _specific_qualifiers(original)
    if _organization_specific(original, passages):
        return FeedbackPlan([original], 'unverified_provider_claim')
    if _already_supported(original, passages, keywords):
        return FeedbackPlan([original], 'already_supported')

    # Program names are NOT query-expansion triggers. They are evidence guards:
    # if the caller explicitly asks about program X, don't amplify unrelated
    # first-search results about program Y merely because both mention care.
    # Use the retrieval catalog's aliases; this works equally for Medicare,
    # Medicaid, JACC, PACE, VA, etc., without program-specific templates.
    protected = []
    for alias in sorted(set(known_program_aliases), key=len, reverse=True):
        if len(alias) < 3 or not re.search(r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])', original, re.I):
            continue
        absent = re.search(r'(?i)\b(?:not on|not enrolled in|not eligible for|ineligible for|without|'
                           r"don\'t qualify for|doesn\'t qualify for)\s+" + re.escape(alias) + r'\b', original)
        if not absent:
            protected.append(alias)
    # A comparison can legitimately mention multiple named programs: accept
    # evidence about any positively named program, not necessarily all.
    protected = list(dict.fromkeys(protected))

    # Keep both headings and short factual phrases, but never convert a
    # loosely related program title into the sole additional query.
    candidates: list[tuple[float, str]] = []
    seen: set[str] = set()
    for rank, passage in enumerate(passages[:6], 1):
        heading = _heading(passage)
        text = passage.text or ''
        if _conflicting_qualifiers(required_qualifiers, heading, text):
            continue
        if protected:
            # Metadata/title corroboration prevents wrong-program reinforcement
            # even if the body casually mentions the correct program.
            evidence = ' '.join((heading, passage.program or ''))
            if not any(re.search(r'(?<![a-z])' + re.escape(name) + r'(?![a-z])', evidence, re.I)
                       for name in protected):
                continue
        text_terms = _terms(text[:1600])
        passage_overlap = (text_terms - _BROAD) & keywords
        if not passage_overlap:
            continue
        if heading and heading.casefold() not in seen:
            title_terms = _terms(heading)
            heading_overlap = (title_terms - _BROAD) & keywords
            # Ubiquitous role labels are not a match to the caller's real
            # question (e.g. weekend scheduling or Mandarin language).
            heading_overlap -= {'caregiver', 'caregiv', 'caregiving', 'carer'}
            # Named program questions may search a corroborated program heading.
            # For unspecified everyday needs, require the *heading itself* to
            # contain the topic. This blocks 'Statewide Respite Care Program'
            # becoming the new topic of an ordinary 'prepare meals' question.
            allow_heading = bool(heading_overlap) and bool(passage_overlap)
            # A broad question about bathing, pricing, etc. must not be
            # narrowed to Medicare/VA/another program solely because an
            # initial hit happened to come from that source. Such a heading
            # is safe only when the caller named that context or explicitly
            # asked to discover programs/alternatives.
            source_context = (_terms(passage.program or '') - _BROAD) - {
                'all', 'nj', 'new', 'jersey', 'adult', 'aging', 'agency',
                'program', 'programs', 'benefit', 'benefits', 'home',
                'health', 'service', 'services', 'care', 'aide',
            }
            introduced_program_terms = (source_context & title_terms) - keywords
            seeking_programs = bool(re.search(
                r'(?i)\b(?:which|what|other|alternative|available)\s+(?:\w+\s+){0,2}programs?\b|'
                r'\b(?:alternative to|instead of)\b', original))
            if introduced_program_terms and not seeking_programs:
                allow_heading = False
            if _source_scope_conflicts(original, heading):
                allow_heading = False
            if allow_heading:
                candidates.append((3 * len(heading_overlap) + min(3, len(passage_overlap)) + 1/rank,
                                   heading))
                seen.add(heading.casefold())
                # The heading already provides a concise query. A second
                # phrase from the same passage is redundant and can drown
                # out the original search in RRF.
                continue

        # Generic headings often hide the actual activity in body bullets:
        # 'assistance with dressing', 'bathing in the tub or shower', etc.
        # Evidence is copied verbatim; no fabricated benefits or program names.
        for phrase in _evidence_phrases(text[:2300]):
            key = phrase.casefold()
            if key in seen or key in original.casefold():
                continue
            approved = _phrase_candidate(phrase, keywords, original)
            if approved is None or _source_scope_conflicts(original, phrase):
                continue
            weight, clean_phrase = approved
            # If multiple substantive constraints occur in a question, a
            # generic phrase matching only one word is too easy to misread
            # (a bank deposit is not a cancellation-fee deposit).
            focus = keywords - {'caregiv', 'caregiver', 'family', 'people', 'person',
                                'himself', 'herself', 'myself', 'yourself', 'kind'}
            matched = (_terms(clean_phrase) - _BROAD) & focus
            if len(focus) >= 3 and len(matched) < 2 and not re.search(
                    r'(?i)\b(?:help|assist|assistance)\b', original):
                continue
            candidates.append((weight + 0.5 / rank, clean_phrase))
            seen.add(key)

    candidates.sort(key=lambda x: -x[0])
    searches = [original]
    for _, phrase in candidates:
        if phrase.casefold() in original.casefold():
            continue
        # Preserve explicit caller restrictions without repeating the whole
        # question, unless negation makes a shortened query ambiguous.
        if _NEGATION_SCOPE.search(original):
            new = f'{phrase}. Relevant to caller question: {original}'
        else:
            new = phrase
            for alias in protected:
                if not re.search(r'(?<![a-z])' + re.escape(alias) + r'(?![a-z])', new, re.I):
                    new += ' ' + alias
            for kind, vals in required_qualifiers.items():
                for value in sorted(vals):
                    if not re.search(r'(?i)\b' + re.escape(value) + r'\b', new):
                        new += ' ' + value + (' County' if kind == 'county' else '-speaking')
            if re.search(r'(?i)\bnew jersey\b', original) and 'new jersey' not in new.casefold():
                new += ' New Jersey'
        if len(new) > 950:
            continue
        searches.append(new)
        if len(searches) == max_extra + 1:
            break
    if len(searches) > 1:
        return FeedbackPlan(searches, 'feedback_used')
    # Untrusted retrieved instructions must never influence additional searches.
    if passages and all(_UNSAFE_SNIPPET.search(p.text or '') for p in passages[:3]):
        return FeedbackPlan([original], 'unsafe_retrieved_text')
    # v4: weak/irrelevant first-search passages are not a reason to give up.
    # One strictly question-grounded reformulation may recover a better hit.
    # An already-sufficient answer and provider-specific question were handled
    # above and never reach this fallback.
    alternate = _question_based_query(original, passages=passages)
    if alternate is not None:
        return FeedbackPlan([original, alternate], 'question_rewrite_used')
    return FeedbackPlan([original], 'no_safe_expansion')


def corroborates_original_question(question: str, passage: PassageLike) -> bool:
    """Admit novel feedback results only when they actually match the caller.

    Original-search hits are never filtered by this rule; it is only used for
    results discovered exclusively through a supplemental query. A lexical
    check cannot prove correctness, but can reject many obvious topic drifts
    without requiring InvokeModel or a program-specific rule taxonomy.
    """
    content = (passage.text or '')[:3400]
    title = ' '.join(x for x in (passage.title, passage.heading_path, passage.program) if x)
    if _conflicting_qualifiers(_specific_qualifiers(question), title, content):
        return False
    if _source_scope_conflicts(question, title):
        return False
    if re.search(r'(?i)\bVA\b', passage.program or '') and not re.search(
            r'(?i)\b(?:VA|veteran|veterans)\b', question):
        return False
    focus = (_terms(question) - _BROAD) - {'caregiv', 'caregiver', 'caregiving', 'carer'}
    if not focus:
        return False
    # The source's actual content must address the caller's topic, not just
    # have a generic name such as 'Personal Care Services'.
    haystack = _terms(content + ' ' + title)
    matches = focus & haystack
    if not matches:
        return False
    if len(focus) > 2 and len(matches) < 2:
        return False
    return True
