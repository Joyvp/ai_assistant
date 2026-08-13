"""Tests for automatic fact capture.

The bar for this module is precision. A missed fact costs one ``/remember``;
a wrong fact poisons every prompt from then on. So the negative cases below
matter more than the positive ones.
"""

from __future__ import annotations

import pytest

from apexis_desktop import capture
from apexis_desktop.memory import Memory


def texts(message: str) -> list[str]:
    return [c.text for c in capture.extract(message)]


# -- things that should be captured ---------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("i live in saskatoon", "I live in Saskatoon"),
        ("I'm based in Toronto", "I live in Toronto"),
        ("i moved to regina last year", "I live in Regina"),
        ("my name is joy", "My name is Joy"),
        ("you can call me joy", "My name is Joy"),
        ("i'm from england", "I am from England"),
        ("i work as a nurse", "I work as a nurse"),
        ("i'm a student", "I am a student"),
        ("i'm building a task router", "I am building a task router"),
        ("i'm working on apexis", "I am working on apexis"),
        ("i use neovim", "I use neovim"),
        ("i don't like bullet lists", "I do not like bullet lists"),
        ("i hate small talk", "I do not like small talk"),
        ("i prefer short answers", "I prefer short answers"),
        ("i'm allergic to peanuts", "I am allergic to peanuts"),
        ("my pronouns are she/her", "My pronouns are she/her"),
    ],
)
def test_captures_plain_statements(said, expected):
    assert expected in texts(said)


def test_i_am_is_treated_like_im():
    assert texts("i am building apexis") == ["I am building apexis"]


def test_curly_apostrophes_are_handled():
    assert texts("I\u2019m based in Regina") == ["I live in Regina"]


def test_a_capitalised_bare_name_is_captured():
    assert texts("I'm Joy") == ["My name is Joy"]


@pytest.mark.parametrize("said", ["im tired", "i'm hungry", "im busy", "im joy"])
def test_a_lowercase_bare_adjective_is_never_a_name(said):
    # Precision over recall: "im joy" is indistinguishable from "im tired",
    # so neither is captured. "my name is joy" still works.
    assert capture.extract(said) == []


def test_two_facts_in_one_sentence():
    assert texts("my name is joy and i live in saskatoon") == [
        "My name is Joy",
        "I live in Saskatoon",
    ]


def test_three_facts_across_commas():
    assert texts("my name is joy, i live in regina, and i use arch") == [
        "My name is Joy",
        "I live in Regina",
        "I use arch",
    ]


def test_a_joined_clause_that_is_not_a_fact_is_skipped():
    assert texts("i live in saskatoon but it is cold") == ["I live in Saskatoon"]


def test_explicit_remember_without_the_slash_command():
    assert texts("remember that my gmail is joy@example.com") == [
        "My gmail is joy@example.com"
    ]


def test_please_remember_is_also_explicit():
    assert texts("please remember my door code is 4417") == ["My door code is 4417"]


# -- things that must NOT be captured -------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "hey",
        "what's up",
        "where do i live?",
        "do i live in saskatoon",
        "how do i use docker",
        "if i lived in tokyo it would be warmer",
        "should i use rust or go",
        "i used to live in toronto",
        "i'm not sure i like this",
        "maybe i live in the wrong city",
        "i wish i lived somewhere warm",
        "/remember i live in saskatoon",
        "/facts",
        "write me a script that lives in /tmp",
        "i",
        "i use",
    ],
)
def test_ignores_non_facts(said):
    assert capture.extract(said) == []


def test_ignores_empty_input():
    assert capture.extract("") == []
    assert capture.extract("   ") == []


def test_question_mark_anywhere_blocks_capture():
    assert capture.extract("i live in saskatoon, right?") == []


# -- shaping ---------------------------------------------------------------


def test_stops_at_the_end_of_the_clause():
    assert texts("i live in saskatoon and it is freezing") == ["I live in Saskatoon"]


def test_stops_at_a_comma():
    assert texts("i live in regina, which is flat") == ["I live in Regina"]


def test_a_full_stop_inside_a_token_is_not_a_clause_end():
    assert texts("remember my email is joy@example.com") == [
        "My email is joy@example.com"
    ]


def test_a_colon_inside_a_model_tag_survives():
    assert texts("i use phi3:mini") == ["I use phi3:mini"]


def test_trailing_filler_is_trimmed():
    assert texts("i live in saskatoon right now") == ["I live in Saskatoon"]


def test_existing_capitalisation_is_respected():
    assert texts("i live in New York") == ["I live in New York"]


def test_overlong_subjects_are_rejected():
    assert capture.extract("i use " + "x" * 200) == []


def test_two_sentences_give_two_facts():
    got = texts("my name is joy. i live in saskatoon")
    assert got == ["My name is Joy", "I live in Saskatoon"]


def test_one_fact_per_clause():
    # Both "i'm building" and "i'm working on" could match; first rule wins.
    assert len(capture.extract("i'm building apexis")) == 1


def test_duplicate_statements_collapse():
    assert len(texts("i live in regina. i live in regina")) == 1


