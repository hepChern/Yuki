"""Unit tests for Yuki.utils.logging_config channel filtering."""
import contextlib
import io
import logging
import os
import shutil
import tempfile
import unittest

from Yuki.utils.logging_config import (
    DEFAULT_CHANNELS,
    load_logging_config,
    apply_channel_levels,
)


def _write_config(dir_path, content):
    """Write a logging.yaml into dir_path."""
    with open(os.path.join(dir_path, "logging.yaml"), "w",
              encoding="utf-8") as fh:
        fh.write(content)


def _channel_loggers():
    """The four channel loggers used by apply_channel_levels."""
    return ("Yuki.workflow", "Yuki.execution", "Yuki.kernel", "paramiko")


class TestLoggingConfig(unittest.TestCase):
    """Test cases for the logging channel config."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._saved_levels = {name: logging.getLogger(name).level
                              for name in _channel_loggers()}

    def tearDown(self):
        for name, level in self._saved_levels.items():
            logging.getLogger(name).setLevel(level)
        shutil.rmtree(self.tmpdir)

    # ------------------------------------------------------------------
    # load_logging_config
    # ------------------------------------------------------------------

    def test_defaults_when_config_missing(self):
        """Missing logging.yaml means every channel is on."""
        result = load_logging_config(yuki_dir=self.tmpdir)
        self.assertEqual(result, DEFAULT_CHANNELS)
        self.assertTrue(all(result.values()))

    def test_channel_off_is_parsed(self):
        """A channel set to false in the yaml comes back false."""
        _write_config(self.tmpdir, (
            "channels:\n"
            "  workflow: false\n"
            "  paramiko: false\n"))
        result = load_logging_config(yuki_dir=self.tmpdir)
        self.assertFalse(result["workflow"])
        self.assertFalse(result["paramiko"])
        self.assertTrue(result["execution"])
        self.assertTrue(result["kernel"])

    def test_malformed_yaml_falls_back_to_defaults(self):
        """Unparseable yaml falls back to all channels on."""
        _write_config(self.tmpdir, "channels: [not: valid")
        result = load_logging_config(yuki_dir=self.tmpdir)
        self.assertEqual(result, DEFAULT_CHANNELS)

    def test_empty_file_falls_back_to_defaults(self):
        """An empty yaml file falls back to all channels on."""
        _write_config(self.tmpdir, "")
        result = load_logging_config(yuki_dir=self.tmpdir)
        self.assertEqual(result, DEFAULT_CHANNELS)

    def test_non_dict_yaml_falls_back_to_defaults(self):
        """A yaml file that is not a mapping falls back to defaults."""
        _write_config(self.tmpdir, "42")
        result = load_logging_config(yuki_dir=self.tmpdir)
        self.assertEqual(result, DEFAULT_CHANNELS)

    def test_unknown_channels_are_ignored(self):
        """Channels outside the known set do not leak into the result."""
        _write_config(self.tmpdir, (
            "channels:\n"
            "  workflow: false\n"
            "  mystery: false\n"))
        result = load_logging_config(yuki_dir=self.tmpdir)
        self.assertFalse(result["workflow"])
        self.assertNotIn("mystery", result)

    # ------------------------------------------------------------------
    # apply_channel_levels
    # ------------------------------------------------------------------

    def test_apply_sets_logger_levels_for_explicit_config(self):
        """Channel flags map to logger levels."""
        channels = dict(DEFAULT_CHANNELS)
        channels["workflow"] = False
        channels["paramiko"] = False
        apply_channel_levels(channels)
        self.assertEqual(logging.getLogger("Yuki.workflow").level,
                         logging.CRITICAL)
        self.assertEqual(logging.getLogger("Yuki.execution").level,
                         logging.DEBUG)
        self.assertEqual(logging.getLogger("Yuki.kernel").level,
                         logging.DEBUG)
        self.assertEqual(logging.getLogger("paramiko").level,
                         logging.WARNING)

    def test_apply_reads_yaml_when_given_none(self):
        """apply_channel_levels() without arguments reads ~/.Yuki yaml."""
        _write_config(self.tmpdir, "channels:\n  kernel: false\n")
        saved_yukidir = os.environ.get("YUKIDIR")
        os.environ["YUKIDIR"] = self.tmpdir
        try:
            apply_channel_levels()
        finally:
            if saved_yukidir is None:
                del os.environ["YUKIDIR"]
            else:
                os.environ["YUKIDIR"] = saved_yukidir
        self.assertEqual(logging.getLogger("Yuki.kernel").level,
                         logging.CRITICAL)
        self.assertEqual(logging.getLogger("Yuki.workflow").level,
                         logging.INFO)

    def test_apply_attaches_a_single_handler_per_channel(self):
        """Each channel logger gets one stream handler; repeated applies
        do not stack handlers."""
        apply_channel_levels(DEFAULT_CHANNELS)
        apply_channel_levels(DEFAULT_CHANNELS)
        for name in _channel_loggers():
            channel = logging.getLogger(name)
            handlers = [h for h in channel.handlers
                        if isinstance(h, logging.StreamHandler)]
            self.assertEqual(len(handlers), 1, f"logger {name}")

    def test_enabled_channel_renders_to_stdout(self):
        """A channel that is on emits its records to stdout."""
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            apply_channel_levels(DEFAULT_CHANNELS)
            logging.getLogger("Yuki.workflow").info("rendered line")
        self.assertIn("rendered line", captured.getvalue())

    def test_disabled_channel_renders_nothing(self):
        """A channel that is off emits nothing to stdout."""
        channels = dict(DEFAULT_CHANNELS)
        channels["workflow"] = False
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            apply_channel_levels(channels)
            logging.getLogger("Yuki.workflow").info("hidden line")
        self.assertNotIn("hidden line", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
