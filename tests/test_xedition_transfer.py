"""xedition_transfer: pure projection/voting/emission logic plus the eflomal plumbing (fake aligner)."""
from lexeme_aligner.eval import xedition_transfer as xt
def test_project_basic_and_unmapped():
    links = {0: {2}, 1: {3}}
    assert xt.project_span([0, 1], links, set()) == (2, 3)
    assert xt.project_span([5], links, set()) is None          # nothing maps
    assert xt.project_span([], links, set()) is None


def test_project_rejects_taken_and_scattered():
    links = {0: {2}, 1: {5}}
    assert xt.project_span([0], links, {2}) is None             # collides with a position T holds
    assert xt.project_span([0, 1], links, set()) is None        # 2 and 5: not contiguous


def test_project_require_all():
    links = {0: {2}}
    assert xt.project_span([0, 1], links, set()) == (2,)
    assert xt.project_span([0, 1], links, set(), require_all=True) is None


def test_project_many_to_one_and_multi_target():
    links = {0: {2, 3}, 1: {3}}
    assert xt.project_span([0, 1], links, set()) == (2, 3)


def test_vote_agreement_and_tiebreak():
    assert xt.vote([None, None]) is None
    assert xt.vote([(1,), (1,), (2,)]) == ((1,), 2, 3)
    # tie: lexicographically smaller span wins, deterministic
    assert xt.vote([(4,), (2, 3)]) == ((2, 3), 1, 2)


def _ed(name, spans, links, usable):
    return xt.RefEdition(name, spans, links, set(usable))


def test_transfer_agreement_and_skips():
    k = ("H1", 0)
    a = _ed("a", {7: {k: {0}}}, {7: {0: {4}}}, [7])
    b = _ed("b", {7: {k: {1}}}, {7: {1: {4}}}, [7])
    c = _ed("c", {7: {k: {2}}}, {7: {2: {5}}}, [7])
    cands, st = xt.transfer({7: [k]}, {7: {0}}, [a, b, c])
    assert len(cands) == 1
    cd = cands[0]
    assert cd.positions == (4,) and cd.n_agree == 2 and cd.n_projecting == 3 and cd.prior == "xedition_n2"
    assert cd.sources == ("a", "b")
    assert st["candidates"] == 1


def test_transfer_unusable_verse_skipped_not_guessed():
    k = ("H1", 0)
    a = _ed("a", {7: {k: {0}}}, {7: {0: {4}}}, [])              # verse 7 not usable (pooling differs)
    cands, st = xt.transfer({7: [k]}, {}, [a])
    assert cands == [] and st["ref_verse_unusable"] == 1


def test_transfer_never_double_claims():
    k1, k2 = ("H1", 0), ("H2", 0)
    a = _ed("a", {7: {k1: {0}, k2: {1}}}, {7: {0: {4}, 1: {4}}}, [7])
    b = _ed("b", {7: {k1: {0}}}, {7: {0: {4}}}, [7])
    cands, st = xt.transfer({7: [k1, k2]}, {}, [a, b])
    assert [c.key for c in cands] == [k1]                       # k1 has 2 agreeing refs, wins position 4
    assert st["dropped_overlap"] == 1


def test_transfer_min_agree():
    k = ("H1", 0)
    a = _ed("a", {7: {k: {0}}}, {7: {0: {4}}}, [7])
    cands, st = xt.transfer({7: [k]}, {}, [a], min_agree=2)
    assert cands == [] and st["below_min_agree"] == 1


def test_to_pair_shape_and_low_score():
    c = xt.Candidate(7, ("H1", 0), (1, 2), 3, 3, 3, ("a", "b", "c"))
    p = xt.to_pair(c, 5, "hbo:0001", "H0001", "אב", "אב", ["x", "y", "z"])
    assert p["method"] == "xfer_edition" and p["prior"] == "xedition_n3"
    assert p["t_idx"] == [1, 2] and p["target"] == "y z" and p["score"] < xt.HI_CONF
    assert xt.to_pair(xt.Candidate(7, "k", (0,), 1, 1, 1), 0, "l", "s", "m", "f", ["w"])["score"] == 0.3


def test_text_similarity():
    a = {1: ["a", "b"], 2: ["c"]}
    b = {1: ["a", "b"], 2: ["d"], 3: ["z"]}
    s = xt.text_similarity(a, b)
    assert s["verses"] == 2 and s["identical_share"] == 0.5 and s["mean_jaccard"] == 0.5
    assert xt.text_similarity({}, b)["verses"] == 0


class _FakeAligner:
    """Writes the same fwd/rev links for every verse: token i <-> token i (identity)."""
    def align(self, src, trg, links_filename_fwd, links_filename_rev, quiet=True, **kw):
        src.seek(0)
        lines = []
        for line in src:
            n = len(line.split())
            lines.append(" ".join(f"{i}-{i}" for i in range(n)))
        for fn in (links_filename_fwd, links_filename_rev):
            with open(fn, "w") as f:
                f.write("\n".join(lines) + "\n")


def test_edition_links_direction_and_verse_intersection():
    t = {1: ["A", "b", "c"], 2: ["x"]}
    r = {1: ["a", "B", "C"], 3: ["q"]}
    out = xt.edition_links(t, r, aligner_factory=_FakeAligner)
    assert set(out) == {1}                                     # only verses both editions have
    assert out[1] == {0: {0}, 1: {1}, 2: {2}}                  # {r_idx: {t_idx}}


def test_edition_links_empty():
    assert xt.edition_links({1: []}, {1: ["a"]}, aligner_factory=_FakeAligner) == {}
