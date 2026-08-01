"""Unit tests for vocabulary grounding.

Only the parts that need no model. The vector strategies need a 7GB fastText
file, so they are exercised by hand rather than in the suite -- what is tested
here is everything that decides *which* entries are worth making, which is
where the bugs were.
"""

from __future__ import annotations

import pytest

from codesense.indexing.grounding import (
    MIN_ENTRY_SCORE,
    GroundingConfig,
    _skeleton,
    _vector_score,
    abbreviates,
    coverage,
    discriminating_targets,
    ground_lexically,
    ground_vocabulary,
)

CONFIG = GroundingConfig()


def table_for(vocab: dict[str, int]) -> dict[str, list[tuple[str, float, str]]]:
    return ground_lexically(vocab, config=CONFIG)


class TestConfig:
    def test_rejects_an_unknown_strategy(self) -> None:
        with pytest.raises(ValueError, match="strategy must be one of"):
            GroundingConfig(strategy="magic")

    def test_a_vector_strategy_needs_a_model(self) -> None:
        with pytest.raises(ValueError, match="needs model_path"):
            GroundingConfig(strategy="vectors")

    def test_lexical_needs_nothing(self) -> None:
        assert GroundingConfig(strategy="lexical").model_path is None

    def test_the_icf_floor_matches_the_query_side(self) -> None:
        """It has to: a build-time gate at a different threshold would keep
        entries the satisfier then discards."""
        from dataclasses import fields

        from codesense.ql.context import EvalContext

        query_side = {f.name: f.default for f in fields(EvalContext)}["icf_floor"]
        assert GroundingConfig().icf_floor == query_side


class TestSkeleton:
    def test_drops_vowels_and_collapses_repeats(self) -> None:
        assert _skeleton("message") == "msg"

    def test_keeps_a_leading_vowel(self) -> None:
        """A leading vowel carries the word, as in `alloc`."""
        assert _skeleton("alloc").startswith("a")

    def test_an_abbreviation_and_its_word_agree(self) -> None:
        assert _skeleton("msg") == _skeleton("message")


class TestAbbreviates:
    def test_a_prefix_counts(self) -> None:
        assert abbreviates("buf", "buffer")

    def test_a_dropped_vowel_counts(self) -> None:
        assert abbreviates("msg", "message")

    def test_unrelated_words_do_not(self) -> None:
        assert not abbreviates("cat", "dog")

    def test_two_letters_are_too_short(self) -> None:
        """Two letters abbreviate far too much to be evidence."""
        assert not abbreviates("bu", "buffer")

    def test_a_word_does_not_abbreviate_itself(self) -> None:
        assert not abbreviates("buffer", "buffer")


class TestGroundLexically:
    def test_the_long_form_is_the_key(self) -> None:
        """Keys are what a query might say; targets are what the project
        writes."""
        assert "buf" in [t for t, _, _ in table_for({"buf": 3, "buffer": 3})["buffer"]]

    def test_records_which_rule_fired(self) -> None:
        assert table_for({"buf": 3, "buffer": 3})["buffer"][0][2] == "prefix"

    def test_finds_a_dropped_vowel_abbreviation(self) -> None:
        assert table_for({"msg": 3, "message": 3})["message"][0][2] == "abbrev"

    def test_finds_a_subsequence_abbreviation(self) -> None:
        """`ctx` loses the `n`, so no skeleton reaches it."""
        assert table_for({"ctx": 3, "context": 3})["context"][0][2] == "subseq"

    def test_a_subsequence_scores_below_a_prefix(self) -> None:
        """It is the rule most likely to be wrong."""
        both = table_for({"ctx": 3, "context": 3, "buf": 3, "buffer": 3})
        assert both["context"][0][1] < both["buffer"][0][1]

    def test_caps_the_targets_per_key(self) -> None:
        vocab = {"a" * 3: 3, **{f"aaa{i}x": 3 for i in range(10)}}
        config = GroundingConfig(max_targets=2)
        assert all(len(v) <= 2 for v in ground_lexically(vocab, config=config).values())

    def test_no_key_maps_to_itself(self) -> None:
        assert all(
            key not in [t for t, _, _ in entries]
            for key, entries in table_for({"buf": 3, "buffer": 3, "buffered": 3}).items()
        )

    def test_empty_vocabulary(self) -> None:
        assert table_for({}) == {}


class TestDiscriminatingTargets:
    def test_drops_a_term_on_nearly_every_symbol(self) -> None:
        """`public` reaches almost every symbol and says nothing about which
        one is wanted."""
        found = discriminating_targets({"public": 950, "watermark": 4}, 1000, CONFIG)
        assert "public" not in found
        assert "watermark" in found

    def test_drops_singletons(self) -> None:
        """A term on one symbol is usually a typo or a one-off local."""
        assert "typo" not in discriminating_targets({"typo": 1}, 1000, CONFIG)

    def test_uses_symbol_count_not_vocabulary_size(self) -> None:
        """The denominator has to be the one `TermInfo.icf_ratio` uses, or the
        two gates disagree and the table fills with entries the satisfier
        throws away."""
        terms = {"word": 50}
        assert discriminating_targets(terms, 100, CONFIG) == {}
        assert discriminating_targets(terms, 100_000, CONFIG) == terms

    def test_no_symbols(self) -> None:
        assert discriminating_targets({"a": 1}, 0, CONFIG) == {}


class TestVectorScore:
    def test_at_the_floor_it_is_zero(self) -> None:
        assert _vector_score(GroundingConfig().min_cosine) == 0.0

    def test_a_perfect_cosine_reaches_the_ceiling(self) -> None:
        assert _vector_score(1.0) == pytest.approx(0.6)

    def test_rises_with_the_cosine(self) -> None:
        assert _vector_score(0.9) > _vector_score(0.7) > _vector_score(0.6)

    def test_stays_below_a_lexical_rule(self) -> None:
        """An orthographic rule is close to certain; a cosine is an estimate."""
        from codesense.indexing.grounding import LEXICAL_SCORE

        assert _vector_score(1.0) < LEXICAL_SCORE

    def test_a_mid_range_cosine_is_still_worth_keeping(self) -> None:
        """Regression: squaring the rescale pushed everything below cosine 0.8
        under 0.01, small enough to change no ranking at all."""
        assert _vector_score(0.75) >= MIN_ENTRY_SCORE


class TestGroundVocabulary:
    def test_lexical_needs_no_model(self) -> None:
        table = ground_vocabulary({"buf": 4, "buffer": 4}, 1000, config=CONFIG)
        assert "buffer" in table

    def test_a_broken_model_degrades_to_lexical(self) -> None:
        """Grounding is an enhancement, not a gate: a missing model must not
        cost you the index."""
        config = GroundingConfig(strategy="vectors", model_path="/nonexistent/model.bin")
        table = ground_vocabulary({"buf": 4, "buffer": 4}, 1000, config=config)
        assert "buffer" in table

    def test_empty_vocabulary(self) -> None:
        assert ground_vocabulary({}, 1000, config=CONFIG) == {}


class TestCoverage:
    def test_share_of_terms_reached(self) -> None:
        table = {"buffer": [("buf", 0.75, "prefix")]}
        assert coverage({"buf": 3, "other": 3}, table) == 0.5

    def test_no_terms(self) -> None:
        assert coverage({}, {}) == 0.0