# -- slots -----------------------------------------------------------------


def test_identity_facts_carry_a_slot():
    (candidate,) = capture.extract("i live in saskatoon")
    assert candidate.key == "location"


def test_open_ended_facts_have_no_slot():
    (candidate,) = capture.extract("i'm building apexis")
    assert candidate.key is None


# -- integration with Memory ----------------------------------------------


@pytest.fixture()
def memory(tmp_path):
    with Memory(tmp_path / "m.db") as m:
        yield m


def test_absorb_stores_and_returns_facts(memory):
    saved = memory.absorb("i live in saskatoon")
    assert [f.text for f in saved] == ["I live in Saskatoon"]
    assert [f.text for f in memory.facts()] == ["I live in Saskatoon"]


def test_absorb_tags_the_source_as_auto(memory):
    (fact,) = memory.absorb("my name is joy")
    assert fact.source == "auto"
    assert fact.auto is True


def test_explicit_remember_is_not_tagged_auto(memory):
    fact = memory.remember("I live in Saskatoon")
    assert fact.source == "user"
    assert fact.auto is False


def test_absorb_returns_nothing_for_ordinary_chat(memory):
    assert memory.absorb("hey what's the weather like") == []


def test_absorb_does_not_store_a_duplicate(memory):
    memory.absorb("i live in saskatoon")
    assert memory.absorb("i live in saskatoon") == []
    assert len(memory.facts()) == 1


def test_a_new_home_replaces_the_old_one(memory):
    memory.absorb("i live in saskatoon")
    memory.absorb("i moved to regina")
    assert [f.text for f in memory.facts()] == ["I live in Regina"]


def test_slotless_facts_accumulate(memory):
    memory.absorb("i use neovim")
    memory.absorb("i use ollama")
    assert len(memory.facts()) == 2


def test_manual_remember_can_take_a_slot(memory):
    memory.remember("I live in Saskatoon", slot="location")
    memory.remember("I live in Regina", slot="location")
    assert [f.text for f in memory.facts()] == ["I live in Regina"]


def test_absorbed_facts_reach_the_prompt(memory):
    memory.absorb("i live in saskatoon")
    assert "I live in Saskatoon" in memory.facts_block()


def test_auto_capture_is_on_by_default(memory):
    assert memory.auto_capture is True


def test_auto_capture_can_be_turned_off(memory):
    memory.auto_capture = False
    assert memory.absorb("i live in saskatoon") == []
    assert memory.facts() == []


def test_auto_capture_setting_survives_a_reopen(tmp_path):
    path = tmp_path / "m.db"
    with Memory(path) as first:
        first.auto_capture = False
    with Memory(path) as second:
        assert second.auto_capture is False


def test_turning_it_back_on_resumes_capture(memory):
    memory.auto_capture = False
    memory.absorb("i live in saskatoon")
    memory.auto_capture = True
    assert memory.absorb("i live in saskatoon")


def test_manual_remember_still_works_with_capture_off(memory):
    memory.auto_capture = False
    memory.remember("I live in Saskatoon")
    assert len(memory.facts()) == 1


def test_stats_counts_auto_facts_separately(memory):
    memory.absorb("i live in saskatoon")
    memory.remember("I like coffee")
    stats = memory.stats()
    assert stats == {"facts": 2, "auto": 1, "messages": 0, "sessions": 0}


def test_has_fact_is_case_insensitive(memory):
    memory.remember("I live in Saskatoon")
    assert memory.has_fact("i live in saskatoon")
    assert not memory.has_fact("i live in regina")


# -- migration -------------------------------------------------------------


def test_a_v1_database_upgrades_without_losing_facts(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO facts (text, source, created_at)
            VALUES ('I live in Saskatoon', 'user', '2026-08-01T00:00:00+00:00');
        INSERT INTO meta (key, value) VALUES ('schema_version', '1');
        """
    )
    conn.commit()
    conn.close()

    with Memory(path) as m:
        assert [f.text for f in m.facts()] == ["I live in Saskatoon"]
        assert m.get_setting("schema_version") == "2"
        # And the new column works.
        m.absorb("i moved to regina")
        assert "I live in Regina" in [f.text for f in m.facts()]


def test_migration_backfills_slots_on_old_facts(tmp_path):
    """A fact stored before slots existed must still be replaceable.

    Otherwise moving city leaves two contradictory homes in the prompt.
    """
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        );
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO facts (text, source, created_at)
            VALUES ('I live in Saskatoon', 'user', '2026-08-01T00:00:00+00:00');
        INSERT INTO facts (text, source, created_at)
            VALUES ('I like coffee', 'user', '2026-08-01T00:00:00+00:00');
        """
    )
    conn.commit()
    conn.close()

    with Memory(path) as m:
        slots = {f.text: f.slot for f in m.facts()}
        assert slots["I live in Saskatoon"] == "location"
        assert slots["I like coffee"] is None

        m.absorb("i moved to regina")
        assert [f.text for f in m.facts()] == ["I like coffee", "I live in Regina"]
