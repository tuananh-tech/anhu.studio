import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ALLOWED = ["co.a@example.test", "co.b@example.test"]
PW = "mat-khau-thu-nghiem-123"


@pytest.fixture(scope="session")
def pg(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    from sqlalchemy import create_engine, text
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pgdata")), cleanup_mode="stop")
    base = srv.get_uri()  # postgresql://postgres:@127.0.0.1:PORT/postgres
    dbname = "anhu_test_" + uuid.uuid4().hex[:8]
    eng = create_engine(base.replace("postgresql://", "postgresql+psycopg://"), isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f"CREATE DATABASE {dbname}"))
    eng.dispose()
    admin = base.rsplit("/", 1)[0] + "/" + dbname
    from scripts.migrate import migrate
    migrate(admin, "app-pw-test")
    hostport = admin.split("@", 1)[1]
    app_url = f"postgresql://anhu_app:app-pw-test@{hostport}"
    yield {"admin": admin, "app": app_url}
    srv.cleanup()


@pytest.fixture(scope="session")
def users(pg):
    from anhu.db import tx
    from anhu.services.core import create_user
    with tx(pg["admin"]) as c:
        a = create_user(c, ALLOWED[0], "Cô A", PW, ALLOWED)
        b = create_user(c, ALLOWED[1], "Cô B", PW, ALLOWED)
    return {"a": a, "b": b}


@pytest.fixture
def as_user(pg):
    from anhu.db import tx

    def _f(uid, reason=None):
        return tx(pg["app"], uid, reason)
    return _f


@pytest.fixture(scope="session")
def make_db(pg):
    """Tạo một CSDL MỚI đã migrate (mô phỏng máy chủ mới, chưa có tài khoản nào)."""
    from sqlalchemy import create_engine, text
    from scripts.migrate import migrate
    root = pg["admin"].rsplit("/", 1)[0]

    def _f():
        name = "anhu_new_" + uuid.uuid4().hex[:8]
        eng = create_engine(root.replace("postgresql://", "postgresql+psycopg://") + "/postgres", isolation_level="AUTOCOMMIT")
        with eng.connect() as c:
            c.execute(text(f"CREATE DATABASE {name}"))
        eng.dispose()
        adm = f"{root}/{name}"
        migrate(adm, "app-pw-test")
        return {"admin": adm, "app": f"postgresql://anhu_app:app-pw-test@{adm.split('@', 1)[1]}"}
    return _f
