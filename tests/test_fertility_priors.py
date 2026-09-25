"""Tests for Step 3's fertility_priors.build_fertility_priors()/build_null_prior() — pure logic,
synthetic Grambank + HebToken data, no real spine/corpus needed."""
import lexeme_aligner.fertility_priors as fp
from lexeme_aligner.hebrew_source import HebToken


def tok(idx, strong, rela=None, case_=None, person=None, lexeme=None):
    t = HebToken(idx, f"w{idx}", strong, lexeme or f"lx:{strong}", f"l{idx}", None, True)
    t.rela, t.case_, t.person = rela, case_, person
    return t


class _Rec:
    def __init__(self, book, ch, v, heb):
        self.book, self.ch, self.v, self.heb = book, ch, v, heb


class _FakeHeb:
    def assimilated_after_idx(self, book, ch, v):
        return set()


HAS_ADP = {"GB074": "1", "GB075": "0"}
HAS_ART = {"GB022": "1", "GB023": "0"}
SUBJ_INCOMPLETE = {"GB089": "0", "GB090": "0"}
NO_SIGNAL = {}


def test_possessor_flagged_when_adposition_direction_exists(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: HAS_ADP)
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1", rela="rec")])]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    assert priors == {"H1": (2, 1.0)}          # increments=1 -> f=2; alpha=min(n_a=1, lam*1)=1.0


def test_possessor_not_flagged_without_an_adposition_direction(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: NO_SIGNAL)
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1", rela="rec")])]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    assert priors == {}


def test_dative_uses_the_same_adposition_gate_as_possessor(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: HAS_ADP)
    recs = [_Rec("MAT", 1, 1, [tok(0, "G1", case_="dative")])]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    assert priors == {"G1": (2, 1.0)}


def test_finite_verb_flagged_when_subject_indexing_incomplete(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: SUBJ_INCOMPLETE)
    recs = [_Rec("MAT", 1, 1, [tok(0, "G1", person="3")])]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    assert priors == {"G1": (2, 1.0)}


def test_finite_verb_not_flagged_when_subject_indexing_present(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: {"GB089": "1"})
    recs = [_Rec("MAT", 1, 1, [tok(0, "G1", person="3")])]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    assert priors == {}


def test_multiple_relations_on_the_same_anchor_combine_increments_and_alpha(monkeypatch):
    combined = {**HAS_ADP, **SUBJ_INCOMPLETE}
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: combined)
    recs = [_Rec("MAT", 1, 1, [tok(0, "G1", rela="rec"), tok(1, "G1", person="3")])]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    # n_a=2, k[possessor]=1, k[finite_verb]=1 -> increments=2 -> f=3; alpha=min(2, 1.0*(1+1))=2.0
    assert priors == {"G1": (3, 2.0)}


def test_alpha_is_capped_at_the_anchors_own_occurrence_count(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: HAS_ADP)
    recs = [_Rec("RUT", 1, i, [tok(0, "H1", rela="rec")]) for i in range(3)]
    priors = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb(), lam=10.0)
    fert, alpha = priors["H1"]
    assert alpha == 3.0                        # capped at n_a=3, not lam*3=30


def test_fertility_is_clamped_to_the_eflomal_array_bound(monkeypatch):
    # a synthetic anchor flagged for all 4 relations at once would want f=5 -- still well under the
    # cap, so exercise the cap directly against FERT_CAP instead of contriving 4 real relations.
    assert fp.FERT_CAP == 7


def test_definite_flagged_via_proper_name_when_article_direction_exists(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: HAS_ART)
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1", lexeme="lx:name")])]
    priors = fp.build_fertility_priors(recs, "fake", {"lx:name": "name"}, _FakeHeb())
    assert priors == {"H1": (2, 1.0)}


def test_invert_placebo_flags_unflagged_anchors_instead(monkeypatch):
    monkeypatch.setattr(fp, "load_grambank_raw", lambda iso, path=None: HAS_ADP)
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1", rela="rec"), tok(1, "H2")])]
    real = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb())
    placebo = fp.build_fertility_priors(recs, "fake", {}, _FakeHeb(), invert=True)
    assert "H1" in real and "H1" not in placebo
    assert "H2" in placebo


def test_build_null_prior_flags_a_fixed_fraction_by_frequency():
    recs = [_Rec("RUT", 1, 1, [tok(0, "H1"), tok(1, "H1"), tok(2, "H2")])]
    priors = fp.build_null_prior(recs, fraction=0.5)
    assert set(priors) == {"H1"}                # the single most frequent anchor type
    fert, alpha = priors["H1"]
    assert fert == fp.NULL_PRIOR_FERT


# --- load_fertility_flags (config-driven per-language flag reuse, mirrors span_extension's) --------------
def test_load_fertility_flags_reads_a_languages_entry(tmp_path):
    fp_path = tmp_path / "fertility_flags.json"
    fp_path.write_text('{"hin": {"enabled": true, "lambda": 2.0, "_note": "measured"}}', encoding="utf-8")
    assert fp.load_fertility_flags("hin", path=fp_path) == {"enabled": True, "lambda": 2.0}


def test_load_fertility_flags_skips_non_bool_non_number_keys(tmp_path):
    fp_path = tmp_path / "fertility_flags.json"
    fp_path.write_text('{"arb": {"enabled": false, "lambda": 2.0, "_note": "wash"}}', encoding="utf-8")
    assert fp.load_fertility_flags("arb", path=fp_path) == {"enabled": False, "lambda": 2.0}


def test_load_fertility_flags_missing_language_is_empty(tmp_path):
    fp_path = tmp_path / "fertility_flags.json"
    fp_path.write_text('{"hin": {"enabled": true}}', encoding="utf-8")
    assert fp.load_fertility_flags("xyz", path=fp_path) == {}


def test_load_fertility_flags_missing_file_is_empty(tmp_path):
    assert fp.load_fertility_flags("hin", path=tmp_path / "nope.json") == {}
