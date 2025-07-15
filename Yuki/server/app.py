"""
Flask application setup and configuration.
"""
import sys
import logging
from logging import getLogger

from flask import Flask, render_template, redirect, url_for, request

from .tasks import celeryapp
from .routes import upload, execution, status, runner, workflow


def create_app():
    """Create and configure Flask application."""
    flask_app = Flask(__name__)

    # Configure logging
    logger = getLogger("YukiLogger")
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[%(asctime)s][%(levelname)s] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    # Flask configuration
    flask_app.config['SECRET_KEY'] = 'top-secret!'
    flask_app.config['CELERY_broker_url'] = 'amqp://localhost'
    flask_app.config['result_backend'] = 'rpc://'

    # Update celery configuration
    celeryapp.conf.update(flask_app.config)

    # Register blueprints
    flask_app.register_blueprint(upload.bp)
    flask_app.register_blueprint(execution.bp)
    flask_app.register_blueprint(status.bp)
    flask_app.register_blueprint(runner.bp)
    flask_app.register_blueprint(workflow.bp)

    # Main index route
    @flask_app.route('/', methods=['GET', 'POST'])
    def index():
        if request.method == 'GET':
            impressions = []
            return render_template('index.html', impressions=impressions)
        return redirect(url_for('index'))

    return flask_app


# Create app instance
app = create_app()
