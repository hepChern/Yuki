"""Tests for the error-log route's offset support."""
from unittest import mock

from Yuki.server.routes import status as status_routes


def _app():
    """Create a minimal Flask app with the status blueprint."""
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(status_routes.bp)
    return app


def _mock_job():
    """Return a mock VJob that echoes the index and offset it received."""
    job = mock.MagicMock()
    job.log.side_effect = lambda index, offset=0: f"index={index},offset={offset}"
    return job


def test_error_log_returns_full_content_by_default():
    """The route returns the full log when no offset is requested."""
    with mock.patch.object(status_routes, "VJob") as vjob:
        vjob.return_value = _mock_job()
        r = _app().test_client().get("/error-log/proj/imp-1/0")
    assert r.status_code == 200
    assert r.data == b"index=0,offset=0"


def test_error_log_passes_offset_query_param():
    """The route forwards the ?offset query parameter to VJob.log()."""
    with mock.patch.object(status_routes, "VJob") as vjob:
        vjob.return_value = _mock_job()
        r = _app().test_client().get("/error-log/proj/imp-1/3?offset=42")
    assert r.status_code == 200
    assert r.data == b"index=3,offset=42"


def test_error_log_returns_204_when_empty():
    """The route returns 204 with an empty body when there is no log content."""
    with mock.patch.object(status_routes, "VJob") as vjob:
        job = mock.MagicMock()
        job.log.return_value = ""
        vjob.return_value = job
        r = _app().test_client().get("/error-log/proj/imp-1/0")
    assert r.status_code == 204
    assert r.data == b""
