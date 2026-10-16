"""Mizani app factory."""
import os
import time as _time

# Single-country deployment: everything runs on East Africa Time.
os.environ.setdefault("TZ", "Africa/Nairobi")
try:
    _time.tzset()
except Exception:
    pass

from flask import Flask
from flask_login import LoginManager
from .models import db, User
from . import daraja, camera

login_manager = LoginManager()
login_manager.login_view = "main.login"


@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))


def create_app():
    app = Flask(__name__)
    app.config.from_pyfile(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "config.py"))
    db.init_app(app)
    login_manager.init_app(app)
    daraja.init(app)
    camera.init(app)

    from .routes import bp
    app.register_blueprint(bp)

    @app.context_processor
    def inject():
        import time
        from flask_login import current_user
        from .models import get_setting
        return dict(current_user=current_user,
                    daraja_demo=app.config.get("DARAJA_DEMO", True),
                    now_ts=time.time, get_setting=get_setting)

    with app.app_context():
        db.create_all()
        from .seed import seed
        seed()
    return app
