from unittest import mock

from Yuki.server.routes import workflow as wf_routes
from Yuki.server.routes import execution as ex_routes


def test_collect_files_route_passes_spec():
    app = _app(wf_routes.bp)
    with mock.patch.object(wf_routes, "ImpressionStorage") as S:
        inst = S.return_value
        inst.collect_files.return_value = {
            "runner": {"collected": [], "skipped": [], "failed": []}
        }
        c = app.test_client()
        r = c.get("/collect-files/proj/imp?kind=stageout&pattern=*.root")
        assert r.status_code == 200
        assert r.get_json()["*.root"]["runner"]["collected"] == []
        inst.collect_files.assert_called_once_with("stageout", "*.root")


def test_collect_files_route_type_keyword():
    app = _app(wf_routes.bp)
    with mock.patch.object(wf_routes, "ImpressionStorage") as S:
        c = app.test_client()
        c.get("/collect-files/proj/imp?type=plots")
        S.return_value.collect_files.assert_called_once_with("stageout", "plots")


def test_file_status_route_returns_json():
    app = _app(ex_routes.bp)
    with mock.patch.object(ex_routes, "ImpressionStorage") as S:
        S.return_value.file_status.return_value = [
            {"name": "mass.png", "size": 3, "type": "plot",
             "in_runner": True, "in_yuki": True}]
        c = app.test_client()
        r = c.get("/file-status/proj/imp/runner?kind=stageout")
        assert r.status_code == 200
        assert r.get_json()[0]["name"] == "mass.png"


def _app(bp):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app
