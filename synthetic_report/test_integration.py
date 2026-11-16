"""Real Journal code + entirely fabricated fixture; no real race data."""
import sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'synthetic_flow'))
from persistent import Journal
from test_persistent import fixture
from demo import Bridge, attempts
import report

class Integration(unittest.TestCase):
    def test_prediction_then_result_then_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.sqlite'
            request, result = fixture()
            self.assertEqual(Journal(path).apply('SYN01', request)['state'], 'committed')
            before = path.read_bytes()
            predictions, comparisons = report.load(path)
            self.assertEqual(len(predictions), 1)
            self.assertEqual(comparisons, {})
            self.assertIn('結果未比較', report.render(predictions, comparisons))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(Journal(path).apply('SYN01', request, result)['events'], 2)
            self.assertEqual(Journal(path).apply('SYN01', request, result)['state'], 'exists')
            before = path.read_bytes()
            with patch.object(Bridge, 'save', side_effect=AssertionError('no recomputation')), patch.object(Bridge, 'compare', side_effect=AssertionError('no comparison')):
                predictions, comparisons = report.load(path)
                html = report.render(predictions, comparisons)
            self.assertEqual(len(predictions), 1)
            self.assertEqual(len(comparisons), 1)
            self.assertEqual(next(iter(comparisons.values()))['candidate_rank'], 1)
            self.assertIn('架空結果との候補順位：1', html)
            self.assertNotIn('fabricated', html)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(attempts, [])

if __name__ == '__main__': unittest.main()
