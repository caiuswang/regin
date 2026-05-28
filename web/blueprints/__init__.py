"""Flask blueprints split by domain.

Each blueprint owns one cohesive slice of the HTTP surface (auth, patterns,
hooks, trace, …) so `web/app.py` can stay an orchestration layer rather than
a 3000-line dumping ground.

Usage:
    from web.blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)
"""
