import unittest

from protogenos_installer.logshare import log_tail


class LogTailTests(unittest.TestCase):
    def test_small_log_is_sent_whole(self) -> None:
        self.assertEqual(log_tail(["one", "two"]), b"one\ntwo\n")

    def test_large_log_keeps_whole_lines_from_the_end(self) -> None:
        lines = [f"line {number:04d}" for number in range(1000)]
        data = log_tail(lines, max_bytes=100)
        text = data.decode()
        self.assertTrue(text.startswith("[protogenos] log truncated"))
        body = text.splitlines()[1:]
        self.assertEqual(body[-1], "line 0999")
        self.assertTrue(all(line.startswith("line ") and len(line) == 9 for line in body))


if __name__ == "__main__":
    unittest.main()
