"""Host tests of the documentation check (scripts/check_foundation.py):
GitHub's anchors (repeated and accented headings), local links and anchors,
and the translation map: pairs, layout, the English / Español links, links
from a translation, and an original changed after its translation's review.
The repositories are temporary folders without Git, as an exported archive."""

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_foundation as foundation  # noqa: E402


class AnchorTests(unittest.TestCase):
    def test_slugs_as_github_makes_them(self):
        cases = {
            "1. What you need": "1-what-you-need",
            "Build the image yourself": "build-the-image-yourself",
            "Dress the console in your game (MUN Shape)": "dress-the-console-in-your-game-mun-shape",
            "1. Qué necesitas": "1-qué-necesitas",
            "Construir la imagen tú mismo": "construir-la-imagen-tú-mismo",
            "¿Dónde está cada cosa?": "dónde-está-cada-cosa",
            "MUN™ OS": "mun-os",
            "`./mun play` y la ventana": "mun-play-y-la-ventana",
            "Juego · menú": "juego--menú",
        }
        for heading, expected in cases.items():
            with self.subTest(heading=heading):
                self.assertEqual(foundation.slug(foundation.heading_text(heading)), expected)

    def test_a_repeated_heading_is_numbered_and_code_is_not_a_heading(self):
        content = "# Guía\n\n## Notas\n\n## Notas\n\n## Notas 1\n\n```sh\n# not a heading\n```\n\n### Qué es\n"
        self.assertEqual(foundation.anchors(content),
                         {"guía", "notas", "notas-1", "notas-1-1", "qué-es"})


class Repository:
    """A small repository, written as files only (no Git)."""

    def __init__(self, test):
        directory = tempfile.TemporaryDirectory(prefix="foundation-")
        test.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def record(self, *pairs):
        entries = [{"translation": translation, "source": source,
                    "source_sha256": hashlib.sha256((self.root / source).read_bytes()).hexdigest()}
                   for translation, source in pairs]
        self.write("docs/translations.json", json.dumps({"format": "mun-translations/1", "translations": entries}))

    def check(self):
        return foundation.check(self.root)[0]


@contextlib.contextmanager
def without_git_environment():
    # Under a Git hook, GIT_DIR and GIT_INDEX_FILE would point `git ls-files`
    # at the repository being committed instead of the temporary one.
    with patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                 if not key.startswith("GIT_")}, clear=True):
        yield


class TranslationTests(unittest.TestCase):
    def setUp(self):
        context = without_git_environment()
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.repo = Repository(self)
        repo = self.repo
        repo.write("README.md", "# Project\n\n**English** · [Español](README.es.md)\n\n"
                                "See [getting started](docs/start.md#1-what-you-need) and [design](docs/design.md).\n")
        repo.write("README.es.md", "# Proyecto\n\n[English](README.md) · **Español**\n\n"
                                   "Mira [cómo empezar](docs/es/start.md#1-qué-necesitas) y el "
                                   "[diseño](docs/design.md) (en inglés).\n")
        repo.write("docs/start.md", "# Start\n\n**English** · [Español](es/start.md)\n\n## 1. What you need\n")
        repo.write("docs/es/start.md", "# Empezar\n\n[English](../start.md) · **Español**\n\n## 1. Qué necesitas\n")
        repo.write("docs/design.md", "# Design\n")
        repo.record(("README.es.md", "README.md"), ("docs/es/start.md", "docs/start.md"))

    def test_a_complete_pair_passes(self):
        self.assertEqual(self.repo.check(), [])

    def test_an_original_changed_after_the_review_fails_until_the_hash_is_set_by_hand(self):
        self.repo.write("docs/start.md", "# Start\n\n**English** · [Español](es/start.md)\n\n## 1. What you need\n\nMore.\n")
        errors = self.repo.check()
        self.assertEqual(len(errors), 1)
        self.assertIn("docs/es/start.md: its original docs/start.md changed after the translation was reviewed", errors[0])
        self.assertIn(hashlib.sha256((self.repo.root / "docs/start.md").read_bytes()).hexdigest(), errors[0])
        self.assertEqual(self.repo.check(), errors, "the check never records a review by itself")

    def test_each_page_links_to_its_counterpart(self):
        self.repo.write("docs/start.md", "# Start\n\n## 1. What you need\n")
        self.repo.record(("README.es.md", "README.md"), ("docs/es/start.md", "docs/start.md"))
        self.assertEqual(self.repo.check(),
                         ["docs/start.md: no link to its translation docs/es/start.md (the English / Español line)"])

    def test_a_spanish_page_must_be_listed_but_an_english_one_need_not_be_translated(self):
        self.repo.write("docs/es/design.md", "# Diseño\n\n[English](../design.md) · **Español**\n")
        self.assertEqual(self.repo.check(), ["docs/es/design.md: a Spanish page not listed in docs/translations.json"])

    def test_the_layout_mirrors_docs_and_puts_other_pages_beside_their_original(self):
        self.repo.record(("README.es.md", "README.md"), ("docs/start.es.md", "docs/start.md"))
        errors = self.repo.check()
        self.assertIn("docs/translations.json: the translation of docs/start.md lives at docs/es/start.md, "
                      "not docs/start.es.md", errors)

    def test_a_translation_links_the_translation_when_there_is_one(self):
        self.repo.write("README.es.md", "# Proyecto\n\n[English](README.md) · **Español**\n\n"
                                        "Mira [cómo empezar](docs/start.md) y el [diseño](docs/design.md) (en inglés).\n")
        self.repo.record(("README.es.md", "README.md"), ("docs/es/start.md", "docs/start.md"))
        self.assertEqual(self.repo.check(),
                         ["README.es.md: links docs/start.md, which has a translation: docs/es/start.md"])

    def test_an_english_page_is_said_to_be_in_english_in_the_same_paragraph_or_item(self):
        self.repo.write("README.es.md", "# Proyecto\n\n[English](README.md) · **Español**\n\n"
                                        "- [Cómo empezar](docs/es/start.md#1-qué-necesitas)\n"
                                        "- [Diseño](docs/design.md)\n- Otra cosa (en inglés)\n")
        self.repo.record(("README.es.md", "README.md"), ("docs/es/start.md", "docs/start.md"))
        self.assertEqual(self.repo.check(),
                         ["README.es.md: links the English page docs/design.md without saying «en inglés»"])

    def test_the_english_mark_may_wrap_across_lines(self):
        self.repo.write("README.es.md", "# Proyecto\n\n[English](README.md) · **Español**\n\n"
                                        "Mira [cómo empezar](docs/es/start.md) y el [diseño](docs/design.md), en\n"
                                        "inglés.\n")
        self.repo.record(("README.es.md", "README.md"), ("docs/es/start.md", "docs/start.md"))
        self.assertEqual(self.repo.check(), [])

    def test_a_missing_anchor_is_found_in_either_language(self):
        self.repo.write("README.es.md", "# Proyecto\n\n[English](README.md) · **Español**\n\n"
                                        "Mira [cómo empezar](docs/es/start.md#1-que-necesitas) y el "
                                        "[diseño](docs/design.md) (en inglés).\n")
        self.repo.record(("README.es.md", "README.md"), ("docs/es/start.md", "docs/start.md"))
        self.assertEqual(self.repo.check(), ["README.es.md: no heading for docs/es/start.md#1-que-necesitas"])

    def test_the_repository_itself_passes(self):
        errors, documents, translations = foundation.check(ROOT)
        self.assertEqual(errors, [])
        self.assertGreater(documents, 20)


if __name__ == "__main__":
    unittest.main()
