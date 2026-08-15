from .pages import pages_bp
from .stock import stock_bp
from .portfolio import portfolio_bp
from .market import market_bp
from .daemon import daemon_bp


def register_blueprints(app):
    app.register_blueprint(pages_bp)
    app.register_blueprint(stock_bp)
    app.register_blueprint(portfolio_bp)
    app.register_blueprint(market_bp)
    app.register_blueprint(daemon_bp)
