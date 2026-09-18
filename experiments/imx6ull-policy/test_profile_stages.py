import copy
import unittest
from profile_stages import distribution, summarize


class ProfileTests(unittest.TestCase):
    def trace(self):
        return dict(format=1, columns=['start_elapsed_s', 'driving', 'policy_id', 'wake_late_ms',
                                      'work_ms', 'thread_cpu_ms', 'stage_wall_ms', 'stage_thread_cpu_ms'],
                    stages=['read', 'infer'], policies=['held', 'walk'], dropped_rows=2, scope='test',
                    rows=[[1, True, 1, 0, 3, 2, [1, 2], [0, 2]],
                          [6, True, 1, 18, 3, 2, [1, 2], [0, 2]],
                          [7, True, 1, 0, 21, None, [1, 20], None],
                          [8, False, 0, 0, 1, 0, [1, 0], [0, 0]]])

    def test_filter_partition_and_same_frame_deadline(self):
        summary = summarize(self.trace())
        self.assertEqual(summary['steady']['count'], 2)
        self.assertEqual(summary['steady']['work_over_period'], 1)
        self.assertEqual(summary['steady']['scheduled_finish_over_period'], 2)
        self.assertEqual(summary['steady']['missing_cpu_rows'], 1)
        self.assertEqual(summary['steady']['thread_cpu_ms']['mean'], 2)
        self.assertEqual(summary['dropped_rows'], 2)
        self.assertEqual(summary['by_policy']['walk']['stages']['infer']['wall_ms']['mean'], 11)

    def test_invalid_partition_rejected(self):
        trace = copy.deepcopy(self.trace())
        trace['rows'][0][4] = 999
        with self.assertRaises(ValueError):
            summarize(trace)

    def test_empty_capture_rejected(self):
        trace = self.trace()
        trace['rows'] = []
        with self.assertRaises(ValueError):
            summarize(trace)

    def test_nearest_rank_keeps_tail(self):
        self.assertEqual(distribution([1, 2, 100])['p99'], 100)
        self.assertIsNone(distribution([]))


if __name__ == '__main__':
    unittest.main()
