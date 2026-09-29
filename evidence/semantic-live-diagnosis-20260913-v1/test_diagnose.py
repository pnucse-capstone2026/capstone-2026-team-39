import unittest
import diagnose as d


class SpanDiagnosticTests(unittest.TestCase):
    def test_wrong_end_detected_but_unique_quote_found(self):
        result = d.span_check('2026 금정 청년 구직응원 패키지에서 문의', 0, 15, '2026 금정 청년 구직응원 패키지')
        self.assertFalse(result['valid'])
        self.assertEqual(result['exact_matches'], [{'start': 0, 'end': 19}])

    def test_duplicate_quote_not_silently_assigned(self):
        result = d.span_check('매월 지급, 매월 정산', 0, 2, '매월')
        self.assertTrue(result['valid'])
        self.assertFalse(result['unique_exact_quote'])
        self.assertEqual(result['quote_occurrences'], 2)

    def test_nonexistent_quote_not_normalized(self):
        result = d.span_check('10만 원', 0, 4, '10만원')
        self.assertFalse(result['valid'])
        self.assertEqual(result['exact_matches'], [])

    def test_unicode_and_invalid_offset_type(self):
        self.assertTrue(d.span_check('😀문자', 1, 3, '문자')['valid'])
        self.assertFalse(d.span_check('😀문자', True, 3, '문자')['valid'])


if __name__ == '__main__':
    unittest.main()
