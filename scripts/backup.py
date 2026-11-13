"""Sao lưu / khôi phục cho NGƯỜI QUẢN TRỊ máy chủ (người dùng thường dùng Cài đặt → Sao lưu trên web).

  python scripts/backup.py dump [--out private/backups]          # pg_dump toàn bộ CSDL (định dạng custom, nén)
  python scripts/backup.py accounts [--out private/backups]      # mỗi tài khoản 1 file .zip (giống nút trên web)
  python scripts/backup.py verify <file.zip>                     # kiểm tra file sao lưu tài khoản
  python scripts/backup.py restore-dump <file.dump> --target <postgresql://admin@host/db_moi_trong>
                                                                  # khôi phục toàn bộ vào CSDL MỚI, sau đó chạy migrate.py

Biến môi trường: ANHU_ADMIN_DATABASE_URL (bắt buộc với dump/accounts). Tìm pg_dump/pg_restore theo PG_BIN, PATH,
hoặc bản đi kèm gói pgserver (môi trường dev). Thư mục private/ không bao giờ được commit.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _s in (sys.stdout, sys.stderr):  # console Windows (cp1252) không in được tiếng Việt
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT))


def pg_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    if os.environ.get("PG_BIN"):
        p = Path(os.environ["PG_BIN"]) / exe
        if p.exists():
            return str(p)
    found = shutil.which(name)
    if found:
        return found
    try:
        import pgserver
        p = Path(pgserver.__file__).parent / "pginstall" / "bin" / exe
        if p.exists():
            return str(p)
    except ImportError:
        pass
    raise SystemExit(f"Không tìm thấy {name}. Cài PostgreSQL client hoặc đặt PG_BIN.")


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def dump(admin_url: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"anhu-toan-bo-{datetime.now():%Y%m%d-%H%M%S}.dump"
    subprocess.run([pg_tool("pg_dump"), "--format=custom", "--no-owner", "--no-privileges", "--file", str(out), _libpq(admin_url)],
                   check=True)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    (out.with_suffix(".sha256")).write_text(f"{sha}  {out.name}\n", encoding="utf-8")
    return out


def restore_dump(dump_file: Path, target_url: str):
    """Khôi phục vào CSDL đích TRỐNG. Quyền của role anhu_app được cấp lại bằng scripts/migrate.py sau đó."""
    sha_file = dump_file.with_suffix(".sha256")
    if sha_file.exists():
        want = sha_file.read_text(encoding="utf-8").split()[0]
        if hashlib.sha256(dump_file.read_bytes()).hexdigest() != want:
            raise SystemExit("File dump không khớp mã băm — dừng.")
    subprocess.run([pg_tool("pg_restore"), "--no-owner", "--no-privileges", "--exit-on-error", "--single-transaction",
                    "--dbname", _libpq(target_url), str(dump_file)], check=True)


def accounts(admin_url: str, out_dir: Path) -> list[Path]:
    from anhu.db import rows, tx
    from anhu.services import backup
    out_dir.mkdir(parents=True, exist_ok=True)
    with tx(admin_url) as c:
        users = rows(c, "SELECT id, email FROM app_users ORDER BY created_at")
    files = []
    for u in users:
        with tx(admin_url, str(u["id"]), "sao lưu định kỳ (quản trị)") as c:
            data, name, _ = backup.export_backup(c, str(u["id"]), None)
        p = out_dir / f"{u['email'].split('@')[0]}-{name}"
        p.write_bytes(data)
        files.append(p)
    return files


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / "private" / "backups"
    if cmd == "dump":
        print("Đã ghi", dump(os.environ["ANHU_ADMIN_DATABASE_URL"], out))
    elif cmd == "accounts":
        for p in accounts(os.environ["ANHU_ADMIN_DATABASE_URL"], out):
            print("Đã ghi", p)
    elif cmd == "verify":
        from anhu.services.backup import read_backup
        mf, _ = read_backup(Path(argv[2]).read_bytes())
        print("Hợp lệ:", mf["owner"], mf["created_at"], {k: v for k, v in mf["counts"].items() if v})
    elif cmd == "restore-dump":
        restore_dump(Path(argv[2]), argv[argv.index("--target") + 1])
        print("Đã khôi phục. Bước tiếp theo: chạy scripts/migrate.py với ANHU_ADMIN_DATABASE_URL trỏ tới CSDL mới.")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
