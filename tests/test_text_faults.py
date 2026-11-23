from lexeme_aligner import text_faults as tf


def test_only_drop_faults_of_the_edition(tmp_path):
    t = tmp_path / "f.tsv"
    t.write_text("fault_id\tlayer\tedition\tword_id\tbook\tchapter\tverse\tposition\tfound\tfault\tfix\tfixed\tnote\tfound_on\tstatus\n"
                 "F1\tsource_text\theb_mod\tx\tROM\t7\t25\t5\tRomans\tstray_label\tdrop\t\t\t2026-11-23\topen\n"
                 "F2\tsource_text\theb_mod\tx\tMAT\t18\t10\t4\twקטנים\tstray_letter\treplace\tקטנים\t\t2026-11-23\topen\n"
                 "F3\tsource_text\theb_scg\tx\tHEB\t9\t5\t1\tx\tlost\tdrop\t\t\t2026-11-23\topen\n", encoding="utf-8")
    tf.drops.cache_clear()
    assert tf.drops("heb_mod", str(t)) == {("ROM", 7, 25): frozenset({5})}
    assert tf.drops("eng_kjv", str(t)) == {}


def test_withdrawn_rows_ignored(tmp_path):
    t = tmp_path / "f.tsv"
    t.write_text("edition\tbook\tchapter\tverse\tposition\tfix\tstatus\n"
                 "grc_bre\t1KI\t8\t58\t0\tdrop\topen\n"
                 "grc_bre\t1KI\t8\t58\t1\tdrop\twithdrawn\n", encoding="utf-8")
    tf.drops.cache_clear()
    assert tf.drops("grc_bre", str(t)) == {("1KI", 8, 58): frozenset({0})}
