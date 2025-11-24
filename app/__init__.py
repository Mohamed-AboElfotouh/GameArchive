import os
from flask import Flask
from app.extensions import db, migrate
from config import config
from flask_bootstrap import Bootstrap


def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    app = Flask(__name__)

    # Load configuration
    app.config.from_object(config.get(config_name, config['development']))

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # Register blueprints
    from app.routes.main import main_blueprint
    app.register_blueprint(main_blueprint)

    # Create tables
    with app.app_context():
        db.create_all()

    Bootstrap(app)

    return app
