"""Channel-gated logging configuration for Yuki.

Reads ~/.Yuki/logging.yaml (honouring $YUKIDIR) and maps per-channel
on/off flags onto Python logging levels. The file looks like:

    channels:
      workflow: true    # VWorkflow phase logs ([SSH] ..., [LOCAL] ...)
      execution: false  # route debug prints (# >>> execute ...)
      kernel: false     # container_job timing prints, tasks.py prints
      paramiko: false   # ssh transport chatter

A missing or malformed file means every channel is on.
"""
import logging
import os
import sys

import yaml

DEFAULT_CHANNELS = {
    "workflow": True,
    "execution": True,
    "kernel": True,
    "paramiko": True,
}

# channel name -> (logger name, level when the channel is on)
CHANNEL_LOGGERS = {
    "workflow": ("Yuki.workflow", logging.INFO),
    "execution": ("Yuki.execution", logging.DEBUG),
    "kernel": ("Yuki.kernel", logging.DEBUG),
    "paramiko": ("paramiko", logging.INFO),
}


def _yuki_dir():
    """Yuki data root ($YUKIDIR or ~/.Yuki)."""
    return os.path.expanduser(os.environ.get("YUKIDIR", "~/.Yuki"))


def load_logging_config(yuki_dir=None):
    """Return the channel flags as {channel: bool}.

    Defaults to every channel on when the file is missing, empty or
    unparseable. Unknown channel names in the file are ignored.
    """
    path = os.path.join(yuki_dir or _yuki_dir(), "logging.yaml")
    channels = dict(DEFAULT_CHANNELS)
    if not os.path.isfile(path):
        return channels
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return channels
    raw_channels = raw.get("channels", {}) if isinstance(raw, dict) else {}
    if isinstance(raw_channels, dict):
        for name, enabled in raw_channels.items():
            if name in channels:
                channels[name] = bool(enabled)
    return channels


def apply_channel_levels(channels=None):
    """Set the level and rendering of each channel logger from the flags.

    channels defaults to load_logging_config(). A channel that is off
    is set to CRITICAL, except paramiko which keeps WARNING and above
    so genuine connection errors stay visible.

    Each channel logger owns a single stream handler, so records render
    regardless of the process's root logging setup (Flask has no root
    handler; Celery's root sits at the worker loglevel).
    """
    if channels is None:
        channels = load_logging_config()
    for name, enabled in channels.items():
        logger_name, on_level = CHANNEL_LOGGERS[name]
        off_level = logging.WARNING if name == "paramiko" \
            else logging.CRITICAL
        channel = logging.getLogger(logger_name)
        channel.setLevel(on_level if enabled else off_level)
        channel.handlers = [h for h in channel.handlers
                            if not isinstance(h, logging.StreamHandler)]
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s][%(levelname)s] - %(message)s"))
        channel.addHandler(handler)
        channel.propagate = False
