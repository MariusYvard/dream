"""Tests for hook_stop._is_load_bearing — covers accented French forms."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from hook_stop import _is_load_bearing


class TestLoadBearingDetection:
    # ── English / unaccented ────────────────────────────────────────────────
    def test_decide_matches(self):
        assert _is_load_bearing("We decide to use TypeScript.")

    def test_convention_matches(self):
        assert _is_load_bearing("Convention: always use snake_case.")

    def test_bug_matches(self):
        assert _is_load_bearing("There is a bug in the parser.")

    # ── French accented ─────────────────────────────────────────────────────
    def test_decision_accented(self):
        assert _is_load_bearing("Décision : on migre vers PostgreSQL.")

    def test_regle_accented(self):
        assert _is_load_bearing("La règle est de toujours valider les inputs.")

    def test_priorite_accented(self):
        assert _is_load_bearing("Priorité : finir le sprint avant vendredi.")

    def test_procedure_accented(self):
        assert _is_load_bearing("La procédure de déploiement a changé.")

    def test_erreur_fr(self):
        assert _is_load_bearing("Il y a une erreur dans le module auth.")

    # ── Negative cases ───────────────────────────────────────────────────────
    def test_casual_chat_does_not_match(self):
        assert not _is_load_bearing("Comment vas-tu aujourd'hui ?")

    def test_empty_string_does_not_match(self):
        assert not _is_load_bearing("")

    def test_unrelated_technical_text_does_not_match(self):
        assert not _is_load_bearing("The temperature was 22 degrees Celsius.")
