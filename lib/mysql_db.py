"""Central MySQL connection for shared tables (users, audit_log).

Configured by `settings.database_url` (`REGIN_DATABASE_URL`), e.g.
`mysql://user:password@host:3306/regin` or `mysql+pymysql://…`.
"""

import pymysql
import pymysql.cursors

from lib.settings import settings

_URL_PREFIXES = ("mysql+pymysql://", "mysql://")


def _parse_database_url(url: str) -> dict:
    """Parse mysql[+pymysql]://user:pass@host:port/dbname into connection kwargs."""
    for prefix in _URL_PREFIXES:
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    else:
        raise ValueError(f"database_url must start with one of {_URL_PREFIXES}")
    user_pass, rest = url.split('@', 1)
    user, password = user_pass.split(':', 1) if ':' in user_pass else (user_pass, '')
    host_port, dbname = rest.split('/', 1) if '/' in rest else (rest, 'regin')
    host, port = host_port.split(':', 1) if ':' in host_port else (host_port, '3306')
    return dict(host=host, port=int(port), user=user, password=password, database=dbname)


def _get_config() -> dict:
    """Build MySQL connection kwargs from `settings.database_url`."""
    if not settings.database_url:
        raise RuntimeError("settings.database_url is not set")
    return _parse_database_url(settings.database_url)


def get_mysql_connection():
    """Get a MySQL connection with dict cursor."""
    cfg = _get_config()
    return pymysql.connect(
        **cfg,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        charset='utf8mb4',
    )


def is_configured() -> bool:
    """Check if a MySQL connection URL is configured."""
    return bool(settings.database_url)


def init_mysql():
    """Create the users and audit_log tables in MySQL if they don't exist."""
    conn = get_mysql_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            INT AUTO_INCREMENT PRIMARY KEY,
                    username      VARCHAR(100) NOT NULL UNIQUE,
                    display_name  VARCHAR(200) NOT NULL,
                    email         VARCHAR(200),
                    password_hash VARCHAR(500) NOT NULL,
                    role          VARCHAR(20) NOT NULL DEFAULT 'editor',
                    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_login    DATETIME
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    user_id    INT,
                    username   VARCHAR(100) NOT NULL,
                    action     VARCHAR(100) NOT NULL,
                    target     VARCHAR(500) NOT NULL,
                    detail     TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_audit_user (username),
                    INDEX idx_audit_created (created_at),
                    INDEX idx_audit_action (action)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
        conn.commit()
    finally:
        conn.close()
