"""Classification of job output files into 'plot' vs 'data', plus a
selection-spec predicate builder. Single source of truth for what counts
as a plot, shared by collect filtering, booking filtering, and status views.
"""
import fnmatch
import os

PLOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".svg", ".webp", ".eps"}


def is_plot(filename):
    """True when the file's extension marks it as a plot/image."""
    return os.path.splitext(filename)[1].lower() in PLOT_EXTENSIONS


def classify(filename):
    """Return 'plot' or 'data' for a stageout filename."""
    return "plot" if is_plot(filename) else "data"


def make_predicate(spec):
    """Build a basename predicate from a selection spec.

    spec is one of: 'plots', 'data', 'all', a glob pattern, or a literal
    filename. Returns a function (basename) -> bool.
    """
    if spec in ("all", "", None):
        return lambda name: True
    if spec == "plots":
        return is_plot
    if spec == "data":
        return lambda name: not is_plot(name)
    if any(ch in spec for ch in "*?["):
        return lambda name: fnmatch.fnmatch(name, spec)
    return lambda name: name == spec
