"""Buyruq satri yordamchisi (server skriptlari uchun).

  python -m app.cli create-project Eduziyo     # loyiha yaratadi (bor bo'lsa kalitini YANGILAYDI), oxirgi qatorda KEY=sk_sms_...
  python -m app.cli set-setting rate_per_minute 0
"""
import sys

from .database import SessionLocal, init_db
from .models import Project
from .security import new_api_key
from .services.settings_store import SETTING_DEFS, set_setting


def create_project(name: str) -> int:
    init_db()
    key, key_hash, prefix = new_api_key()
    with SessionLocal() as db:
        p = db.query(Project).filter(Project.name == name).first()
        if p is None:
            p = Project(name=name, api_key_hash=key_hash, key_prefix=prefix)
            db.add(p)
            print(f"Loyiha yaratildi: {name}")
        else:
            p.api_key_hash, p.key_prefix, p.is_active = key_hash, prefix, True
            print(f"Loyiha bor edi: {name} (API kaliti yangilandi, eskisi ishlamaydi)")
        db.commit()
    print(f"KEY={key}")
    return 0


def set_one(key: str, value: str) -> int:
    kinds = {k: kind for k, kind, *_ in SETTING_DEFS}
    if key not in kinds:
        print(f"Noma'lum sozlama: {key}. Mavjud: {', '.join(kinds)}")
        return 2
    init_db()
    with SessionLocal() as db:
        set_setting(db, key, value if kinds[key] != "bool" else value.lower() in ("1", "true", "on", "yes"))
        db.commit()
    print(f"{key} = {value}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "create-project":
        return create_project(argv[2].strip())
    if len(argv) == 4 and argv[1] == "set-setting":
        return set_one(argv[2], argv[3])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
