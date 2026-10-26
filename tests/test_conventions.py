from lexeme_aligner import conventions as cv

TEXT = """Target positions are the letter runs of the verse in reading order. Punctuation is dropped.

English notes:

- **Articles `the` / `a`**: belong to the noun's span.
- **Words in italics in the KJV** were supplied by the translators; give
  a supplied word to the source word it completes.
"""


def test_blocks_and_kinds():
    rules = cv.rules_from_text("eng_kjv", "eng", TEXT, {"file": "x.md"},
                               {"Words in italics": {"derivation": {"basis": "edition markup \\add"}}})
    assert [r["kind"] for r in rules] == ["by-construction", "llm-authored", "llm-authored", "llm-authored"]
    assert rules[0]["derivation"]["source"] == cv.TOKENIZER
    assert rules[2]["title"] == "Articles `the` / `a`"
    assert rules[3]["text"].endswith("it completes.") and rules[3]["derivation"]["basis"] == "edition markup \\add"
    assert rules[1]["check"] == {"status": "unchecked"}


def test_link_checks_points_to_measured_rules_about_the_same_word():
    rules = cv.rules_from_text("eng", "eng", TEXT, {"file": "x.md"})
    prof = {"target": {"NT": {"the": {"n": 50, "what": {"its own source word": 40, "nothing": 10}, "lemmas": {"ὁ": 40}}}}}
    measured = cv.measured_rules(prof, "eng", "bsb-clear", {"source": "gold"})
    assert measured[0]["id"] == "eng.measured.bsb-clear.NT.target.the" and measured[0]["kind"] == "measured"
    cv.link_checks(rules, measured)
    articles = next(r for r in rules if r["title"].startswith("Articles"))
    assert articles["check"]["related_measured"] == ["eng.measured.bsb-clear.NT.target.the"]
    assert articles["check"]["status"] == "unchecked"
