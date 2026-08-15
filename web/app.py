import os
import sys
from flask import Flask

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

import config


def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config['JSON_AS_ASCII'] = False
    app.config['SECRET_KEY'] = config.FLASK_SECRET_KEY

    import stock_master
    stock_master.ensure_updated()

    from web.routes import register_blueprints
    register_blueprints(app)
    return app


app = create_app()


if __name__ == "__main__":
    print("✅ 웹 대시보드 시작: http://localhost:8081")
    app.run(host="0.0.0.0", port=8081, debug=True)
