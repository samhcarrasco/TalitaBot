"""Tests for src/utils/run_summary.py"""

from unittest.mock import MagicMock, patch

from src.utils.run_summary import build_run_summary, show_summary_popup


class TestBuildRunSummary:
    def test_includes_all_metrics(self):
        body = build_run_summary(
            {
                "applied": 30,
                "skipped": 13,
                "failed": 1,
                "discovered": 99,
                "processed": 44,
                "duration": "1h 04m",
                "reason": "Reached max applications (30)",
            }
        )
        assert "Applied" in body and "30" in body
        assert "Skipped" in body and "13" in body
        assert "Failed" in body and "1" in body
        assert "Discovered" in body and "99" in body
        assert "Processed" in body and "44" in body
        assert "1h 04m" in body
        assert "Reached max applications (30)" in body

    def test_defaults_to_zero_when_missing(self):
        body = build_run_summary({})
        assert "Applied    : 0" in body

    def test_omits_optional_lines_when_absent(self):
        body = build_run_summary({"applied": 1})
        assert "Duration" not in body
        assert "Result" not in body


class TestShowSummaryPopup:
    def test_noop_on_non_windows(self):
        with patch("src.utils.run_summary.sys") as mock_sys:
            mock_sys.platform = "linux"
            with patch("src.utils.run_summary.threading.Thread") as mock_thread:
                show_summary_popup("Title", "Body")
        mock_thread.assert_not_called()

    def test_starts_thread_on_windows(self):
        with patch("src.utils.run_summary.sys") as mock_sys:
            mock_sys.platform = "win32"
            with patch("src.utils.run_summary.threading.Thread") as mock_thread:
                instance = MagicMock()
                mock_thread.return_value = instance
                show_summary_popup("Title", "Body")
        mock_thread.assert_called_once()
        instance.start.assert_called_once()

    def test_swallows_thread_start_failure(self):
        with patch("src.utils.run_summary.sys") as mock_sys:
            mock_sys.platform = "win32"
            with patch(
                "src.utils.run_summary.threading.Thread",
                side_effect=RuntimeError("no threads"),
            ):
                # Must not raise.
                show_summary_popup("Title", "Body")
