import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from config import Config

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()

def create_app(config_class=Config):
    """
    Creates and configures the Flask application instance.
    """
    app = Flask(__name__,
            instance_relative_config=True,
            static_folder='../static',
            static_url_path=''        
           )
    app.config.from_object(config_class)

    # Ensure the instance folder exists
    try:
        os.makedirs(app.instance_path)
        os.makedirs(app.config['PROJECTS_DATA_DIR'])
    except OSError:
        pass

    # Initialize extensions with the app
    db.init_app(app)
    migrate.init_app(app, db)

    # Register blueprints (routes)
    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    return app
