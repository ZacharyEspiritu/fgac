from __future__ import annotations

from enumerator.constants import PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX
from enumerator.ngram_enumerator import SearchAsYouTypeNgramEnumerator, TermTrie
from enumerator.prefix_oracles import MatchPhrasePrefixOracle


class TinyTraversal:
    def child_prefixes(self, prefix: str, *, max_term_len: int | None) -> list[str]:
        del max_term_len
        if len(prefix) >= 2:
            return []
        return [prefix + ch for ch in "abc"]


def make_ngram_enumerator(ngram_size: int = 4) -> SearchAsYouTypeNgramEnumerator:
    return SearchAsYouTypeNgramEnumerator(
        admin_client=object(),
        user_client=object(),
        backend=object(),
        traversal=object(),
        ngram_size=ngram_size,
        max_term_len=None,
        max_expansions=10000,
        batch_size=128,
        auto_batch_max_bytes=None,
        auto_batch_max_probes=None,
        exact_strategy="optimized",
        verbose_prefixes=False,
        progress_interval=0,
    )


def test_sayt_ngram_recovery_uses_mpp_on_shingle_fields() -> None:
    enumerator = make_ngram_enumerator()

    assert enumerator.prefix_query_mode == PREFIX_QUERY_MODE_MATCH_PHRASE_PREFIX
    assert isinstance(enumerator.prefix_oracle, MatchPhrasePrefixOracle)
    assert enumerator.prefix_oracle.field == "text._4gram"
    assert enumerator.shingle_field == "text._4gram"


def test_ngram_phrase_helpers_preserve_context_and_extension() -> None:
    phrases = SearchAsYouTypeNgramEnumerator.phrases("alpha beta", ["g", "ga"])

    assert phrases == ["alpha beta g", "alpha beta ga"]
    assert SearchAsYouTypeNgramEnumerator.split_ngram_phrase("alpha beta ga") == (
        "alpha beta",
        "ga",
    )
    assert SearchAsYouTypeNgramEnumerator.extension_from_phrase("alpha beta ga") == "ga"


def test_ngram_controls_keep_context_phrase() -> None:
    enumerator = make_ngram_enumerator(ngram_size=3)

    prefix_control = enumerator.prefix_control_for("alpha beta ga")
    exact_control = enumerator.exact_control_for("alpha beta gas")

    assert prefix_control.startswith("alpha beta ")
    assert len(prefix_control.rsplit(" ", 1)[1]) == len("ga")
    assert exact_control.startswith("alpha beta ")
    assert len(exact_control.rsplit(" ", 1)[1]) >= 10


def test_join_probe_phrases_inserts_ngram_minus_one_separators() -> None:
    enumerator = make_ngram_enumerator(ngram_size=4)
    joined = enumerator.join_probe_phrases(["a b c d", "e f g h"])

    parts = joined.split()
    assert parts[:4] == ["a", "b", "c", "d"]
    assert parts[-4:] == ["e", "f", "g", "h"]
    assert len(parts[4:-4]) == 3


def test_term_trie_follows_analyzer_traversal_edges() -> None:
    trie = TermTrie.from_terms({"ab", "ac"}, TinyTraversal())

    assert trie.initial_prefixes() == ["a"]
    assert trie.child_prefixes("a") == ["ab", "ac"]
    assert trie.is_terminal("ab")
    assert not trie.is_terminal("a")
