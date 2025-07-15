"""
Flask application setup and configuration.
"""
import sys
import logging
from flask import Flask, render_template, redirect, url_for, request
from logging import getLogger
from .tasks import celeryapp


def create_app():
    """Create and configure Flask application."""
    app = Flask(__name__)
    
    # Configure logging
    logger = getLogger("YukiLogger")
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[%(asctime)s][%(levelname)s] - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    
    # Flask configuration
    app.config['SECRET_KEY'] = 'top-secret!'
    app.config['CELERY_broker_url'] = 'amqp://localhost'
    app.config['result_backend'] = 'rpc://'
    
    # Update celery configuration
    celeryapp.conf.update(app.config)
    
    # Register blueprints
    from .routes import upload, execution, status, runner, workflow
    app.register_blueprint(upload.bp)
    app.register_blueprint(execution.bp)
    app.register_blueprint(status.bp)
    app.register_blueprint(runner.bp)
    app.register_blueprint(workflow.bp)
    
    # Main index route
    @app.route('/', methods=['GET', 'POST'])
    def index():
        if request.method == 'GET':
            impressions = []
            return render_template('index.html', impressions=impressions)
        return redirect(url_for('index'))
    
    return app


# Create app instance
app = create_app()
