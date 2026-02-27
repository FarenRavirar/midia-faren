import unittest

from mfaren.util import make_output_name, sanitize_filename


class TestSanitize(unittest.TestCase):
    def test_sanitize_invalid_chars(self):
        name = 'inv:alid*name?'
        sanitized = sanitize_filename(name)
        self.assertNotIn(':', sanitized)
        self.assertNotIn('*', sanitized)
        self.assertNotIn('?', sanitized)

    def test_empty_returns_default(self):
        self.assertEqual(sanitize_filename(''), 'não informado')

    def test_output_name_fallback(self):
        name = make_output_name(None, None)
        self.assertIn('não informado', name)


if __name__ == '__main__':
    unittest.main()
