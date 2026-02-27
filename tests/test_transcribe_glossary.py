import unittest

from mfaren import transcribe_glossary as tgloss


class TestTranscribeGlossary(unittest.TestCase):
    def test_parse_and_apply_glossary(self):
        rules = tgloss.parse_glossary("morrer => Kovir\nfaren ravirar -> Farenravirar")
        self.assertEqual(len(rules), 2)
        out = tgloss.apply_glossary("o ambiente de morrer para faren ravirar", rules)
        self.assertIn("Kovir", out)
        self.assertIn("Farenravirar", out)

    def test_ignore_invalid_rows(self):
        rules = tgloss.parse_glossary("linha sem seta\n# comentario\n")
        self.assertEqual(rules, [])

    def test_known_terms_accept_plain_lines(self):
        raw = "morrer => Kovir\nMahakam\nKaedwen"
        terms = tgloss.parse_known_terms(raw)
        self.assertIn("Kovir", terms)
        self.assertIn("Mahakam", terms)
        self.assertIn("Kaedwen", terms)


if __name__ == "__main__":
    unittest.main()
