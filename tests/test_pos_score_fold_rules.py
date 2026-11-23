import json

import pytest

from lexeme_aligner.eval import pos_score as ps


def test_gold_fold_rule_table(tmp_path):
    fp = tmp_path / "rules.json"
    fp.write_text(json.dumps({"default": "fold", "a_ed": "stem", "b_ed": {"NT": "fold", "OT": "stem"}}), encoding="utf-8")
    assert ps.gold_fold_rule("a_ed", "OT", fp) == ("stem", True)
    assert ps.gold_fold_rule("b_ed", "OT", fp) == ("stem", True)
    assert ps.gold_fold_rule("b_ed", None, fp) == ("fold", True)           # mixed scope: the NT rule
    assert ps.gold_fold_rule("c_ed", "NT", fp) == ("fold", False)          # not listed: default
    assert ps.fold_args("stem") == {"fold": False, "fold_block": False}
    assert ps.fold_args("fold") == {"fold": True, "fold_block": True}
    with pytest.raises(ValueError):
        ps.fold_args("ending")


def test_repo_table_covers_measured_gold_editions():
    d = json.loads(ps.GOLD_FOLD_RULES.read_text(encoding="utf-8"))
    gold = json.loads((ps.GOLD_FOLD_RULES.parent / "gold_langs.json").read_text(encoding="utf-8"))
    editions = {v.get("edition") for k, v in gold.items() if not k.startswith("_") and isinstance(v, dict)}
    for k, v in d.items():
        if k.startswith("_") or k == "default":
            continue
        assert k in editions, k                                            # keyed by gold edition tags, not languages
        for r in (v.values() if isinstance(v, dict) else [v]):
            assert r in ("stem", "fold")
