from __future__ import annotations

import io
import unittest

import context  # noqa: F401
from xiangqi_solver import ProofAssistedUciEngine
from xiangqi_solver.uci_loop import _parse_setoption, run_uci_loop


class ProofUciSetOptionTests(unittest.TestCase):
    def test_parse_setoption_allows_button_option_without_value(self) -> None:
        name, value = _parse_setoption("setoption name Clear Hash")

        self.assertEqual(name, "Clear Hash")
        self.assertIsNone(value)

    def test_unknown_button_option_is_ignored_without_error(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("setoption name Clear Hash\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertNotIn("setoption error", output.getvalue())

    def test_known_spin_option_without_value_reports_error(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("setoption name MaxPly\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertIn("info string setoption error: MaxPly requires a value", output.getvalue())

    def test_ponder_option_rejects_invalid_boolean_value(self) -> None:
        output = io.StringIO()

        result = run_uci_loop(
            ProofAssistedUciEngine(),
            input_stream=io.StringIO("setoption name Ponder value maybe\nquit\n"),
            output_stream=output,
        )

        self.assertEqual(result, 0)
        self.assertIn("info string setoption error: expected boolean value", output.getvalue())


if __name__ == "__main__":
    unittest.main()
