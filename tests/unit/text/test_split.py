"""Unit tests for identifier splitting and corpus extraction.

Indexing and embedding must split identifiers the same way, or the vectors are
trained on tokens the index never stores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codesense.text import (
    Splitter,
    corpus_sentences,
    source_files,
    split_identifier,
    words_of,
)


@dataclass
class FakeDeclaration:
    name: str = ""
    container: str = ""
    signature: str = ""
    doc: str = ""
    annotations: tuple[object, ...] = field(default_factory=tuple)


@dataclass
class FakeAnnotation:
    name: str


class TestSplitIdentifier:
    def test_splits_camel_case(self) -> None:
        assert split_identifier("parseResponse") == ["parse", "response"]

    def test_splits_a_run_of_capitals(self) -> None:
        assert split_identifier("parseHTTPResponse") == ["parse", "http", "response"]

    def test_lowercases(self) -> None:
        assert split_identifier("Buffer") == ["buffer"]

    def test_drops_digits(self) -> None:
        """`utf8` yields `utf`: a bare `8` matches nothing and inflates the
        vocabulary."""
        assert "8" not in split_identifier("utf8Decoder")

    def test_empty_name(self) -> None:
        assert split_identifier("") == []

    def test_is_deterministic(self) -> None:
        assert split_identifier("readableBytes") == split_identifier("readableBytes")


class TestWordsOf:
    def test_takes_words_from_prose(self) -> None:
        assert words_of("Returns the buffer") == ["returns", "the", "buffer"]

    def test_drops_single_letters(self) -> None:
        assert "a" not in words_of("a buffer")

    def test_honours_the_limit(self) -> None:
        assert len(words_of("one two three four five", limit=3)) == 3

    def test_no_limit_takes_everything(self) -> None:
        assert len(words_of("one two three four five")) == 5


class TestCorpusSentences:
    def test_one_declaration_is_one_sentence(self) -> None:
        """The point of the corpus: `buf` learns its meaning from sitting
        beside `alloc` and `capacity` in the same declaration."""
        declaration = FakeDeclaration(name="allocBuffer", container="PooledAllocator")
        assert list(corpus_sentences([declaration])) == [["alloc", "buffer", "pooled", "allocator"]]

    def test_skips_a_one_word_sentence(self) -> None:
        """A single word teaches a co-occurrence model nothing."""
        assert list(corpus_sentences([FakeDeclaration(name="run")])) == []

    def test_includes_doc_words(self) -> None:
        sentence = next(iter(corpus_sentences([FakeDeclaration(name="read", doc="from socket")])))
        assert "socket" in sentence

    def test_includes_annotation_names(self) -> None:
        declaration = FakeDeclaration(name="handle", annotations=(FakeAnnotation("GetMapping"),))
        assert "mapping" in next(iter(corpus_sentences([declaration])))

    def test_collapses_immediate_repeats(self) -> None:
        """A getter repeats its own name across name and signature; that is
        Java's shape, not evidence the words belong together."""
        declaration = FakeDeclaration(name="getUser", signature="getUser() : User")
        sentence = next(iter(corpus_sentences([declaration])))
        assert sentence == ["get", "user"]

    def test_handles_objects_missing_attributes(self) -> None:
        """Duck-typed on purpose, so the corpus can be built from anything with
        a name."""
        assert list(corpus_sentences([object()])) == []


class FakeLanguage:
    """Stands in for an adapter: `source_files` needs only these two."""

    name = "fake"
    file_globs = ("*.java",)
    skip_parts = ("/test/", "/generated/", "/target/")


class TestSourceFiles:
    def test_finds_sources_the_language_claims(self, tmp_path: Path) -> None:
        (tmp_path / "A.java").write_text("class A {}")
        (tmp_path / "notes.md").write_text("x")
        assert [p.name for p in source_files(tmp_path, FakeLanguage())] == ["A.java"]

    def test_skips_tests_and_generated(self, tmp_path: Path) -> None:
        for part in ("test", "generated", "target"):
            directory = tmp_path / "src" / part
            directory.mkdir(parents=True)
            (directory / "B.java").write_text("class B {}")
        (tmp_path / "Keep.java").write_text("class Keep {}")
        assert [p.name for p in source_files(tmp_path, FakeLanguage())] == ["Keep.java"]

    def test_is_ordered_so_symbol_ids_reproduce(self, tmp_path: Path) -> None:
        for name in ("C.java", "A.java", "B.java"):
            (tmp_path / name).write_text("class X {}")
        assert [p.name for p in source_files(tmp_path, FakeLanguage())] == [
            "A.java",
            "B.java",
            "C.java",
        ]

    def test_takes_every_glob(self, tmp_path: Path) -> None:
        """A language may claim more than one extension."""

        class TwoGlobs(FakeLanguage):
            file_globs = ("*.java", "*.kt")

        (tmp_path / "A.java").write_text("x")
        (tmp_path / "B.kt").write_text("x")
        assert len(list(source_files(tmp_path, TwoGlobs()))) == 2


class TestSplitter:
    """Compound segmentation. The lexicon is the whole of the training."""

    LEXICON = {
        "io": 500,
        "stat": 200,
        "net": 400,
        "link": 300,
        "iostat": 3,
        "interface": 60,
        "inter": 2,
        "face": 2,
        "nfs": 40,
        "read": 800,
    }

    def splitter(self) -> Splitter:
        return Splitter(self.LEXICON)

    def test_segments_a_compound_the_delimiter_left_whole(self) -> None:
        """Ronin returns `iostat` unsplit -- measured, not assumed."""
        assert self.splitter().segment("iostat") == ("io", "stat")

    def test_keeps_a_word_that_is_common_in_its_own_right(self) -> None:
        """`interface` segments into `inter` + `face` quite happily, and doing
        so is wrong."""
        assert self.splitter().segment("interface") == ()

    def test_refuses_a_single_letter_piece(self) -> None:
        """Otherwise `nfsd` becomes `nfs` + `d`, which matches everywhere."""
        assert self.splitter().segment("nfsd") == ()

    def test_refuses_a_piece_outside_the_lexicon(self) -> None:
        assert self.splitter().segment("zzzread") == ()

    def test_leaves_short_tokens_alone(self) -> None:
        assert self.splitter().segment("read") == ()

    def test_split_keeps_the_whole_word_beside_its_pieces(self) -> None:
        """`iostat` is a real thing to search for; dropping it would trade one
        kind of miss for another."""
        assert self.splitter().split("iostat") == ["iostat", "io", "stat"]

    def test_split_still_does_boundary_splitting(self) -> None:
        assert self.splitter().split("readIostat") == ["read", "iostat", "io", "stat"]

    def test_an_empty_lexicon_segments_nothing(self) -> None:
        assert Splitter({}).segment("iostat") == ()

    def test_from_tokens_counts(self) -> None:
        splitter = Splitter.from_tokens(["io", "io", "stat", "x"])
        assert splitter.known("io") and not splitter.known("x")

    def test_is_deterministic(self) -> None:
        assert self.splitter().segment("iostat") == self.splitter().segment("iostat")
