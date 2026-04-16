import re

# Lazy import: avoid hard dependency at import time
_NLP = None
_SPACY_READY = False

_FALLBACK_NON_ENTITY_WORDS = {
    "am", "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "doing", "done",
    "have", "has", "had", "having",
    "can", "could", "may", "might", "must", "shall", "should", "will", "would",
    "in", "on", "at", "of", "to", "for", "from", "by", "with", "about", "into", "over",
    "under", "between", "through", "during", "before", "after", "above", "below", "within",
    "without", "against", "across", "behind", "beside", "among", "around", "as",
    "and", "or", "but", "nor", "so", "yet", "for", "because", "although", "though", "while",
    "unless", "since", "if", "than", "whether",
    "a", "an", "the", "this", "that", "these", "those", "some", "any", "each", "every",
    "either", "neither", "both", "all", "many", "much", "few", "little", "another", "other",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "mine", "yours", "ours", "theirs",
    "who", "whom", "whose", "which", "what",
}

# POS tags typically corresponding to non-entity/function words
_NON_ENTITY_POS = {"ADP", "CCONJ", "SCONJ", "AUX", "DET", "PRON", "PART"}


def _ensure_spacy():
    global _NLP, _SPACY_READY
    if _SPACY_READY:
        return _NLP
    try:
        import spacy
        _NLP = spacy.load("en_core_web_sm")
    except Exception:
        _NLP = None
    _SPACY_READY = True
    return _NLP


def is_non_entity_english_word(word: str) -> bool:
    """Return True if word is likely a function word (no concrete entity meaning)."""
    if not isinstance(word, str):
        return False

    w = word.strip().lower()
    if not w or not re.fullmatch(r"[a-z]+", w):
        return False

    nlp = _ensure_spacy()
    if nlp is not None:
        doc = nlp(w)
        if len(doc) == 1:
            tok = doc[0]
            if tok.pos_ in _NON_ENTITY_POS:
                return True
            # spaCy lexical stop-word as a soft signal
            if tok.is_stop and tok.pos_ != "NOUN" and tok.pos_ != "PROPN":
                return True

    # Fallback for environments without spaCy model
    return w in _FALLBACK_NON_ENTITY_WORDS
