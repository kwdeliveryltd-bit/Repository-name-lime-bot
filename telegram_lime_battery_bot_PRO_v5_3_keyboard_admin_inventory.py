import asyncio
import json
import math
import os
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise RuntimeError("Brak TELEGRAM_TOKEN w Railway Variables")
DB_FILE = "telegram_db.json"
INVENTORY_FILE = "telegram_inventory.json"
CHARGE_JOBS_FILE = "telegram_charge_jobs.json"
GROUP_FILE = "telegram_group.json"
DRIVER_CHECK_FILE = "telegram_driver_checks.json"
DRIVER_FLOW_FILE = "telegram_driver_flow.json"
WEEKLY_REPORT_FILE = "telegram_weekly_report.json"
DRIVERS_FILE = "telegram_drivers.json"
AUDIT_FILE = "telegram_audit_log.json"
DRIVER_LIMITS_FILE = "telegram_driver_limits.json"

FILE_LOCK = threading.RLock()

# Wpisz tutaj swoje ID z Telegrama po użyciu  komendy: moj id
# Przykład: ADMIN_IDS = {"123456789"}
# UWAGA: pusta lista oznacza BRAK administratorów.
ADMIN_IDS = {"6030936882"}

# RĘCZNA KSIĄŻKA KIEROWCÓW
# Tu możesz wpisać kierowców po zebraniu ich Telegram ID.
# Format:
# DRIVER_ID_BOOK = {
#     "a": {"id": "123456789", "name": "Adam Od Dobosza Lima", "aliases": ["adam", "adam od dobosza"]},
#     "paulina": {"id": "987654321", "name": "Paulinka Moja Księżniczka", "aliases": ["paulina", "paulinka"]},
# }
#
# Możesz też dodawać ich z Telegrama komendą:
# dodaj id a 123456789 Adam Od Dobosza Lima
# dodaj id paulina 987654321 Paulinka Moja Księżniczka
DRIVER_ID_BOOK = {
    "1": {
        "id": "8635659517",
        "name": "Luke",
        "aliases": ["1", "luke", "lp"]
    },
    "2": {
        "id": "6651434498",
        "name": "Michał Surmacz",
        "aliases": ["2", "surmacz", "michal surmacz", "michal s", "ms"]
    },
    "3": {
        "id": "7247279842",
        "name": "Pietrzak",
        "aliases": ["3", "pietrzak", "michal pietrzak", "michal p", "mp"]
    },
    "4": {
        "id": "8006256107",
        "name": "Piter",
        "aliases": ["4", "piter"]
    },
    "5": {
        "id": "6921903873",
        "name": "Paweł LEGIA",
        "aliases": ["5", "pawel", "legia"]
    },
    "6": {
        "id": "",
        "name": "WOLNY",
        "aliases": ["6"]
    },
    "7": {
        "id": "8226089815",
        "name": "Waldek",
        "aliases": ["7", "waldek"]
    },
    "8": {
        "id": "8087250524",
        "name": "Alex",
        "aliases": ["8", "alex"]
    },
    "9": {
        "id": "7733740199",
        "name": "Paulina",
        "aliases": ["9", "paulina"]
    },
    "10": {
        "id": "1051855484",
        "name": "Krzysztof",
        "aliases": ["10", "krzysztof"]
    },
    "11": {
        "id": "8220348868",
        "name": "Paweł J",
        "aliases": ["11", "pawel j"]
    },
    "12": {
        "id": "",
        "name": "WOLNY",
        "aliases": ["12"]
    },
    "13": {
        "id": "6030936882",
        "name": "Kris",
        "aliases": ["13", "kris"]
    },
    "14": {
        "id": "7794225975",
        "name": "Martinez",
        "aliases": ["14", "martinez"]
    }
}
# Pamięć klikniętych przycisków: użytkownik klika akcję, potem wpisuje samą liczbę.
USER_STATE = {}

TZ = ZoneInfo("Europe/London")

BASE_RATE = 2.00
PENALTY_PER_HOUR = 0.10
TIME_LIMIT_HOURS = 6
SMALL_ROUTE_LIMIT_HOURS = 6
SMALL_ROUTE_MAX_QTY = 60
MIN_RATE = 1.00

CHARGE_TIME_HOURS = 4.5
MIN_CHARGE_TIME_HOURS = 3.5
ALARM_BEFORE_MINUTES = 15
LOW_READY_LIMIT = 50  # alarm w status_report jest wyłączony
DRIVER_MAX_BATTERIES = 70
DRIVER_MIN_BATTERIES = 30
DRIVER_LIMIT_STEP = 10
DRIVER_ROUTE_TIME_LIMIT_HOURS = 6
DEFAULT_CHARGER_SLOTS = 175  # domyślna liczba portów ładowania
CHARGER_CAPACITY = DEFAULT_CHARGER_SLOTS  # fallback dla starych fragmentów kodu


def now():
    return datetime.now(TZ)


def normalize_text(text):
    return (
        text.lower()
        .replace("ł", "l")
        .replace("ą", "a")
        .replace("ę", "e")
        .replace("ó", "o")
        .replace("ż", "z")
        .replace("ź", "z")
        .replace("ć", "c")
        .replace("ń", "n")
        .replace("ś", "s")
    )


def trip_time_limit_hours(qty):
    """Każdy kierowca ma 6h na wykonanie trasy."""
    return DRIVER_ROUTE_TIME_LIMIT_HOURS


def load_json(path, default):
    with FILE_LOCK:
        if not os.path.exists(path):
            save_json(path, default)
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Jeśli JSON jest uszkodzony, zachowujemy kopię awaryjną i tworzymy świeży plik.
            try:
                broken_path = f"{path}.broken-{now().strftime('%Y%m%d-%H%M%S')}"
                os.replace(path, broken_path)
            except Exception:
                pass
            save_json(path, default)
            return default


def save_json(path, data):
    # Atomowy zapis zmniejsza ryzyko uszkodzenia JSON przy przerwaniu procesu.
    with FILE_LOCK:
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)


def load_db():
    return load_json(DB_FILE, {"trips": []})


def save_db(db):
    save_json(DB_FILE, db)



def load_driver_limits():
    return load_json(DRIVER_LIMITS_FILE, {"drivers": {}})


def save_driver_limits(data):
    save_json(DRIVER_LIMITS_FILE, data)


def driver_limit_key(user):
    return str(user.id)


def get_driver_battery_limit(user):
    data = load_driver_limits()
    item = data.get("drivers", {}).get(driver_limit_key(user), {})
    try:
        limit = int(item.get("limit", DRIVER_MAX_BATTERIES))
    except Exception:
        limit = DRIVER_MAX_BATTERIES
    return max(DRIVER_MIN_BATTERIES, min(DRIVER_MAX_BATTERIES, limit))


def set_driver_battery_limit(user, limit, reason="manual"):
    limit = max(DRIVER_MIN_BATTERIES, min(DRIVER_MAX_BATTERIES, int(limit)))
    data = load_driver_limits()
    drivers = data.setdefault("drivers", {})
    drivers[driver_limit_key(user)] = {
        "name": get_driver_name(user),
        "limit": limit,
        "reason": reason,
        "updated_at": now().isoformat()
    }
    save_driver_limits(data)
    return limit


def update_driver_limit_after_trip(user, was_late):
    old_limit = get_driver_battery_limit(user)

    if was_late:
        new_limit = max(DRIVER_MIN_BATTERIES, old_limit - DRIVER_LIMIT_STEP)
        reason = "late_penalty"
    else:
        new_limit = min(DRIVER_MAX_BATTERIES, old_limit + DRIVER_LIMIT_STEP)
        reason = "on_time_reward"

    set_driver_battery_limit(user, new_limit, reason)
    return old_limit, new_limit


def driver_limit_status_line(user):
    limit = get_driver_battery_limit(user)
    if limit >= DRIVER_MAX_BATTERIES:
        return f"Limit kierowcy: {limit} baterii ✅"
    return f"Limit kierowcy po karze: {limit} baterii ⚠️"


def driver_limit_change_message(user, was_late):
    old_limit, new_limit = update_driver_limit_after_trip(user, was_late)

    if was_late:
        if new_limit < old_limit:
            return (
                "🚨 KARA LIMITU\n"
                f"Spóźnienie obniża następny limit: {old_limit} → {new_limit} baterii.\n"
                f"Minimum kary: {DRIVER_MIN_BATTERIES}. Żeby wracać do maxa {DRIVER_MAX_BATTERIES}, kierowca musi przywozić na czas.\n"
            )
        return (
            "🚨 KARA LIMITU\n"
            f"Kierowca jest już na minimum: {new_limit} baterii.\n"
            "Żeby podnieść limit, musi przywieźć następną trasę na czas.\n"
        )

    if new_limit > old_limit:
        return (
            "✅ LIMIT PODNIESIONY\n"
            f"Trasa na czas: {old_limit} → {new_limit} baterii.\n"
        )

    return (
        "✅ LIMIT UTRZYMANY\n"
        f"Kierowca ma maksymalny limit: {new_limit} baterii.\n"
    )



def load_inventory():
    return load_json(INVENTORY_FILE, {
        "depot_total": 505,
        "ready": 0,
        "waiting": 0,
        "charging": 0,
        "updated_at": None
    })


def save_inventory(inv):
    inv["updated_at"] = now().isoformat()
    save_json(INVENTORY_FILE, inv)


def load_jobs():
    return load_json(CHARGE_JOBS_FILE, {"jobs": []})


def save_jobs(data):
    save_json(CHARGE_JOBS_FILE, data)


def load_group():
    return load_json(GROUP_FILE, {"chat_id": None})


def save_group(chat_id):
    save_json(GROUP_FILE, {"chat_id": chat_id})


def load_drivers():
    """
    Zwraca zapisanych kierowców:
    - automatycznie zapamiętanych przez bota,
    - ręcznie wpisanych w DRIVER_ID_BOOK w kodzie.
    """
    data = load_json(DRIVERS_FILE, {})

    # Dorzuć ręczną książkę kierowców z kodu.
    for code, item in DRIVER_ID_BOOK.items():
        uid = str(item.get("id", "")).strip()
        if not uid or uid.upper().startswith("WPISZ"):
            continue

        name = item.get("name") or str(code)
        aliases = item.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]

        # Normalnie kluczem jest Telegram ID. Jeśli dwa wpisy mają ten sam Telegram ID
        # (np. osobne numery 1 i 3 na tym samym koncie), robimy unikalny klucz ręczny,
        # żeby wyszukiwarka nie nadpisywała jednego kierowcy drugim.
        data_key = uid
        existing = data.get(data_key, {})
        if existing and existing.get("code") and existing.get("code") != str(code):
            data_key = f"manual:{code}:{uid}"
            existing = data.get(data_key, {})

        data[data_key] = {
            "id": uid,
            "name": existing.get("name") or name,
            "first_name": existing.get("first_name") or name.split()[0],
            "full_name": existing.get("full_name") or name,
            "username": existing.get("username") or "",
            "code": existing.get("code") or str(code),
            "aliases": sorted(set((existing.get("aliases") or []) + aliases + [str(code), name])),
            "manual_book": True
        }

    return data


def save_drivers(data):
    save_json(DRIVERS_FILE, data)


def remember_driver(user):
    """
    Zapamiętuje osobę, która napisała do bota.
    """
    if not user:
        return

    data = load_json(DRIVERS_FILE, {})
    uid = str(user.id)
    old = data.get(uid, {})

    aliases = old.get("aliases") or []
    name = get_driver_name(user)
    for x in [name, user.first_name or "", user.full_name or "", user.username or ""]:
        if x and x not in aliases:
            aliases.append(x)

    data[uid] = {
        "id": uid,
        "name": old.get("name") or name,
        "first_name": user.first_name or old.get("first_name", ""),
        "full_name": user.full_name or old.get("full_name", ""),
        "username": user.username or old.get("username", ""),
        "code": old.get("code", ""),
        "aliases": aliases,
        "manual_book": old.get("manual_book", False)
    }

    save_drivers(data)


def add_driver_id_command(text):
    """
    Admin może dodać kierowcę bez reply, po zebraniu Telegram ID.

    Format:
    dodaj id a 123456789 Adam Od Dobosza Lima
    dodaj kierowce a 123456789 Adam Od Dobosza Lima
    """
    raw = text.strip()
    m = re.search(
        r"^(?:dodaj\s+id|dodaj\s+kierowce|dodaj\s+kierowcę|zapisz\s+kierowce|zapisz\s+kierowcę)\s+(\S+)\s+(\d{4,})\s+(.+?)\s*$",
        raw,
        re.IGNORECASE
    )
    if not m:
        return None

    code = m.group(1).strip()
    uid = m.group(2).strip()
    name = m.group(3).strip()

    data = load_json(DRIVERS_FILE, {})
    old = data.get(uid, {})
    aliases = old.get("aliases") or []

    for x in [code, name, name.split()[0] if name.split() else ""]:
        if x and x not in aliases:
            aliases.append(x)

    data[uid] = {
        "id": uid,
        "name": name,
        "first_name": name.split()[0] if name.split() else name,
        "full_name": name,
        "username": old.get("username", ""),
        "code": code,
        "aliases": aliases,
        "manual_added": True
    }
    save_drivers(data)

    return (
        "✅ Kierowca dodany ręcznie\n\n"
        f"Kod: {code}\n"
        f"Nazwa: {name}\n"
        f"Telegram ID: {uid}\n\n"
        "Od teraz możesz użyć np.:\n"
        f"{code} 55 start 14:52\n"
        f"{name} 55 start 14:52"
    )


def driver_search(query):
    """
    Szuka kierowców po kodzie, jednej literze, imieniu, nazwisku, aliasie albo username.
    """
    q = normalize_text(query).strip().lstrip("@")
    if not q:
        return []

    # Safety guard: two drivers have the same first name.
    # Plain "michal" is ambiguous, so require a surname/code/alias:
    # "pietrzak", "surmacz", "michal p", "michal s", "2", or "3".
    if q in {"michał"}:
        return []

    results = []
    drivers = load_drivers()

    for uid, info in drivers.items():
        aliases = info.get("aliases") or []
        candidates = [
            info.get("code", ""),
            info.get("name", ""),
            info.get("first_name", ""),
            info.get("full_name", ""),
            info.get("username", ""),
            *aliases,
        ]

        normalized_candidates = [normalize_text(str(x)).strip().lstrip("@") for x in candidates if x]
        searchable = " ".join(normalized_candidates)

        # Numery kierowców muszą pasować TYLKO dokładnie.
        # Dzięki temu "4" nie łapie "14", ID zawierającego 4 ani innych przypadkowych tekstów.
        if q.isdigit():
            matched = any(c == q for c in normalized_candidates)
        else:
            # Litery nadal działają elastycznie: kod, początek imienia/nazwy albo alias.
            matched = (
                any(c == q for c in normalized_candidates)
                or any(c.startswith(q) for c in normalized_candidates)
                or q in searchable
            )

        if matched:
            display_name = (
                info.get("name")
                or info.get("full_name")
                or info.get("first_name")
                or info.get("username")
                or uid
            )
            results.append({
                "user_id": str(uid),
                "name": display_name,
                "username": info.get("username", ""),
                "code": info.get("code", "")
            })

    unique = {}
    for item in results:
        unique[item["user_id"]] = item

    return sorted(unique.values(), key=lambda x: normalize_text(x["name"]))


def drivers_list_text():
    drivers = load_drivers()
    if not drivers:
        return (
            "👥 KIEROWCY\n\n"
            "Brak zapisanych kierowców.\n\n"
            "Dodaj ich ręcznie, gdy zbierzesz Telegram ID:\n"
            "dodaj id a 123456789 Adam Od Dobosza Lima\n"
            "dodaj id paulina 987654321 Paulinka Moja Księżniczka"
        )

    lines = ["👥 ZAPISANI KIEROWCY", ""]
    for item in sorted(drivers.values(), key=lambda x: normalize_text(x.get("name", ""))):
        username = f" @{item.get('username')}" if item.get("username") else ""
        code = f" [{item.get('code')}]" if item.get("code") else ""
        aliases = item.get("aliases") or []
        alias_txt = f" | aliasy: {', '.join(aliases[:5])}" if aliases else ""
        lines.append(f"•{code} {item.get('name') or item.get('id')}{username} — ID: {item.get('id')}{alias_txt}")

    return "\n".join(lines)


def create_restored_trip(driver_name, user_id, chat_id, start_time, qty, manual=False):
    db = load_db()
    wanted = normalize_text(driver_name)

    # IMPORTANT:
    # Do NOT remove active trips by fuzzy/contains name matching.
    # Example: "Michal" / "Michal P" / "Michal S" can match the wrong driver.
    # We only replace an active route when Telegram ID is the same, or the full
    # normalized display name is exactly the same.
    db["trips"] = [
        trip for trip in db["trips"]
        if not (
            trip.get("end") is None
            and (
                str(trip.get("user_id", "")) == str(user_id)
                or normalize_text(trip.get("driver", "")) == wanted
            )
        )
    ]

    db["trips"].append({
        "driver": driver_name,
        "user_id": str(user_id),
        "chat_id": chat_id,
        "start": start_time.isoformat(),
        "qty": int(qty),
        "end": None,
        "alert_sent": False,
        "manual": manual,
        "restored": True
    })

    save_db(db)
    deadline = start_time + timedelta(hours=trip_time_limit_hours(qty))

    return (
        f"✅ ODTWORZONO TRASĘ\n\n"
        f"🚗 {driver_name}: {qty} baterii\n"
        f"Telegram ID: {user_id}\n"
        f"Start: {fmt_dt(start_time)}\n"
        f"Deadline: {fmt_dt(deadline)}\n"
        f"📌 Gotowe NIE zostały pomniejszone.\n\n"
        f"{status_report()}"
    )


def handle_restore_driver_choice(text, user, chat_id):
    key = str(user.id)
    state = USER_STATE.get(key)

    if not state or state.get("action") != "choose_restore_driver":
        return None

    if not only_number(text):
        return "Wpisz numer kierowcy z listy albo wpisz: anuluj"

    idx = int(text.strip()) - 1
    matches = state.get("matches", [])

    if idx < 0 or idx >= len(matches):
        return f"❌ Wybierz numer od 1 do {len(matches)}."

    selected = matches[idx]
    USER_STATE.pop(key, None)

    return create_restored_trip(
        selected["name"],
        selected["user_id"],
        chat_id,
        datetime.fromisoformat(state["start"]),
        int(state["qty"]),
        manual=True
    )


def load_driver_checks():
    return load_json(DRIVER_CHECK_FILE, {})


def save_driver_checks(data):
    save_json(DRIVER_CHECK_FILE, data)


def mark_driver_inventory_check(user_id, fields):
    data = load_driver_checks()
    key = str(user_id)
    current = set(data.get(key, []))
    current.update(fields)
    data[key] = sorted(current)
    save_driver_checks(data)


def get_driver_inventory_check(user_id):
    return set(load_driver_checks().get(str(user_id), []))


def reset_driver_inventory_check(user_id):
    data = load_driver_checks()
    key = str(user_id)
    if key in data:
        del data[key]
        save_driver_checks(data)


def inventory_fields_in_text(text):
    t = normalize_text(text)
    fields = set()
    if find_number_near("gotowe|gotowych", t) is not None:
        fields.add("gotowe")
    if find_number_near("ladowarka|ladowarki|w ladowarkach|laduje sie|laduja sie", t) is not None:
        fields.add("ladowarki")
    if find_number_near("oczekuje|oczekuja|oczekujace|oczekujacych", t) is not None:
        fields.add("oczekujace")
    return fields


def missing_inventory_check_text(user_id):
    required = {"gotowe", "ladowarki", "oczekujace"}
    done = get_driver_inventory_check(user_id)
    missing = required - done
    if not missing:
        return None
    names = {"gotowe": "gotowe", "ladowarki": "ładowarki", "oczekujace": "oczekujące"}
    missing_txt = ", ".join(names[x] for x in sorted(missing))
    return (
        "⚠️ ZŁA KOLEJNOŚĆ\n\n"
        "Najpierw podaj aktualny stan magazynu, dopiero potem wpisz/kliknij Zabrane.\n\n"
        f"Brakuje: {missing_txt}\n\n"
        "Przykład:\n"
        "gotowe 195\n"
        "ladowarki 85\n"
        "oczekuje 0\n"
        "zabrane 50"
    )




def display_driver_name(name):
    """Ujednolica wyświetlaną nazwę kierowcy."""
    if not name:
        return name

    clean = str(name).strip()
    normalized = normalize_text(clean)

    mapping = {
        "michal szczepanski": "Tofik",
        "michal od szczepanski": "Tofik",
        "michal od szczepanskiego": "Tofik",
        "michal surmacz": "Surmacz",
        "surmacz": "Surmacz",
        "michal pietrzak": "Pietrzak",
        "pietrzak": "Pietrzak",
    }

    return mapping.get(normalized, clean)



def get_driver_from_book_by_user_id(user_id):
    """Zwraca wpis z DRIVER_ID_BOOK po Telegram ID. To jest źródło prawdy dla kierowcy."""
    uid = str(user_id)
    for key, item in DRIVER_ID_BOOK.items():
        if str(item.get("id", "")).strip() == uid and uid:
            return key, item
    return None, None


def get_driver_name_by_user_id(user):
    """
    Kierowca wysyłający wiadomość jest identyfikowany po Telegram ID.
    Imię/nazwisko wpisane w tekście nie decyduje o tym, kto robi pickup/return.
    """
    key, item = get_driver_from_book_by_user_id(user.id)
    if item:
        return display_driver_name(item.get("name", get_telegram_display_name(user)))
    return display_driver_name(get_telegram_display_name(user))


def get_telegram_display_name(user):
    if getattr(user, "full_name", None):
        return user.full_name
    if getattr(user, "first_name", None):
        return user.first_name
    return str(user.id)



def get_driver_name(user):
    """
    Najważniejsza zasada:
    - jeśli Telegram ID jest w DRIVER_ID_BOOK, używamy nazwy z tego ID,
    - tekst wpisany przez kierowcę NIE wybiera kierowcy,
    - aliasy są tylko dla komend admina / ręcznego wyboru.
    """
    return get_driver_name_by_user_id(user)


def find_number_after(words, text):
    match = re.search(rf"(?:{words})\s+(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def find_number_before(words, text):
    match = re.search(rf"(\d+)\s+(?:{words})", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def find_number_near(words, text):
    """Obsługuje oba formaty: 'oddane 61' i '61 oddane'."""
    qty = find_number_after(words, text)
    if qty is not None:
        return qty
    return find_number_before(words, text)


def is_admin(user):
    # Produkcyjnie wpisz swoje Telegram ID w ADMIN_IDS.
    # Pusta lista ADMIN_IDS oznacza brak administratorów, a nie tryb testowy.
    return bool(user) and str(user.id) in ADMIN_IDS


def get_keyboard(user=None, chat=None):
    """Main Telegram keyboard.

    Everyone sees only the safe driver panel.
    Admin tools stay available by typed commands only.
    """

    base = [
        ["Pickup", "Return"],
        ["🟢 OK"],
    ]

    return ReplyKeyboardMarkup(base, resize_keyboard=True, one_time_keyboard=False)


def get_confirm_keyboard():
    """Confirmation keyboard for Pickup / Return.

    Enterprise-safe:
    - OK zapisuje aktualne wyliczenie,
    - Edit wraca do poprawiania danych,
    - Cancel przerywa cały flow.
    """
    return ReplyKeyboardMarkup(
        [["🟢 OK"], ["✏️ Edit"], ["🔴 Cancel"]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def is_ok_text(t):
    return t in ["zatwierdz", "zatwierdź", "ok", "🟢 ok", "potwierdz", "potwierdź"]


def is_edit_text(t):
    return t in ["edit", "edytuj", "popraw", "popraw dane", "✏️ edit", "✏️ edytuj"]


def is_cancel_text(t):
    return t in ["anuluj", "cancel", "🔴 cancel", "stop"]


def audit_log(event_type, user=None, chat_id=None, details=None):
    """Prosty audit trail do diagnozy rozjazdów człowiek ↔ stan bota."""
    try:
        data = load_json(AUDIT_FILE, {"events": []})
        events = data.setdefault("events", [])
        events.append({
            "time": now().isoformat(),
            "event": event_type,
            "user_id": str(getattr(user, "id", "")) if user else "",
            "user_name": get_driver_name(user) if user else "",
            "chat_id": chat_id,
            "details": details or {},
        })
        # Nie pozwalamy, żeby plik rósł bez końca.
        data["events"] = events[-5000:]
        save_json(AUDIT_FILE, data)
    except Exception:
        # Audit nie może zatrzymać pracy operacyjnej bota.
        pass


def confirmation_block_message():
    return (
        "❌ Nie zapisuję tej wiadomości jako poprawki, bo jesteś na kroku POTWIERDZENIA.\n\n"
        "Żeby uniknąć rozjazdu stanów, po takiej wiadomości blokuję 🟢 OK.\n\n"
        "Kliknij:\n"
        "✏️ Edit — popraw dane\n"
        "🔴 Cancel — przerwij i zacznij od nowa"
    )


BUTTON_ACTIONS = {
    # English buttons
    "pickup": "zabrane",
    "return": "oddane",
    "ready": "gotowe",
    "charging": "ladowarka",
    "waiting": "oczekuja",
    "depot": "depo",

    # Old Polish commands still work
    "zabrane": "zabrane",
    "oddane": "oddane",
    "gotowe": "gotowe",
    "ladowarka": "ladowarka",
    "ladowarki": "ladowarka",
    "oczekuja": "oczekuja",
    "oczekujace": "oczekuja",
    "depo": "depo",
}


def only_number(text):
    return re.fullmatch(r"\s*\d+\s*", text or "") is not None


def admin_text_action(t):
    if t in ["ogloszenie", "ogłoszenie", "announcement"]:
        return "ogloszenie"
    if t == "alert":
        return "alert"
    return None


def fmt_hours(hours):
    total_minutes = int(round(hours * 60))
    return f"{total_minutes // 60}h {total_minutes % 60}min"


def fmt_dt(dt):
    return dt.astimezone(TZ).strftime("%H:%M")


def active_in_transit():
    db = load_db()
    return sum(int(t.get("qty", 0)) for t in db["trips"] if t.get("end") is None)


def active_trip_details():
    db = load_db()
    details = {}

    for trip in db["trips"]:
        if trip.get("end") is None:
            name = display_driver_name(trip.get("driver", "Nieznany"))
            details[name] = details.get(name, 0) + int(trip.get("qty", 0))

    return details



def active_charging_jobs_for_g():
    data = load_jobs()
    jobs = []
    for job in data.get("jobs", []):
        if job.get("status") in ["charging", "alarm_sent"] and not job.get("ready_sent"):
            try:
                qty = int(job.get("qty", 0))
                ready_at = datetime.fromisoformat(job["ready_at"])
                start_at = datetime.fromisoformat(job.get("start_at", job["ready_at"]))
            except Exception:
                continue

            if qty > 0:
                jobs.append((ready_at, start_at, job))

    jobs.sort(key=lambda x: x[0])
    return [job for _, _, job in jobs]


def g_job_elapsed_hours(job):
    try:
        start_at = datetime.fromisoformat(job.get("start_at", job["ready_at"]))
    except Exception:
        return 0
    return max(0, (now() - start_at).total_seconds() / 3600)


def g_job_elapsed_text(job):
    minutes = int(g_job_elapsed_hours(job) * 60)
    return f"{minutes // 60}h {minutes % 60}min"


def g_job_can_be_removed(job):
    return g_job_elapsed_hours(job) >= MIN_CHARGE_TIME_HOURS


def g_time_left_text(ready_at):
    seconds = int((ready_at - now()).total_seconds())
    if seconds <= 0:
        return "✅ gotowe do wyjęcia"
    minutes = (seconds + 59) // 60
    return f"zostało {minutes // 60}h {minutes % 60}min"


def g_min_time_left_text(job):
    try:
        start_at = datetime.fromisoformat(job.get("start_at", job["ready_at"]))
    except Exception:
        return ""
    min_ready_at = start_at + timedelta(hours=MIN_CHARGE_TIME_HOURS)
    seconds = int((min_ready_at - now()).total_seconds())
    if seconds <= 0:
        return ""
    minutes = (seconds + 59) // 60
    return f"minimum za {minutes // 60}h {minutes % 60}min"


def g_charging_report_text():
    jobs = active_charging_jobs_for_g()
    if not jobs:
        return "🔌 Brak aktywnych partii w ładowarkach."

    lines = ["🔌 PARTIE W ŁADOWARKACH:"]
    for job in jobs:
        qty = int(job.get("qty", 0))
        ready_at = datetime.fromisoformat(job["ready_at"])
        left = g_time_left_text(ready_at)
        elapsed = g_job_elapsed_text(job)

        if g_job_can_be_removed(job):
            lines.append(f"• {qty} baterii — {left} — w ładowarce {elapsed}")
        else:
            min_left = g_min_time_left_text(job)
            lines.append(f"• {qty} baterii — {left} — w ładowarce {elapsed} — ⛔ {min_left}")

    lines.append("\nMinimum do wyjęcia przez G: 3h 30min")
    return "\n".join(lines)


def g_nearest_removable_job():
    candidates = []
    for job in active_charging_jobs_for_g():
        if not g_job_can_be_removed(job):
            continue
        try:
            ready_at = datetime.fromisoformat(job["ready_at"])
        except Exception:
            continue
        candidates.append((ready_at, job))

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1] if candidates else None


def g_finish_nearest_batch(chat_id):
    data = load_jobs()
    jobs = data.get("jobs", [])

    candidates = []
    for index, job in enumerate(jobs):
        if job.get("status") in ["charging", "alarm_sent"] and not job.get("ready_sent"):
            try:
                qty = int(job.get("qty", 0))
                ready_at = datetime.fromisoformat(job["ready_at"])
            except Exception:
                continue

            if qty > 0 and g_job_can_be_removed(job):
                candidates.append((ready_at, index, job))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    ready_at, index, job = candidates[0]
    qty = int(job.get("qty", 0))

    inv = load_inventory()
    inv["charging"] = max(0, int(inv.get("charging", 0)) - qty)
    inv["ready"] = int(inv.get("ready", 0)) + qty
    save_inventory(inv)

    jobs[index]["ready_sent"] = True
    jobs[index]["status"] = "done"
    jobs[index]["finished_by_g_at"] = now().isoformat()
    save_jobs(data)

    moved = auto_move_waiting_to_chargers(chat_id)

    return {
        "qty": qty,
        "moved_waiting_to_charging": moved
    }


def start_g_command_flow(user, chat_id):
    report = g_charging_report_text()
    job = g_nearest_removable_job()

    if not job:
        return (
            "🔋 RAPORT ŁADOWAREK — G\n\n"
            f"{report}\n\n"
            "Nie ma jeszcze partii gotowej do wyjęcia przez G."
        )

    qty = int(job.get("qty", 0))
    ready_at = datetime.fromisoformat(job["ready_at"])
    left = g_time_left_text(ready_at)
    elapsed = g_job_elapsed_text(job)

    data = load_driver_flow()
    data[str(user.id)] = {
        "type": "g_ready_batch",
        "chat_id": chat_id,
        "step": "confirm",
        "driver": get_driver_name(user),
        "created_at": now().isoformat(),
        "qty": qty,
    }
    save_driver_flow(data)

    return (
        (
            "🔋 RAPORT ŁADOWAREK — G\n\n"
            f"{report}\n\n"
            "🟢 NAJBLIŻSZA PARTIA DO WYJĘCIA:\n"
            f"• {qty} baterii — {left} — w ładowarce {elapsed}\n\n"
            "Kliknij 🟢 OK, jeśli wyjmujesz tę partię."
        ),
        get_confirm_keyboard()
    )




def day_bounds_for_tasks():
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return start, end


def week_bounds_for_tasks():
    """
    Tydzień roboczy:
    start: poniedziałek 00:01
    koniec: niedziela 23:59
    """
    current = now()
    monday = (current - timedelta(days=current.weekday())).replace(hour=0, minute=1, second=0, microsecond=0)
    sunday_end = monday + timedelta(days=6, hours=23, minutes=58)
    return monday, sunday_end


def completed_tasks_count(start_dt, end_dt):
    db = load_db()
    count = 0
    batteries = 0
    drivers = {}

    for trip in db.get("trips", []):
        end_iso = trip.get("end")
        if not end_iso:
            continue

        try:
            end = datetime.fromisoformat(end_iso)
        except Exception:
            continue

        if not (start_dt <= end <= end_dt):
            continue

        count += 1
        returned = trip_work_qty(trip)
        batteries += returned

        name = display_driver_name(trip.get("driver", "Nieznany"))
        item = drivers.setdefault(name, {"tasks": 0, "batteries": 0})
        item["tasks"] += 1
        item["batteries"] += returned

    return {
        "tasks": count,
        "batteries": batteries,
        "drivers": drivers,
    }


def tasks_counter_text():
    day_start, day_end = day_bounds_for_tasks()
    week_start, week_end = week_bounds_for_tasks()

    daily = completed_tasks_count(day_start, day_end)
    weekly = completed_tasks_count(week_start, week_end)

    return (
        "✅ WYKONANE ZADANIA\\n"
        f"Dzisiaj: {daily['tasks']} zadań / {daily['batteries']} baterii\\n"
        f"Tydzień: {weekly['tasks']} zadań / {weekly['batteries']} baterii\\n"
        f"Okres tygodnia: {week_start.strftime('%d/%m %H:%M')} – {week_end.strftime('%d/%m %H:%M')}"
    )


def tasks_report_text():
    day_start, day_end = day_bounds_for_tasks()
    week_start, week_end = week_bounds_for_tasks()

    daily = completed_tasks_count(day_start, day_end)
    weekly = completed_tasks_count(week_start, week_end)

    lines = [
        "📊 LICZNIK WYKONANYCH ZADAŃ",
        "",
        f"📅 Dzisiaj: {daily['tasks']} zadań / {daily['batteries']} baterii",
        "",
        "🗓️ Tydzień:",
        f"{week_start.strftime('%d/%m %H:%M')} – {week_end.strftime('%d/%m %H:%M')}",
        f"{weekly['tasks']} zadań / {weekly['batteries']} baterii",
    ]

    if weekly["drivers"]:
        lines.append("")
        lines.append("Kierowcy w tym tygodniu:")
        for name, item in sorted(weekly["drivers"].items(), key=lambda x: x[1]["tasks"], reverse=True):
            lines.append(f"• {name}: {item['tasks']} zadań / {item['batteries']} baterii")

    return "\\n".join(lines)




def start_manual_fill_flow(user, chat_id):
    inv = load_inventory()
    waiting = int(inv.get("waiting", 0))
    charging = int(inv.get("charging", 0))
    free = charger_free_slots()

    data = load_driver_flow()
    data[str(user.id)] = {
        "type": "manual_fill",
        "chat_id": chat_id,
        "step": "qty",
        "driver": get_driver_name(user),
        "created_at": now().isoformat(),
    }
    save_driver_flow(data)

    return (
        "🔌 UZUPEŁNIANIE ŁADOWAREK\n\n"
        f"⏳ Oczekujące: {waiting}\n"
        f"🔌 W ładowarkach: {charging}\n"
        f"🟢 Wolne porty: {free}\n\n"
        "Ile baterii wkładasz do ładowarek?"
    )


def manual_fill_chargers(chat_id, qty):
    inv = load_inventory()
    waiting = int(inv.get("waiting", 0))
    charging = int(inv.get("charging", 0))
    free = charger_free_slots()

    requested = int(qty)
    moved = max(0, min(requested, waiting, free))
    not_moved = max(0, requested - moved)

    if moved > 0:
        inv["waiting"] = waiting - moved
        inv["charging"] = charging + moved
        save_inventory(inv)
        add_charging_job_from_return(chat_id, moved)

    inv_after = load_inventory()
    return {
        "requested": requested,
        "moved": moved,
        "not_moved": not_moved,
        "waiting_before": waiting,
        "charging_before": charging,
        "free_before": free,
        "waiting_after": int(inv_after.get("waiting", 0)),
        "charging_after": int(inv_after.get("charging", 0)),
        "free_after": charger_free_slots(),
    }




def trip_work_qty(trip):
    """
    Ile baterii liczyć kierowcy jako zrobione.
    Gotowe przywiezione NIE są pracą kierowcy.
    """
    try:
        return int(trip.get("work_qty", max(0, int(trip.get("returned", 0)) - int(trip.get("ready_returned", 0)))))
    except Exception:
        return int(trip.get("returned", 0) or 0)


def status_report():
    inv = load_inventory()
    depot = int(inv.get("depot_total", 0))
    ready = int(inv.get("ready", 0))
    waiting = int(inv.get("waiting", 0))
    charging = int(inv.get("charging", 0))
    transit = active_in_transit()
    counted = ready + waiting + charging + transit
    diff = depot - counted

    lines = [
        "📊 STATUS",
        "",
        f"🏢 Depo total: {depot}",
        "",
        f"📦 Gotowe: {ready}",
        f"⏳ Oczekujące: {waiting}",
        f"🔌 W ładowarkach: {charging}",
        f"🚗 W trasie: {transit}",
    ]

    transit_details = active_trip_details()
    if transit_details:
        lines.append("")
        lines.append("Kierowcy w trasie:")
        db = load_db()
        current = now()
        active_info = []
        for trip in db["trips"]:
            if trip.get("end") is None:
                start = datetime.fromisoformat(trip["start"])
                deadline = start + timedelta(hours=trip_time_limit_hours(trip.get("qty", 0)))
                left_minutes = int((deadline - current).total_seconds() // 60)
                if left_minutes >= 0:
                    left_txt = f"zostało {left_minutes // 60}h {left_minutes % 60}min"
                else:
                    late = abs(left_minutes)
                    left_txt = f"spóźnienie {late // 60}h {late % 60}min"
                active_info.append((display_driver_name(trip.get("driver", "Nieznany")), int(trip.get("qty", 0)), left_txt))

        for driver, qty, left_txt in sorted(active_info):
            lines.append(f"• {driver}: {qty} baterii ({left_txt})")

    lines += [
        "",
        f"🧮 Razem policzone: {counted}",
    ]

    if diff > 0:
        lines.append(f"⚠️ Brakuje do depo: {diff}")
    elif diff < 0:
        lines.append(f"🚨 Nadwyżka ponad depo: {abs(diff)}")
    else:
        lines.append("✅ Zgadza się z depo")

    # Alarm "mało gotowych baterii" wyłączony na życzenie — status ma nie spamować takim komunikatem.

    return "\n".join(lines)





def parse_time_today(time_text):
    cleaned = time_text.replace(".", ":").strip()

    try:
        hour, minute = cleaned.split(":")
        hour = int(hour)
        minute = int(minute)
    except Exception:
        raise ValueError("Nieprawidłowy format czasu")

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Nieprawidłowa godzina")

    return now().replace(hour=hour, minute=minute, second=0, microsecond=0)



def restore_trip_command(text, chat_id, chooser_user_id=None):
    """
    Odtwarza aktywną trasę po resecie, ale najpierw próbuje przypisać ją
    do prawdziwego Telegram ID kierowcy z książki kierowców.

    Działa też z krótkim formatem:
    a 59 start10:23
    a 59 start 10:23
    trasa Adam 100 start 07:39
    """
    raw = text.strip()

    match = re.search(
        r"^(?:trasa\s+)?(.+?)\s+(?:(?:zabrane|w trasie)\s+)?(\d+)\s+start\s*(\d{1,2}[:.]\d{2})\s*$",
        raw,
        re.IGNORECASE
    )

    if not match:
        return None

    driver_query = match.group(1).strip()
    qty = int(match.group(2))
    try:
        start_time = parse_time_today(match.group(3))
    except Exception:
        return "❌ Nieprawidłowa godzina startu. Użyj np. 14:52"

    if qty < 1:
        return "🚨 BŁĄD: trasa musi mieć minimum 1 baterię."

    matches = driver_search(driver_query)

    if not matches:
        return (
            f"❌ Nie znalazłem kierowcy dla: {driver_query}\n\n"
            "Dodaj go ręcznie po Telegram ID, np.:\n"
            "dodaj id a 123456789 Adam Od Dobosza Lima\n"
            "dodaj id paulina 987654321 Paulinka Moja Księżniczka\n\n"
            "Kierowca może sprawdzić ID komendą: moj id"
        )

    if len(matches) == 1:
        selected = matches[0]
        return create_restored_trip(
            selected["name"],
            selected["user_id"],
            chat_id,
            start_time,
            qty,
            manual=True
        )

    if chooser_user_id is None:
        lines = [f"🔎 Znalazłem kilku kierowców dla: {driver_query}", ""]
        for i, item in enumerate(matches, start=1):
            code = f" [{item.get('code')}]" if item.get("code") else ""
            lines.append(f"{i}. {item['name']}{code} — ID: {item['user_id']}")
        lines += ["", "Wpisz pełniejszą nazwę albo użyj konkretnego kodu."]
        return "\n".join(lines)

    USER_STATE[str(chooser_user_id)] = {
        "action": "choose_restore_driver",
        "matches": matches,
        "qty": qty,
        "start": start_time.isoformat(),
    }

    lines = [f"🔎 Znalazłem kilku kierowców dla: {driver_query}", ""]
    for i, item in enumerate(matches, start=1):
        code = f" [{item.get('code')}]" if item.get("code") else ""
        lines.append(f"{i}. {item['name']}{code} — ID: {item['user_id']}")
    lines += ["", "Wpisz numer kierowcy, którego chcesz dodać."]

    return "\n".join(lines)


def restore_charging_command(text, chat_id):
    """
    Po resecie odtwarza aktywne ładowanie:
    ladowarki 57 start 13:24
    ladowanie 57 start 13.24

    Dodaje job ładowania i dodaje stan w ładowarkach.
    """
    raw = text.strip()
    t = normalize_text(raw)

    match = re.search(
        r"^(?:ladowarki|ladowarka|ladowanie|wlozone)\s+(\d+)\s+start\s*(\d{1,2}[:.]\d{2})\s*$",
        t,
        re.IGNORECASE
    )

    if not match:
        return None

    qty = int(match.group(1))
    try:
        start_time = parse_time_today(match.group(2))
    except Exception:
        return "❌ Nieprawidłowa godzina startu ładowania. Użyj np. 13:24"

    if qty < 1:
        return "🚨 BŁĄD: ładowarki muszą mieć minimum 1 baterię."

    ready_time = start_time + timedelta(hours=CHARGE_TIME_HOURS)
    alarm_time = ready_time - timedelta(minutes=ALARM_BEFORE_MINUTES)

    data = load_jobs()
    jobs = data.setdefault("jobs", [])
    job_id = len(jobs) + 1

    jobs.append({
        "id": job_id,
        "qty": qty,
        "chat_id": chat_id,
        "start_at": start_time.isoformat(),
        "ready_at": ready_time.isoformat(),
        "alarm_at": alarm_time.isoformat(),
        "alarm_sent": now() >= alarm_time,
        "ready_sent": False,
        "status": "alarm_sent" if now() >= alarm_time else "charging",
        "manual": True,
        "restored": True
    })
    save_jobs(data)

    inv = load_inventory()
    inv["charging"] = int(inv.get("charging", 0)) + qty
    save_inventory(inv)

    return (
        f"✅ ODTWORZONO ŁADOWANIE\n\n"
        f"🔌 W ładowarkach: {qty}\n"
        f"Start: {fmt_dt(start_time)}\n"
        f"Gotowe: {fmt_dt(ready_time)}\n"
        f"Alarm: {fmt_dt(alarm_time)}\n\n"
        f"{status_report()}"
    )




def setup_wizard_start(user_id):
    USER_STATE[str(user_id)] = {
        "action": "setup_depo",
        "setup": {
            "depot": None,
            "ready": None,
            "waiting": None,
            "charging": 0,
            "charging_start": None,
            "trips": []
        }
    }

    return (
        "⚠️ RESET WIZARD URUCHOMIONY\n\n"
        "Dane NIE są jeszcze wyczyszczone. Zapis nastąpi dopiero po OK/zatwierdz na końcu.\n\n"
        "1️⃣ Podaj stan DEPO, np.:\n"
        "494"
    )


def setup_wizard_handle(text, user_id, chat_id):
    """
    Kreator po resecie:
    reset -> depo -> ladowarki + start -> gotowe -> oczekuje -> trasy -> zatwierdz
    """
    key = str(user_id)
    state = USER_STATE.get(key)
    if not state or not str(state.get("action", "")).startswith("setup_"):
        return None

    action = state.get("action")
    setup = state.get("setup", {})
    raw = text.strip()
    t = normalize_text(raw)

    def set_state(new_action):
        state["action"] = new_action
        state["setup"] = setup
        USER_STATE[key] = state

    def parse_plain_or_named_number(names=None):
        if only_number(raw):
            return int(raw.strip())
        if names:
            return find_number_near(names, t)
        return None

    if t in ["anuluj", "cancel", "🔴 cancel"]:
        USER_STATE.pop(key, None)
        return "❌ Kreator anulowany."

    if action == "setup_depo":
        qty = parse_plain_or_named_number("depo|depot|magazyn|mamy|total")
        if qty is None:
            return "Podaj samą liczbę depo, np. 494"
        setup["depot"] = qty
        set_state("setup_charging_qty")
        return (
            "✅ Depo zapisane.\n\n"
            "2️⃣ Podaj ile jest W ŁADOWARKACH, np.:\n"
            "57\n\n"
            "Jeśli zero, wpisz: 0"
        )

    if action == "setup_charging_qty":
        qty = parse_plain_or_named_number("ladowarka|ladowarki|w ladowarkach")
        if qty is None:
            return "Podaj samą liczbę baterii w ładowarkach, np. 57"
        setup["charging"] = qty
        if qty > 0:
            set_state("setup_charging_start")
            return (
                "✅ Ładowarki zapisane.\n\n"
                "2b️⃣ Podaj START ładowania, np.:\n"
                "13:24\n\n"
                "Możesz też wpisać: teraz"
            )
        set_state("setup_ready")
        return (
            "✅ Ładowarki: 0.\n\n"
            "3️⃣ Podaj ile jest GOTOWYCH, np.:\n"
            "77"
        )

    if action == "setup_charging_start":
        if t in ["teraz", "now"]:
            setup["charging_start"] = fmt_dt(now())
        else:
            m = re.search(r"(\d{1,2}[:.]\d{2})", raw)
            if not m:
                return "Podaj godzinę startu, np. 13:24 albo wpisz: teraz"
            setup["charging_start"] = m.group(1).replace(".", ":")
        set_state("setup_ready")
        return (
            "✅ Start ładowania zapisany.\n\n"
            "3️⃣ Podaj ile jest GOTOWYCH, np.:\n"
            "77"
        )

    if action == "setup_ready":
        qty = parse_plain_or_named_number("gotowe|gotowych")
        if qty is None:
            return "Podaj samą liczbę gotowych, np. 77"
        setup["ready"] = qty
        set_state("setup_waiting")
        return (
            "✅ Gotowe zapisane.\n\n"
            "4️⃣ Podaj ile jest OCZEKUJĄCYCH, np.:\n"
            "77"
        )

    if action == "setup_waiting":
        qty = parse_plain_or_named_number("oczekuje|oczekuja|oczekujace|oczekujacych")
        if qty is None:
            return "Podaj samą liczbę oczekujących, np. 77"
        setup["waiting"] = qty
        set_state("setup_trips")
        return (
            "✅ Oczekujące zapisane.\n\n"
            "5️⃣ Teraz podaj kierowców w trasie — po jednej osobie w wiadomości.\n\n"
            "Formaty, które działają:\n"
            "trasa Imie Nazwisko 55 start 14:52\n"
            "Imie Nazwisko zabrane 55 start 14:52\n"
            "Imie Nazwisko 55 start 14:52\n\n"
            "Przykład:\n"
            "trasa Adam 100 start 07:39\n\n"
            "Gdy wpiszesz wszystkich, napisz:\n"
            "zatwierdz"
        )

    if action == "setup_trips":
        if t in ["zatwierdz", "zatwierdzam", "gotowe", "koniec"]:
            # Save everything at once
            save_db({"trips": []})
            save_jobs({"jobs": []})
            save_driver_checks({})

            inv = {
                "depot_total": int(setup.get("depot") or 0),
                "ready": int(setup.get("ready") or 0),
                "waiting": int(setup.get("waiting") or 0),
                "charging": 0,
                "updated_at": None
            }
            save_inventory(inv)

            # Restore charging with timer
            charging_qty = int(setup.get("charging") or 0)
            charging_start = setup.get("charging_start")
            if charging_qty > 0 and charging_start:
                restore_charging_command(f"ladowarki {charging_qty} start {charging_start}", chat_id)

            # Restore trips
            for trip in setup.get("trips", []):
                restore_trip_command(
                    f"{trip['driver']} zabrane {trip['qty']} start {trip['start']}",
                    chat_id
                )

            USER_STATE.pop(key, None)
            return "✅ ZATWIERDZONE\n\nSystem uzupełniony i gotowy do pracy.\n\n" + status_report()

        # Accept driver route line:
        # trasa Adam 100 start 07:39
        # Adam zabrane 100 start 07:39
        # Adam 100 start 07:39
        m = re.search(
            r"^(?:trasa\s+)?(.+?)\s+(?:(?:zabrane|w trasie)\s+)?(\d+)\s+start\s*(\d{1,2}[:.]\d{2})\s*$",
            raw,
            re.IGNORECASE
        )
        if not m:
            return (
                "Nie rozumiem trasy.\n\n"
                "Wpisz np.:\n"
                "trasa Adam 100 start 07:39\n"
                "Adam zabrane 100 start 07:39\n\n"
                "Albo zakończ:\n"
                "zatwierdz"
            )

        driver = m.group(1).strip()
        qty = int(m.group(2))
        start = m.group(3).replace(".", ":")

        if qty < 1:
            return "🚨 Trasa musi mieć minimum 1 baterię."

        setup.setdefault("trips", []).append({"driver": driver, "qty": qty, "start": start})
        set_state("setup_trips")

        return (
            f"✅ Dodano trasę: {driver} — {qty} baterii, start {start}\n\n"
            "Podaj następnego kierowcę albo wpisz:\n"
            "zatwierdz"
        )

    return None



def load_driver_flow():
    return load_json(DRIVER_FLOW_FILE, {})


def save_driver_flow(data):
    save_json(DRIVER_FLOW_FILE, data)


def clear_driver_flow(user_id):
    data = load_driver_flow()
    key = str(user_id)
    if key in data:
        del data[key]
        save_driver_flow(data)


def start_pickup_flow(user, chat_id, pending_qty=None):
    """
    Prosty pickup:
    - pokazuje cały stan od razu,
    - pyta tylko ile kierowca zabiera,
    - zmianę stanu robi dopiero przez ✏️ Edit w podsumowaniu.
    """
    inv = load_inventory()

    ready = int(inv.get("ready", 0))
    charging = int(inv.get("charging", 0))
    waiting = int(inv.get("waiting", 0))

    try:
        limit = get_driver_battery_limit(user)
    except Exception:
        limit = 70

    data = load_driver_flow()
    data[str(user.id)] = {
        "type": "pickup_simple",
        "chat_id": chat_id,
        "step": "take_qty",
        "driver": get_driver_name(user),
        "created_at": now().isoformat(),
        "ready": ready,
        "charging": charging,
        "waiting": waiting,
        "qty": pending_qty,
    }
    save_driver_flow(data)

    if pending_qty:
        return (
            "🚗 PICKUP\n\n"
            f"📦 Gotowe: {ready}\n"
            f"🔌 W ładowarkach: {charging}\n"
            f"⏳ Oczekujące: {waiting}\n\n"
            f"🚦 Twój limit: {limit}\n"
            f"🚗 Chcesz zabrać: {pending_qty}\n\n"
            "Wpisz tę liczbę jeszcze raz albo wpisz inną."
        )

    return (
        "🚗 PICKUP\n\n"
        f"📦 Gotowe: {ready}\n"
        f"🔌 W ładowarkach: {charging}\n"
        f"⏳ Oczekujące: {waiting}\n\n"
        f"🚦 Twój limit: {limit}\n\n"
        "Ile baterii zabierasz?"
    )


def charger_ready_to_remove():
    """
    Ile baterii z ładowarek faktycznie jest już gotowych do wyjęcia.
    Liczymy tylko aktywne joby, których ready_at <= teraz.
    """
    current = now()
    data = load_jobs()
    total = 0

    for job in data.get("jobs", []):
        if job.get("status") in ["charging", "alarm_sent"] and not job.get("ready_sent"):
            try:
                ready_at = datetime.fromisoformat(job["ready_at"])
                if current >= ready_at:
                    total += int(job.get("qty", 0))
            except Exception:
                pass

    inv = load_inventory()
    charging = int(inv.get("charging", 0))
    return min(total, charging)


def remove_ready_from_chargers(qty):
    """
    Wyjmuje gotowe baterie z ładowarek:
    - inventory charging -= qty
    - inventory ready += qty
    - zamyka lub zmniejsza odpowiednie joby ładowania
    """
    qty = int(qty)
    if qty <= 0:
        return 0

    ready_available = charger_ready_to_remove()
    qty = min(qty, ready_available)

    inv = load_inventory()
    inv["charging"] = max(0, int(inv.get("charging", 0)) - qty)
    inv["ready"] = int(inv.get("ready", 0)) + qty
    save_inventory(inv)

    current = now()
    left = qty
    data = load_jobs()

    for job in data.get("jobs", []):
        if left <= 0:
            break

        if job.get("status") in ["charging", "alarm_sent"] and not job.get("ready_sent"):
            try:
                ready_at = datetime.fromisoformat(job["ready_at"])
            except Exception:
                continue

            if current < ready_at:
                continue

            job_qty = int(job.get("qty", 0))
            take = min(left, job_qty)

            if take >= job_qty:
                job["ready_sent"] = True
                job["status"] = "done"
            else:
                job["qty"] = job_qty - take

            left -= take

    save_jobs(data)
    return qty




CONFIG_FILE = "config.json"


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {
            "charger_slots": DEFAULT_CHARGER_SLOTS
        }

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_charger_slots():
    cfg = load_config()
    return int(cfg.get("charger_slots", DEFAULT_CHARGER_SLOTS))


def charger_free_slots():
    inv = load_inventory()
    charging = int(inv.get("charging", 0))
    return max(0, get_charger_slots() - charging)


def auto_move_waiting_to_chargers(chat_id):
    """
    Automatycznie przenosi baterie z OCZEKUJĄCYCH do wolnych miejsc w ŁADOWARKACH.
    Nie zmienia sumy depo — tylko przesuwa: waiting -> charging i zakłada nowy timer ładowania.
    """
    inv = load_inventory()
    waiting = int(inv.get("waiting", 0))
    charging = int(inv.get("charging", 0))
    free = max(0, get_charger_slots() - charging)

    move_qty = min(waiting, free)
    if move_qty <= 0:
        return 0

    inv["waiting"] = waiting - move_qty
    inv["charging"] = charging + move_qty
    save_inventory(inv)

    add_charging_job_from_return(chat_id, move_qty)
    return move_qty


def add_charging_job_from_return(chat_id, qty):
    qty = int(qty)
    if qty <= 0:
        return

    start = now()
    ready_at = start + timedelta(hours=CHARGE_TIME_HOURS)
    alarm_at = ready_at - timedelta(minutes=ALARM_BEFORE_MINUTES)

    jobs_data = load_jobs()
    jobs = jobs_data.setdefault("jobs", [])
    jobs.append({
        "id": len(jobs) + 1,
        "qty": qty,
        "chat_id": chat_id,
        "start_at": start.isoformat(),
        "ready_at": ready_at.isoformat(),
        "alarm_at": alarm_at.isoformat(),
        "alarm_sent": False,
        "ready_sent": False,
        "status": "charging",
        "source": "return"
    })
    save_jobs(jobs_data)


def start_return_flow(user, chat_id, returned_qty=None):
    db = load_db()
    trip = active_trip(db, user.id, user)

    if not trip:
        return "Brak aktywnej trasy do rozliczenia."

    route_qty = int(trip.get("qty", 0))
    if route_qty < 1:
        return "Brak baterii w aktywnej trasie."

    data = load_driver_flow()
    data[str(user.id)] = {
        "type": "return_auto",
        "chat_id": chat_id,
        "step": "returned_qty",
        "driver": get_driver_name(user),
        "created_at": now().isoformat(),
        "route_qty": route_qty,
        "returned": None,
        "ready_returned": 0,
        "used_returned": 0,
        "to_charging": 0,
        "to_waiting": 0,
        "take_extra": 0,
        "next_action": "finish",
    }
    save_driver_flow(data)

    return (
        f"🔁 KONTROLA ZWROTU\n\n"
        f"Masz w trasie: {route_qty} baterii.\n\n"
        "1/3 Ile baterii oddajesz RAZEM?\n"
        "Potem podasz, ile z nich jest GOTOWYCH."
    )

def number_from_text(text):
    match = re.search(r"\d+", text or "")
    return int(match.group(0)) if match else None


def inventory_mismatch_message(field_name, expected, given):
    return (
        "❌ STAN SIĘ NIE ZGADZA\n\n"
        f"{field_name}: w systemie jest {expected}, a wpisano {given}.\n\n"
        "Przyjmujemy, że stan po ostatnim kierowcy jest prawdziwy.\n"
        f"Wpisz poprawnie: {expected}\n"
        "albo wpisz: anuluj"
    )


def handle_driver_flow(text, user, chat_id):
    """
    Obsługa krok po kroku dla kierowcy.
    To ma pierwszeństwo przed normalnymi komendami, ale admin może przerwać resetem.
    """
    data = load_driver_flow()
    key = str(user.id)
    flow = data.get(key)

    if not flow:
        return None

    t = normalize_text(text).strip()

    if t in ["f", "/f", "fill", "/fill"]:
        if not is_admin(user):
            return "❌ Brak dostępu."
        clear_driver_flow(user.id)
        return start_manual_fill_flow(user, chat_id)

    if flow and flow.get("type") == "manual_fill":
        if flow.get("step") == "qty":
            qty = number_from_text(text)
            if qty is None:
                return "Wpisz liczbę baterii, np. 18"
            if qty < 1:
                return "Minimum to 1 bateria."

            result = manual_fill_chargers(chat_id, qty)
            clear_driver_flow(user.id)

            extra = ""
            if result["not_moved"] > 0:
                extra = f"\n⚠️ Nie weszło: {result['not_moved']} — brak miejsca albo za mało oczekujących.\n"

            return (
                "✅ ŁADOWARKI UZUPEŁNIONE\n\n"
                f"Chciałeś włożyć: {result['requested']}\n"
                f"Przeniesiono do ładowarek: {result['moved']}\n"
                f"{extra}\n"
                f"⏳ Oczekujące: {result['waiting_after']}\n"
                f"🔌 W ładowarkach: {result['charging_after']}\n"
                f"🟢 Wolne porty: {result['free_after']}"
            )


    # GLOBAL_G_COMMAND_IN_FLOW
    # G ma otworzyć raport nawet gdy użytkownik ma stary flow.
    if t in ["g", "/g", "gotowe z ladowarek", "gotowe z ładowarek"]:
        if flow and flow.get("type") == "g_ready_batch":
            pass
        else:
            clear_driver_flow(user.id)
            return start_g_command_flow(user, chat_id)

    if t in ["kierowcy", "lista kierowcow", "lista kierowców", "drivers"]:
        return drivers_list_text()

    if is_cancel_text(t):
        audit_log("flow_cancelled", user, chat_id, {"flow": flow})
        clear_driver_flow(user.id)
        return "❌ Przerwano kontrolę. Możesz zacząć od nowa."


    if flow.get("type") == "g_ready_batch":
        if flow["step"] == "confirm":
            if not is_ok_text(t):
                return (
                    "Kliknij 🟢 OK, żeby wyjąć podświetloną partię, albo 🔴 Cancel.",
                    get_confirm_keyboard()
                )

            result = g_finish_nearest_batch(chat_id)
            clear_driver_flow(user.id)

            if not result:
                return "🔌 Nie ma partii spełniającej minimum 3h30."

            moved = int(result.get("moved_waiting_to_charging", 0))
            moved_line = (
                f"🔁 Z oczekujących do ładowarek: {moved}\n"
                if moved > 0 else
                "🔁 Nic nie przełożono z oczekujących.\n"
            )

            return (
                f"✅ PARTIA WYJĘTA Z ŁADOWAREK\n\n"
                f"📦 Dodano do gotowych: {result['qty']}\n"
                f"{moved_line}\n"
                f"{status_report()}"
            )

    qty = number_from_text(text)

    # Tylko te kroki wymagają liczby. Kroki tekstowe typu:
    # Kroki tekstowe typu "zatwierdz" nie mogą być blokowane przez brak liczby.
    numeric_steps = {
        ("pickup_simple", "take_qty"),
        ("pickup", "ready"),
        ("pickup", "charging"),
        ("pickup", "waiting"),
        ("pickup", "take_qty"),
        ("return_auto", "returned_qty"),
        ("return_auto", "ready_returned"),
        ("pickup_manual_charge_check", "manual_charge_qty"),
    }

    if (flow.get("type"), flow.get("step")) in numeric_steps:
        if qty is None:
            return "Wpisz liczbę albo wpisz: anuluj"
        if qty < 0:
            return "Liczba nie może być ujemna."


    if flow.get("type") == "pickup_simple":
        if flow["step"] == "take_qty":
            if qty < 1:
                return "🚨 Minimum to 1 bateria."

            try:
                limit = get_driver_battery_limit(user)
            except Exception:
                limit = 70

            if qty > limit:
                return (
                    f"❌ Nie możesz zabrać {qty} baterii.\n\n"
                    f"Twój aktualny limit: {limit}."
                )

            inv = load_inventory()
            ready = int(inv.get("ready", 0))
            charging = int(inv.get("charging", 0))
            waiting = int(inv.get("waiting", 0))

            if qty > ready:
                return f"❌ Gotowych jest tylko: {ready}"

            flow["qty"] = qty
            flow["ready"] = ready
            flow["charging"] = charging
            flow["waiting"] = waiting
            flow["step"] = "confirm"

            data[key] = flow
            save_driver_flow(data)

            return (
                (
                    "✅ PODSUMOWANIE PICKUP\n\n"
                    f"📦 Gotowe teraz: {ready}\n"
                    f"🔌 W ładowarkach: {charging}\n"
                    f"⏳ Oczekujące: {waiting}\n\n"
                    f"🚗 Zabierasz: {qty}\n"
                    f"📦 Gotowe po pobraniu: {ready - qty}\n\n"
                    "🟢 OK / 🔴 Cancel"
                ),
                get_confirm_keyboard()
            )

        if flow["step"] == "confirm":
            if not is_ok_text(t):
                return (
                    "Kliknij tylko:\n"
                    "🟢 OK\n"
                    "🔴 Cancel",
                    get_confirm_keyboard()
                )

            qty_take = int(flow.get("qty", 0))

            inv = load_inventory()
            ready = int(inv.get("ready", 0))

            if qty_take > ready:
                return f"❌ Gotowych jest teraz tylko {ready}. Kliknij ✏️ Edit albo zacznij pickup od nowa."

            db = load_db()
            existing = active_trip(db, user.id, user)
            if existing:
                clear_driver_flow(user.id)
                return f"Uwaga: masz już aktywną trasę ({existing['qty']} baterii). Najpierw wpisz: Oddane"

            start_time = now()
            deadline = start_time + timedelta(hours=trip_time_limit_hours(qty_take))

            db["trips"].append({
                "driver": get_driver_name(user),
                "user_id": str(user.id),
                "chat_id": chat_id,
                "start": start_time.isoformat(),
                "qty": qty_take,
                "limit_at_start": flow.get("limit_at_start"),
                "time_limit_hours": trip_time_limit_hours(qty_take),
                "end": None,
                "alert_sent": False
            })
            save_db(db)

            inv["ready"] = ready - qty_take
            save_inventory(inv)

            clear_driver_flow(user.id)

            return (
                f"✅ PICKUP ZAPISANY\n\n"
                f"Kierowca: {get_driver_name(user)}\n"
                f"Start: {start_time.strftime('%H:%M')}\n"
                f"Pobrane: {qty_take}\n"
                f"Limit czasu: {trip_time_limit_hours(qty_take)}h\n"
                f"Deadline: {deadline.strftime('%H:%M')}\n\n"
                f"{status_report()}"
            )

        clear_driver_flow(user.id)
        return "⚠️ Pickup się zaciął. Wpisz pickup jeszcze raz."


    # FLOW: pobranie baterii — prosty i odporny kreator
    if flow.get("type") == "pickup":
        inv = load_inventory()

        if flow["step"] == "ready":
            expected = int(inv.get("ready", 0))
            if qty != expected:
                return inventory_mismatch_message("Gotowe", expected, qty)

            flow["ready"] = qty
            flow["step"] = "charging"
            data[key] = flow
            save_driver_flow(data)
            expected_charging = int(inv.get("charging", 0))
            return (
                f"✅ Gotowe potwierdzone: {qty}\n\n"
                f"📌 STAN Z PAMIĘCI — W ŁADOWARKACH: {expected_charging}\n"
                f"✅ Wpisz dokładnie: {expected_charging}\n\n"
                "2/4 Podaj ilość baterii W ŁADOWARKACH:"
            )

        if flow["step"] == "charging":
            if qty > get_charger_slots():
                return f"❌ Ładowarki mają limit {get_charger_slots()}. Wpisz poprawną liczbę."

            expected = int(inv.get("charging", 0))
            if qty != expected:
                return inventory_mismatch_message("W ładowarkach", expected, qty)

            flow["charging"] = qty
            flow["step"] = "waiting"
            data[key] = flow
            save_driver_flow(data)
            expected_waiting = int(inv.get("waiting", 0))
            return (
                f"✅ Ładowarki potwierdzone: {qty}\n\n"
                f"📌 STAN Z PAMIĘCI — OCZEKUJĄCE: {expected_waiting}\n"
                f"✅ Wpisz dokładnie: {expected_waiting}\n\n"
                "3/4 Podaj ilość baterii OCZEKUJĄCYCH:"
            )

        if flow["step"] == "waiting":
            expected = int(inv.get("waiting", 0))
            if qty != expected:
                return inventory_mismatch_message("Oczekujące", expected, qty)

            flow["waiting"] = qty

            if flow.get("qty"):
                flow["step"] = "confirm_take"
                data[key] = flow
                save_driver_flow(data)
                return (
                    (
                        "✅ Stany potwierdzone.\n\n"
                        f"Gotowe: {flow['ready']}\n"
                        f"W ładowarkach: {flow['charging']}\n"
                        f"Oczekujące: {flow['waiting']}\n"
                        f"Zabrane: {flow['qty']}\n\n"
                        "Wybierz opcję:"
                    ),
                    get_confirm_keyboard()
                )

            flow["step"] = "take_qty"
            data[key] = flow
            save_driver_flow(data)
            return (
                "✅ Stany potwierdzone.\n\n"
                f"📌 GOTOWE DO POBRANIA: {flow['ready']}\n"
                f"🔌 W ładowarkach: {flow['charging']}\n"
                f"⏳ Oczekujące: {flow['waiting']}\n\n"
                "4/4 Ile baterii ZABIERASZ?"
            )

        if flow["step"] == "confirm_take":
            if is_edit_text(t):
                flow["confirm_blocked"] = False
                flow["step"] = "take_qty"
                data[key] = flow
                save_driver_flow(data)
                audit_log("pickup_confirm_edit", user, chat_id, {"flow": flow})
                return "✏️ OK, poprawiamy. Ile baterii ZABIERASZ?"

            if not is_ok_text(t):
                flow["confirm_blocked"] = True
                flow["blocked_reason"] = text
                data[key] = flow
                save_driver_flow(data)
                audit_log("pickup_confirm_blocked_by_text", user, chat_id, {"message": text, "flow": flow})
                return (confirmation_block_message(), get_confirm_keyboard())

            if flow.get("confirm_blocked"):
                audit_log("pickup_ok_rejected_after_invalid_text", user, chat_id, {"flow": flow})
                return (
                    "🚫 OK jest zablokowane, bo po podsumowaniu wpisano dodatkową wiadomość.\n\n"
                    "Kliknij ✏️ Edit, żeby poprawić, albo 🔴 Cancel, żeby zacząć od nowa.",
                    get_confirm_keyboard()
                )

            qty_take = int(flow.get("qty", 0))
            if qty_take < 1:
                return "🚨 Nie można zabrać 0. Minimum to 1."

            driver_limit = get_driver_battery_limit(user)
            if qty_take > driver_limit:
                return (
                    f"❌ Nie możesz zabrać {qty_take} baterii.\n\n"
                    f"{driver_limit_status_line(user)}\n"
                    f"Max systemu: {DRIVER_MAX_BATTERIES}, minimum po karach: {DRIVER_MIN_BATTERIES}.\n"
                    "Przywieź trasę na czas, żeby podnieść limit."
                )

            inv = load_inventory()
            ready = int(inv.get("ready", 0))
            if qty_take > ready:
                return f"❌ Za mało gotowych baterii. Gotowe: {ready}, próbujesz zabrać: {qty_take}"

            db = load_db()
            existing = active_trip(db, user.id, user)
            if existing:
                clear_driver_flow(user.id)
                return f"Uwaga: masz już aktywną trasę ({existing['qty']} baterii). Najpierw wpisz: Oddane"

            start_time = now()
            deadline = start_time + timedelta(hours=trip_time_limit_hours(qty_take))

            db["trips"].append({
                "driver": get_driver_name(user),
                "user_id": str(user.id),
                "chat_id": chat_id,
                "start": start_time.isoformat(),
                "qty": qty_take,
                "limit_at_start": driver_limit,
                "time_limit_hours": DRIVER_ROUTE_TIME_LIMIT_HOURS,
                "end": None,
                "alert_sent": False
            })
            save_db(db)

            inv["ready"] = ready - qty_take
            save_inventory(inv)
            clear_driver_flow(user.id)

            return (
                f"{get_driver_name(user)} ✅\n"
                f"Start: {start_time.strftime('%H:%M')}\n"
                f"Pobrane: {qty_take}\n"
                f"Limit czasu: {DRIVER_ROUTE_TIME_LIMIT_HOURS}h\n"
                f"Limit baterii kierowcy: {driver_limit}\n"
                f"Deadline: {deadline.strftime('%H:%M')}\n\n"
                f"{status_report()}"
            )

        if flow["step"] == "take_qty":
            if qty < 1:
                return "🚨 Nie można zabrać 0. Minimum to 1."

            driver_limit = get_driver_battery_limit(user)
            if qty > driver_limit:
                return (
                    f"❌ Nie możesz zabrać {qty} baterii.\n\n"
                    f"{driver_limit_status_line(user)}\n"
                    f"Max systemu: {DRIVER_MAX_BATTERIES}, minimum po karach: {DRIVER_MIN_BATTERIES}.\n"
                    "Przywieź trasę na czas, żeby podnieść limit."
                )

            inv = load_inventory()
            ready = int(inv.get("ready", 0))
            if qty > ready:
                return f"❌ Za mało gotowych baterii. Gotowe: {ready}, próbujesz zabrać: {qty}"

            db = load_db()
            existing = active_trip(db, user.id, user)
            if existing:
                clear_driver_flow(user.id)
                return f"Uwaga: masz już aktywną trasę ({existing['qty']} baterii). Najpierw wpisz: Oddane"

            start_time = now()
            deadline = start_time + timedelta(hours=trip_time_limit_hours(qty))

            db["trips"].append({
                "driver": get_driver_name(user),
                "user_id": str(user.id),
                "chat_id": chat_id,
                "start": start_time.isoformat(),
                "qty": qty,
                "limit_at_start": driver_limit,
                "time_limit_hours": DRIVER_ROUTE_TIME_LIMIT_HOURS,
                "end": None,
                "alert_sent": False
            })
            save_db(db)

            inv["ready"] = ready - qty
            save_inventory(inv)
            clear_driver_flow(user.id)

            return (
                f"{get_driver_name(user)} ✅\n"
                f"Start: {start_time.strftime('%H:%M')}\n"
                f"Pobrane: {qty}\n"
                f"Limit czasu: {DRIVER_ROUTE_TIME_LIMIT_HOURS}h\n"
                f"Limit baterii kierowcy: {driver_limit}\n"
                f"Deadline: {deadline.strftime('%H:%M')}\n\n"
                f"{status_report()}"
            )

        clear_driver_flow(user.id)
        return "⚠️ Kreator pobrania się zaciął. Wpisz Zabrane jeszcze raz."

       # FLOW: automatyczna kontrola zwrotu — opcja B
    if flow.get("type") == "return_auto":
        route_qty = int(flow.get("route_qty", 0))

        if flow["step"] == "returned_qty":
            if qty < 1:
                return "🚨 Nie można oddać 0. Minimum to 1."
            if qty > route_qty:
                return f"❌ Nie możesz oddać {qty}, bo w trasie masz {route_qty}."

            flow["returned"] = qty
            flow["step"] = "ready_returned"
            data[key] = flow
            save_driver_flow(data)

            return (
                f"✅ Oddane razem: {qty}\n\n"
                "2/3 Ile z oddanych baterii jest GOTOWYCH?\n\n"
                "Przykład:\n"
                "Pobrałeś 50, oddajesz 50, gotowe są 3 → wpisz 3."
            )

        if flow["step"] == "ready_returned":
            returned = int(flow.get("returned", 0))

            if qty < 0:
                return "Liczba nie może być ujemna."
            if qty > returned:
                return f"❌ Gotowych nie może być więcej niż oddanych. Oddane razem: {returned}."

            ready_returned = qty
            used_returned = returned - ready_returned

            moved_before = auto_move_waiting_to_chargers(chat_id)

            free = charger_free_slots()
            to_charging = min(used_returned, free)
            to_waiting = used_returned - to_charging
            missing = route_qty - returned

            flow["ready_returned"] = ready_returned
            flow["used_returned"] = used_returned
            flow["to_charging"] = to_charging
            flow["to_waiting"] = to_waiting
            flow["auto_moved_before_return"] = moved_before
            flow["missing_to_ready"] = 0
            flow["next_action"] = "finish"
            flow["take_extra"] = 0
            flow["step"] = "confirm_return_auto"

            data[key] = flow
            save_driver_flow(data)

            missing_line = f"\n⚠️ Brakuje do pełnego zwrotu: {missing}\n" if missing > 0 else ""
            moved_line = (
                f"🔁 Najpierw automatycznie przełożono z oczekujących do ładowarek: {moved_before}\n"
                if moved_before > 0 else ""
            )

            return (
                (
                    "✅ BOT ROZDZIELIŁ ZWROT:\n\n"
                    f"Pobrane na trasę: {route_qty}\n"
                    f"Oddane razem: {returned}\n"
                    f"📦 Gotowe przywiezione: {ready_returned}\n"
                    f"🔋 Do ładowania/oczekujące: {used_returned}\n"
                    f"{moved_line}"
                    f"🔌 Wolne miejsca w ładowarkach: {free}\n"
                    f"➡️ Do ładowarek: {to_charging}\n"
                    f"➡️ Oczekujące: {to_waiting}\n"
                    f"{missing_line}\n"
                    "3/3 Wybierz opcję:"
                ),
                get_confirm_keyboard()
            )

        if flow["step"] == "confirm_return_auto":
            if is_edit_text(t):
                flow["confirm_blocked"] = False
                flow["step"] = "ready_returned"
                data[key] = flow
                save_driver_flow(data)
                audit_log("return_confirm_edit", user, chat_id, {"flow": flow})
                returned = int(flow.get("returned", 0))
                return (
                    "✏️ OK, poprawiamy zwrot.\n\n"
                    f"Oddane razem zostaje: {returned}\n"
                    "Podaj jeszcze raz: ile z oddanych baterii jest GOTOWYCH?"
                )

            if not is_ok_text(t):
                if qty is not None:
                    to_charging_info = int(flow.get("to_charging", 0))
                    to_waiting_info = int(flow.get("to_waiting", 0))

                    if qty == to_charging_info:
                        msg = (
                            f"✅ Tak, bot policzył: {to_charging_info} do ładowarek.\n"
                            f"Oczekujące: {to_waiting_info}.\n\n"
                            "Teraz kliknij 🟢 OK, żeby zapisać zwrot."
                        )
                    elif qty == to_waiting_info:
                        msg = (
                            f"✅ Tak, bot policzył: {to_waiting_info} oczekujące.\n"
                            f"Do ładowarek: {to_charging_info}.\n\n"
                            "Teraz kliknij 🟢 OK, żeby zapisać zwrot."
                        )
                    else:
                        msg = (
                            "To jest już krok potwierdzenia. Bot sam policzył rozdział baterii.\n\n"
                            f"Do ładowarek: {to_charging_info}\n"
                            f"Oczekujące: {to_waiting_info}\n\n"
                            "Kliknij 🟢 OK, żeby zapisać zwrot."
                        )

                    data[key] = flow
                    save_driver_flow(data)
                    return (msg, get_confirm_keyboard())

                flow["confirm_blocked"] = True
                flow["blocked_reason"] = text
                data[key] = flow
                save_driver_flow(data)
                audit_log("return_confirm_blocked_by_text", user, chat_id, {
                    "message": text,
                    "computed": {
                        "returned": flow.get("returned"),
                        "ready_returned": flow.get("ready_returned"),
                        "to_charging": flow.get("to_charging"),
                        "to_waiting": flow.get("to_waiting"),
                    },
                    "flow": flow
                })
                return (confirmation_block_message(), get_confirm_keyboard())

            if flow.get("confirm_blocked"):
                audit_log("return_ok_rejected_after_invalid_text", user, chat_id, {"flow": flow})
                return (
                    "🚫 OK jest zablokowane, bo po podsumowaniu wpisano dodatkową wiadomość.\n\n"
                    "Kliknij ✏️ Edit, żeby poprawić, albo 🔴 Cancel, żeby zacząć od nowa.",
                    get_confirm_keyboard()
                )

            with FILE_LOCK:
                db = load_db()
                trip = active_trip(db, user.id, user)

            if not trip:
                clear_driver_flow(user.id)
                return "Brak aktywnej trasy do rozliczenia."

            original_qty = int(trip.get("qty", 0))
            returned = int(flow.get("returned", 0))
            ready_returned = int(flow.get("ready_returned", 0))
            used_returned = int(flow.get("used_returned", returned - ready_returned))

            if returned > original_qty:
                clear_driver_flow(user.id)
                return f"❌ Oddane ({returned}) nie może być większe niż trasa ({original_qty}). Zacznij zwrot od nowa."

            if ready_returned > returned:
                clear_driver_flow(user.id)
                return "❌ Gotowe nie mogą być większe niż oddane. Zacznij zwrot od nowa."

            moved_before_save = auto_move_waiting_to_chargers(chat_id)
            free = charger_free_slots()
            to_charging = min(used_returned, free)
            to_waiting = used_returned - to_charging

            payment = calc_trip_payment(trip["start"], original_qty, returned)
            end_time = payment["end"]
            hours = payment["hours"]
            late_hours = payment["late_hours"]
            penalty_steps = payment["penalty_steps"]
            gross_returned_earned = payment["gross_returned_earned"]
            time_penalty = payment["time_penalty"]
            earned = payment["earned"]

            trip["end"] = end_time.isoformat()
            trip["returned"] = returned
            trip["ready_returned"] = ready_returned
            trip["work_qty"] = max(0, returned - ready_returned)
            trip["used_returned"] = used_returned
            trip["charged_inside"] = ready_returned
            trip["hours"] = hours
            trip["late_hours"] = late_hours
            trip["penalty_steps"] = penalty_steps
            trip["gross_returned_earned"] = gross_returned_earned
            trip["gross_ready_earned"] = gross_returned_earned
            trip["time_penalty"] = time_penalty
            trip["paid_qty"] = returned
            trip["rate"] = BASE_RATE
            trip["earned"] = 0
            trip["return_auto"] = True
            trip["payment_logic"] = "returned_minus_time_penalty_from_route_qty_v3"

            save_db(db)

            inv = load_inventory()
            inv["ready"] = int(inv.get("ready", 0)) + ready_returned
            inv["charging"] = int(inv.get("charging", 0)) + to_charging
            inv["waiting"] = int(inv.get("waiting", 0)) + to_waiting
            save_inventory(inv)

            if to_charging > 0:
                add_charging_job_from_return(chat_id, to_charging)

            auto_moved_to_charging = auto_move_waiting_to_chargers(chat_id)

            was_late = late_hours > 0
            limit_change_line = driver_limit_change_message(user, was_late)

            clear_driver_flow(user.id)

            state = "OK ✅" if late_hours <= 0 else f"SPÓŹNIONY ❌ ({fmt_hours(late_hours)})"

            moved_total = int(flow.get("auto_moved_before_return", 0)) + moved_before_save + auto_moved_to_charging
            auto_move_line = (
                f"Automatycznie z oczekujących do ładowarek: {moved_total}\n"
                if moved_total > 0 else ""
            )

            return (
                f"✅ ZWROT ZAPISANY\n\n"
                f"Kierowca: {get_driver_name(user)}\n"
                f"Pobrane: {original_qty}\n"
                f"Oddane razem: {returned}\n"
                f"Gotowe przywiezione: {ready_returned}\n"
                f"Zrobione do statystyk: {max(0, returned - ready_returned)}\n"
                f"Do ładowarek: {to_charging}\n"
                f"Oczekujące: {to_waiting}\n"
                f"{auto_move_line}"
                f"Czas: {fmt_hours(hours)}\n"
                f"Status: {state}\n"
                f"{limit_change_line}\n"
                f"{status_report()}"
            )

    return None


RESET_WIZARD_FILE = "telegram_reset_wizard.json"


def load_reset_wizard():
    return load_json(RESET_WIZARD_FILE, {})


def save_reset_wizard(data):
    save_json(RESET_WIZARD_FILE, data)


def clear_reset_wizard(user_id):
    data = load_reset_wizard()
    key = str(user_id)
    if key in data:
        del data[key]
        save_reset_wizard(data)


def start_reset_wizard(user, chat_id):
    # SAFE RESET WIZARD:
    # Nie czyścimy JSON-ów na starcie. Dane zostają nietknięte,
    # dopóki admin nie przejdzie całego kreatora i nie kliknie/wpisze OK.
    # Dzięki temu Cancel nie kasuje działającego systemu.
    USER_STATE.pop(str(user.id), None)
    clear_driver_flow(user.id)

    data = load_reset_wizard()
    data[str(user.id)] = {
        "chat_id": chat_id,
        "step": "depo",
        "depot_total": 0,
        "charging": 0,
        "charge_start": None,
        "ready": 0,
        "waiting": 0,
        "trips": [],
        "pending_driver_choice": None
    }
    save_reset_wizard(data)

    return (
        "⚠️ RESET WIZARD URUCHOMIONY\n\n"
        "Dane NIE są jeszcze wyczyszczone. Zapis nastąpi dopiero po OK/zatwierdz na końcu.\n\n"
        "1/6 Podaj stan DEPO, np.:\n"
        "504"
    )


def parse_wizard_time(text):
    t = normalize_text(text).strip()

    if t in ["teraz", "now"]:
        return now()

    m = re.search(r"(\d{1,2})[:.](\d{2})", text)
    if not m:
        return None

    hour = int(m.group(1))
    minute = int(m.group(2))

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def handle_reset_wizard(text, user, chat_id):
    data = load_reset_wizard()
    key = str(user.id)
    wiz = data.get(key)

    if not wiz:
        return None

    if not is_admin(user):
        return None

    t = normalize_text(text).strip()

    if t in ["anuluj", "cancel", "🔴 cancel", "stop"]:
        clear_reset_wizard(user.id)
        return "❌ Reset wizard przerwany."

    if t in ["zatwierdz", "zatwierdź", "ok", "🟢 ok", "koniec"]:
        if wiz.get("step") != "trips":
            return "Jeszcze nie skończyliśmy. Uzupełnij aktualny krok albo wpisz: anuluj"

        if wiz.get("pending_driver_choice"):
            return "Najpierw wybierz kierowcę z listy numerem albo wpisz: anuluj"

        inv = load_inventory()
        inv["depot_total"] = int(wiz.get("depot_total", 0))
        inv["ready"] = int(wiz.get("ready", 0))
        inv["waiting"] = int(wiz.get("waiting", 0))
        inv["charging"] = int(wiz.get("charging", 0))
        save_inventory(inv)

        charging = int(wiz.get("charging", 0))
        charge_start_iso = wiz.get("charge_start")
        if charging > 0 and charge_start_iso:
            charge_start = datetime.fromisoformat(charge_start_iso)
            ready_at = charge_start + timedelta(hours=CHARGE_TIME_HOURS)
            alarm_at = ready_at - timedelta(minutes=ALARM_BEFORE_MINUTES)
            save_jobs({"jobs": [{
                "id": 1,
                "qty": charging,
                "chat_id": chat_id,
                "start_at": charge_start.isoformat(),
                "ready_at": ready_at.isoformat(),
                "alarm_at": alarm_at.isoformat(),
                "alarm_sent": now() >= alarm_at,
                "ready_sent": False,
                "status": "alarm_sent" if now() >= alarm_at else "charging",
                "manual": True,
                "reset_wizard": True
            }]})
        else:
            save_jobs({"jobs": []})

        db = {"trips": []}
        for trip in wiz.get("trips", []):
            db["trips"].append({
                "driver": trip["driver"],
                "user_id": trip.get("user_id") or f"manual:{normalize_text(trip['driver'])}",
                "chat_id": chat_id,
                "start": trip["start"],
                "qty": int(trip["qty"]),
                "end": None,
                "alert_sent": False,
                "manual": True,
                "reset_wizard": True
            })
        save_db(db)

        clear_reset_wizard(user.id)

        return (
            "✅ RESET ZATWIERDZONY\n\n"
            "Stany i trasy zapisane.\n\n"
            f"{status_report()}\n\n"
            "📢 Ważne: jeśli trasy dodałeś za kierowców ręcznie, kierowcy mogą nie zakończyć ich ze swoich kont.\n"
            "Najbezpieczniej po resecie: każdy kierowca sam wpisuje Zabrane ze swojego Telegrama."
        )

    step = wiz.get("step")

    if step == "depo":
        qty = number_from_text(text)
        if qty is None:
            return "Podaj samą liczbę DEPO, np. 504"
        wiz["depot_total"] = qty
        wiz["step"] = "charging"
        data[key] = wiz
        save_reset_wizard(data)
        return (
            "✅ Depo zapisane.\n\n"
            "2/6 Podaj ile jest W ŁADOWARKACH, np.:\n"
            "133\n\n"
            "Jeśli zero, wpisz: 0"
        )

    if step == "charging":
        qty = number_from_text(text)
        if qty is None:
            return "Podaj samą liczbę baterii w ładowarkach, np. 133"
        if qty < 0:
            return "Liczba nie może być ujemna."
        if qty > get_charger_slots():
            return f"❌ Ładowarki mają limit {get_charger_slots()}."
        wiz["charging"] = qty
        if qty == 0:
            wiz["charge_start"] = None
            wiz["step"] = "ready"
            data[key] = wiz
            save_reset_wizard(data)
            return (
                "✅ Ładowarki zapisane: 0\n\n"
                "3/6 Podaj ile jest GOTOWYCH, np.:\n"
                "77"
            )

        wiz["step"] = "charge_start"
        data[key] = wiz
        save_reset_wizard(data)
        return (
            "✅ Ładowarki zapisane.\n\n"
            "3/6 Podaj START ładowania, np.:\n"
            "06:30\n\n"
            "Możesz też wpisać: teraz"
        )

    if step == "charge_start":
        dt = parse_wizard_time(text)
        if not dt:
            return "Podaj godzinę startu ładowania, np. 06:30 albo wpisz: teraz"
        wiz["charge_start"] = dt.isoformat()
        wiz["step"] = "ready"
        data[key] = wiz
        save_reset_wizard(data)
        return (
            "✅ Start ładowania zapisany.\n\n"
            "4/6 Podaj ile jest GOTOWYCH, np.:\n"
            "77"
        )

    if step == "ready":
        qty = number_from_text(text)
        if qty is None:
            return "Podaj samą liczbę gotowych, np. 77"
        if qty < 0:
            return "Liczba nie może być ujemna."
        wiz["ready"] = qty
        wiz["step"] = "waiting"
        data[key] = wiz
        save_reset_wizard(data)
        return (
            "✅ Gotowe zapisane.\n\n"
            "5/6 Podaj ile jest OCZEKUJĄCYCH, np.:\n"
            "275"
        )

    if step == "waiting":
        qty = number_from_text(text)
        if qty is None:
            return "Podaj samą liczbę oczekujących, np. 275"
        if qty < 0:
            return "Liczba nie może być ujemna."
        wiz["waiting"] = qty
        wiz["step"] = "trips"
        data[key] = wiz
        save_reset_wizard(data)
        return (
            "✅ Oczekujące zapisane.\n\n"
            "6/6 Dodaj trasy kierowców albo wpisz: zatwierdz\n\n"
            "Formaty:\n"
            "trasa Adam 100 start 07:39\n"
            "Adam zabrane 100 start 07:39\n"
            "Adam 100 start 07:39\n\n"
            "Każdego kierowcę wpisz osobno."
        )

    if step == "trips":
        if t in ["numery", "numery kierowcow", "numery kierowców", "kody", "numbers"]:
            return driver_numbers_text()

        if t in ["kierowcy", "lista kierowcow", "lista kierowców", "drivers"]:
            return drivers_list_text()

        pending = wiz.get("pending_driver_choice")

        if pending:
            choice = number_from_text(text)

            if choice is None:
                return "Wpisz numer kierowcy z listy albo wpisz: anuluj"

            matches = pending.get("matches", [])
            if choice < 1 or choice > len(matches):
                return f"Wybierz numer od 1 do {len(matches)}."

            selected = matches[choice - 1]

            wiz.setdefault("trips", []).append({
                "driver": selected["name"],
                "user_id": selected["user_id"],
                "qty": int(pending["qty"]),
                "start": pending["start"]
            })

            wiz["pending_driver_choice"] = None
            data[key] = wiz
            save_reset_wizard(data)

            total_routes = sum(int(x["qty"]) for x in wiz.get("trips", []))
            start_dt = datetime.fromisoformat(pending["start"])

            return (
                f"✅ Dodano trasę:\n"
                f"{selected['name']}: {pending['qty']} baterii, start {fmt_dt(start_dt)}\n"
                f"✅ Telegram ID przypisane: {selected['user_id']}\n\n"
                f"Razem w trasie z resetu: {total_routes}\n\n"
                "Dodaj następną trasę, np.:\n"
                "p 55 start 14:52\n"
                "albo wpisz: zatwierdz"
            )

        patterns = [
            r"^trasa\s+(.+?)\s+(\d+)\s+start\s*(\d{1,2}[:.]\d{2})$",
            r"^(.+?)\s+zabrane\s+(\d+)\s+start\s*(\d{1,2}[:.]\d{2})$",
            r"^(.+?)\s+(\d+)\s+start\s*(\d{1,2}[:.]\d{2})$",
        ]
        m = None
        for p in patterns:
            m = re.search(p, text.strip(), re.IGNORECASE)
            if m:
                break

        if not m:
            return (
                "Nie rozumiem trasy.\n\n"
                "Użyj np.:\n"
                "p 55 start 14:52\n"
                "paw 55 start 14:52\n"
                "trasa Pawel 55 start 14:52\n"
                "albo wpisz: zatwierdz"
            )

        driver_query = m.group(1).strip()
        qty = int(m.group(2))
        dt = parse_wizard_time(m.group(3))

        if qty < 1:
            return "Trasa musi mieć minimum 1 baterię."

        matches = driver_search(driver_query)

        if not matches:
            return (
                f"❌ Nie znalazłem kierowcy dla: {driver_query}\n\n"
                "Najpewniejszy sposób zapisu w grupie:\n"
                "1. Kierowca pisze wiadomość na grupie, np. Cześć.\n"
                "2. Ty odpowiadasz na JEGO wiadomość tekstem: dodaj kierowce\n\n"
                "Alternatywnie kierowca może napisać do bota prywatnie /start albo Pomoc.\n\n"
                "Możesz sprawdzić zapisanych kierowców komendą: kierowcy"
            )

        if len(matches) == 1:
            selected = matches[0]

            wiz.setdefault("trips", []).append({
                "driver": selected["name"],
                "user_id": selected["user_id"],
                "qty": qty,
                "start": dt.isoformat()
            })

            data[key] = wiz
            save_reset_wizard(data)

            total_routes = sum(int(x["qty"]) for x in wiz.get("trips", []))

            return (
                f"✅ Dodano trasę:\n"
                f"{selected['name']}: {qty} baterii, start {fmt_dt(dt)}\n"
                f"✅ Telegram ID przypisane: {selected['user_id']}\n\n"
                f"Razem w trasie z resetu: {total_routes}\n\n"
                "Dodaj następną trasę albo wpisz: zatwierdz"
            )

        wiz["pending_driver_choice"] = {
            "qty": qty,
            "start": dt.isoformat(),
            "matches": matches
        }

        data[key] = wiz
        save_reset_wizard(data)

        lines = [f"🔎 Znalazłem kilku kierowców dla: {driver_query}", ""]

        for i, item in enumerate(matches, start=1):
            username = f" @{item.get('username')}" if item.get("username") else ""
            lines.append(f"{i}. {item['name']}{username}")

        lines += [
            "",
            "Wpisz numer kierowcy, którego chcesz dodać."
        ]

        return "\n".join(lines)

    return "Błąd kreatora resetu. Wpisz: reset"


def reset_all_command(text):
    """
    Komenda admina:
    reset

    Czyści wszystko:
    - trasy
    - magazyn
    - ładowania
    - checki kierowców
    """
    t = normalize_text(text).strip()

    if t not in ["reset", "reset wszystko", "reset system"]:
        return None

    save_db({"trips": []})
    save_inventory({
        "depot_total": 0,
        "ready": 0,
        "waiting": 0,
        "charging": 0,
        "updated_at": None
    })
    save_jobs({"jobs": []})
    save_driver_checks({})

    return (
        "✅ RESET SYSTEMU ZROBIONY\n\n"
        "Wyczyszczono:\n"
        "🚗 trasy\n"
        "📦 magazyn\n"
        "🔌 ładowania\n\n"
        "Teraz wpisz stany i kierowców od nowa, np.:\n"
        "depo 494\n"
        "gotowe 77\n"
        "oczekuje 77\n"
        "ladowarki 57 start 13:24\n"
        "Marcin zabrane 55 start 14:52"
    )


def restore_inventory_with_start_command(text, chat_id):
    """
    Odtwarza po resecie:
    depo 494
    gotowe 77
    oczekuje 77
    ladowarki 57 start 13:24

    Dla gotowe/oczekuje/depo bez startu tylko ustawia stan.
    Dla ladowarki ze startem dodaje aktywne ładowanie z czasem.
    """
    t = normalize_text(text)

    if "start" in t:
        charge_reply = restore_charging_command(text, chat_id)
        if charge_reply:
            return charge_reply
        return None

    normal_state = set_inventory_command(text)
    if normal_state:
        return normal_state

    return None


def update_route_command(text, chat_id):
    """
    Komendy:
    aktualizacja trasa Waldek 60
    korekta trasa Pawel 52
    usun trasa pawel
    trasa pawel 0

    Działa po części nazwy:
    "pawel" znajdzie "Pawel Jaroszewski", zamiast dodać nowego "pawel".
    """
    raw = text.strip()
    t = normalize_text(raw)

    if not (
        t.startswith("aktualizacja trasa")
        or t.startswith("korekta trasa")
        or t.startswith("usun trasa")
        or t.startswith("usuń trasa")
        or t.startswith("trasa ")
    ):
        return None

    remove_mode = t.startswith("usun trasa") or t.startswith("usuń trasa")

    if remove_mode:
        match = re.search(r"(?:usun|usuń)\s+trasa\s+(.+?)\s*$", raw, re.IGNORECASE)
        if not match:
            return "❌ Użyj: usun trasa Pawel"
        driver_input = match.group(1).strip()
        qty = 0
    else:
        match = re.search(r"(?:(?:aktualizacja|korekta)\s+)?trasa\s+(.+?)\s+(\d+)\s*$", raw, re.IGNORECASE)
        if not match:
            return "❌ Użyj: aktualizacja trasa Waldek 60"
        driver_input = match.group(1).strip()
        qty = int(match.group(2))

    db = load_db()
    wanted = normalize_text(driver_input)

    matched_driver = None
    for trip in db["trips"]:
        if trip.get("end") is None:
            existing = trip.get("driver", "")
            existing_n = normalize_text(existing)
            if wanted == existing_n or wanted in existing_n or existing_n in wanted:
                matched_driver = existing
                break

    final_driver = matched_driver or driver_input

    db["trips"] = [
        trip for trip in db["trips"]
        if not (
            trip.get("end") is None
            and (
                normalize_text(trip.get("driver", "")) == normalize_text(final_driver)
                or wanted in normalize_text(trip.get("driver", ""))
                or normalize_text(trip.get("driver", "")) in wanted
            )
        )
    ]

    if qty > 0:
        db["trips"].append({
            "driver": final_driver,
            "user_id": f"manual:{normalize_text(final_driver)}",
            "chat_id": chat_id,
            "start": now().isoformat(),
            "qty": qty,
            "end": None,
            "alert_sent": False,
            "manual": True
        })
        msg = (
            f"✅ AKTUALIZACJA TRASY\n\n"
            f"🚗 {final_driver}: {qty} baterii\n"
            f"📌 Magazyn NIE został pomniejszony."
        )
    else:
        msg = (
            f"✅ USUNIĘTO TRASĘ\n\n"
            f"🚗 {final_driver}\n"
            f"📌 Magazyn NIE został zmieniony."
        )

    save_db(db)

    return f"{msg}\n\n{status_report()}"


def update_all_command(text, chat_id=None, reset_trips=False):
    """
    Jedna komenda do ustawienia całego stanu:
    aktualizacja depo 505 gotowe 197 oczekuje 114 ladowarki 133
    stan depo 505 gotowe 197 oczekuje 114 ladowarki 133

    Opcjonalnie czyści aktywne trasy:
    aktualizacja reset depo 505 gotowe 197 oczekuje 114 ladowarki 133
    """
    t = normalize_text(text)

    if not (t.startswith("aktualizacja") or t.startswith("stan ")):
        return None

    depo = find_number_near("depo|depot|magazyn|mamy|total", t)
    gotowe = find_number_near("gotowe|gotowych", t)
    oczekuje = find_number_near("oczekuje|oczekuja|oczekujace|oczekujacych", t)
    ladowarki = find_number_near("ladowarka|ladowarki|w ladowarkach|laduje sie|laduja sie", t)

    missing = []
    if depo is None:
        missing.append("depo")
    if gotowe is None:
        missing.append("gotowe")
    if oczekuje is None:
        missing.append("oczekuje")
    if ladowarki is None:
        missing.append("ladowarki")

    if missing:
        return (
            "❌ Brakuje danych: " + ", ".join(missing) + "\n\n"
            "Użyj:\n"
            "aktualizacja depo 505 gotowe 197 oczekuje 114 ladowarki 133"
        )

    inv = load_inventory()
    inv["depot_total"] = int(depo)
    inv["ready"] = int(gotowe)
    inv["waiting"] = int(oczekuje)
    inv["charging"] = int(ladowarki)
    save_inventory(inv)

    if " reset " in f" {t} " or reset_trips:
        save_db({"trips": []})

    return (
        "✅ AKTUALIZACJA ZAPISANA\n\n"
        f"🏢 Depo total: {depo}\n"
        f"📦 Gotowe: {gotowe}\n"
        f"⏳ Oczekujące: {oczekuje}\n"
        f"🔌 W ładowarkach: {ladowarki}\n\n"
        + status_report()
    )


def set_inventory_command(text):
    t = normalize_text(text)
    inv = load_inventory()
    changed = False
    notes = []

    qty = find_number_near("depo|depot|magazyn|mamy|total", t)
    if qty is not None:
        inv["depot_total"] = qty
        changed = True
        notes.append(f"✅ Depo total zapisane: {inv['depot_total']}")

    qty = find_number_near("gotowe|gotowych", t)
    if qty is not None:
        inv["ready"] = qty
        changed = True
        notes.append(f"✅ Gotowe zapisane: {inv['ready']}")

    qty = find_number_near("oczekuje|oczekuja|oczekujace|oczekujacych", t)
    if qty is not None:
        inv["waiting"] = qty
        changed = True
        notes.append(f"✅ Oczekujące zapisane: {inv['waiting']}")

    qty = find_number_near("ladowarka|ladowarki|w ladowarkach|laduje sie|laduja sie", t)
    if qty is not None:
        inv["charging"] = qty
        changed = True
        notes.append(f"✅ W ładowarkach zapisane: {inv['charging']}")

    if changed:
        save_inventory(inv)
        return "\n".join(notes) + "\n\n" + status_report()

    return None


def take_from_ready(qty):
    inv = load_inventory()
    ready = int(inv.get("ready", 0))
    qty = int(qty)

    if qty > ready:
        raise ValueError(f"Za mało gotowych baterii. Gotowe: {ready}, próbujesz zabrać: {qty}")

    inv["ready"] = ready - qty
    save_inventory(inv)


def returned_to_inventory(total_returned, charged_inside=0):
    total_returned = int(total_returned)
    charged_inside = min(max(0, int(charged_inside or 0)), total_returned)
    to_waiting = total_returned - charged_inside

    inv = load_inventory()
    inv["ready"] = int(inv.get("ready", 0)) + charged_inside
    inv["waiting"] = int(inv.get("waiting", 0)) + to_waiting
    save_inventory(inv)

    return charged_inside, to_waiting


def move_waiting_to_charging(qty):
    inv = load_inventory()
    qty = int(qty)
    waiting = int(inv.get("waiting", 0))

    if waiting >= qty:
        moved = qty
        warning = ""
    else:
        moved = waiting
        warning = f"\n⚠️ Uwaga: oczekujących było tylko {waiting}, więc przeniesiono {moved}."

    inv["waiting"] = max(0, waiting - moved)
    inv["charging"] = int(inv.get("charging", 0)) + moved
    save_inventory(inv)

    return moved, warning


def move_charging_to_ready(qty):
    inv = load_inventory()
    qty = int(qty)
    charging = int(inv.get("charging", 0))
    moved = min(qty, charging)

    inv["charging"] = max(0, charging - moved)
    inv["ready"] = int(inv.get("ready", 0)) + moved
    save_inventory(inv)

    return moved


def driver_aliases_for_user(user):
    """
    Returns safe aliases for the current Telegram user.
    Used only as a fallback when an active trip was restored with a manual/old ID.
    """
    aliases = set()
    if not user:
        return aliases

    uid = str(user.id)

    for item in load_drivers().values():
        if str(item.get("id", "")) == uid:
            for value in [
                item.get("name", ""),
                item.get("first_name", ""),
                item.get("full_name", ""),
                item.get("username", ""),
                item.get("code", ""),
                *(item.get("aliases") or []),
            ]:
                value = normalize_text(str(value)).strip().lstrip("@")
                if value:
                    aliases.add(value)

    # Telegram profile name as a weak fallback, but only if it is not empty.
    for value in [
        getattr(user, "full_name", ""),
        getattr(user, "first_name", ""),
        getattr(user, "username", ""),
    ]:
        value = normalize_text(str(value)).strip().lstrip("@")
        if value:
            aliases.add(value)

    return aliases


def active_trip(db, user_id, user=None):
    """
    Szuka aktywnej trasy po Telegram ID.
    Fallback po nazwie jest tylko dla starych rekordów bez user_id.
    """
    uid = str(user_id)

    for trip in db.get("trips", []):
        if trip.get("end") is None and str(trip.get("user_id", "")) == uid:
            return trip

    # Fallback dla starszych tras, które nie mają user_id.
    if user is not None:
        current_name = get_driver_name(user)
        for trip in db.get("trips", []):
            if trip.get("end") is None and display_driver_name(trip.get("driver", "")) == current_name:
                return trip

    return None

def calculate_driver_payout_v2(trip, returned, ready_returned=0):
    """
    Tymczasowa funkcja wypłaty.
    Wypłaty i kary są teraz wyłączone.
    Później tutaj można wstawić nową logikę.
    """
    return {
        "earned": 0,
        "penalty": 0,
        "payout": 0,
        "description": ""
    }


def calc_trip(start_iso, qty):
    """
    Stara funkcja zostaje dla kompatybilności raportów / starszych rekordów.
    UWAGA: dla nowych zwrotów używamy calc_trip_payment(), bo wypłata
    jest liczona za GOTOWE, a kara czasowa od CAŁEJ trasy.
    """
    start = datetime.fromisoformat(start_iso)
    end = now()
    hours = (end - start).total_seconds() / 3600
    limit_hours = trip_time_limit_hours(qty)
    late_hours = max(0, hours - limit_hours)
    penalty_steps = math.ceil(late_hours) if late_hours > 0 else 0
    rate = max(MIN_RATE, BASE_RATE - (penalty_steps * PENALTY_PER_HOUR))
    earned = qty * rate
    return end, hours, late_hours, rate, earned


def calc_trip_payment(start_iso, route_qty, paid_qty):
    """
    LOGIKA WYPŁATY:

    - zarobek bazowy: ODDANE RAZEM baterie × BASE_RATE
    - kara czasowa: CAŁA trasa / pobrane baterie × PENALTY_PER_HOUR × rozpoczęte godziny spóźnienia
    - do wypłaty: max(0, zarobek bazowy - kara czasowa)
    """
    start = datetime.fromisoformat(start_iso)
    end = now()
    hours = (end - start).total_seconds() / 3600

    route_qty = int(route_qty)
    paid_qty = int(paid_qty)

    limit_hours = trip_time_limit_hours(route_qty)
    late_hours = max(0, hours - limit_hours)
    penalty_steps = math.ceil(late_hours) if late_hours > 0 else 0

    gross_returned_earned = paid_qty * BASE_RATE
    time_penalty = route_qty * PENALTY_PER_HOUR * penalty_steps
    earned = max(0.0, gross_returned_earned - time_penalty)

    return {
        "end": end,
        "hours": hours,
        "late_hours": late_hours,
        "penalty_steps": penalty_steps,
        "gross_returned_earned": gross_returned_earned,
        "gross_ready_earned": gross_returned_earned,  # kompatybilność ze starymi raportami
        "time_penalty": time_penalty,
        "earned": earned,
        "paid_qty": paid_qty,
        "route_qty": route_qty,
    }


def active_trips_text():
    db = load_db()
    lines = []
    current = now()

    for trip in db["trips"]:
        if trip.get("end") is None:
            start = datetime.fromisoformat(trip["start"])
            deadline = start + timedelta(hours=trip_time_limit_hours(trip.get("qty", 0)))
            hours = (current - start).total_seconds() / 3600
            left_minutes = int((deadline - current).total_seconds() // 60)

            if left_minutes >= 0:
                left_txt = f"zostało {left_minutes // 60}h {left_minutes % 60}min"
                state = "OK ✅"
            else:
                late = abs(left_minutes)
                left_txt = f"spóźnienie {late // 60}h {late % 60}min"
                state = "SPÓŹNIONY ❌"

            lines.append(
                f"{trip['driver']}: {trip['qty']} baterii, "
                f"start {fmt_dt(start)}, deadline {fmt_dt(deadline)}, {left_txt}, {state}"
            )

    return "🚚 AKTYWNE TRASY\n" + ("\n".join(lines) if lines else "Brak aktywnych tras.")



def period_start(period):
    current = now()

    if period in ["tydzien", "week"]:
        return (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0), "TYDZIEŃ"

    if period in ["miesiac", "month"]:
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0), "MIESIĄC"

    return current.replace(hour=0, minute=0, second=0, microsecond=0), "DZIŚ"


def driver_name_match(saved_name, query):
    saved = normalize_text(saved_name).strip()
    q = normalize_text(query).strip()

    if not q:
        return False

    return q == saved or q in saved or saved in q


def driver_report(driver_query, period="dzis"):
    db = load_db()
    start_period, title = period_start(period)

    matched_name = None
    batteries = 0
    trips = 0
    earned = 0.0
    late = 0
    hours_total = 0.0

    for trip in db["trips"]:
        if trip.get("end") is None:
            continue

        driver = trip.get("driver", "")
        if not driver_name_match(driver, driver_query):
            continue

        end = datetime.fromisoformat(trip["end"])
        if end < start_period:
            continue

        matched_name = driver
        batteries += trip_work_qty(trip)
        trips += 1
        earned += float(trip.get("earned", 0))
        hours_total += float(trip.get("hours", 0))

        if float(trip.get("late_hours", 0)) > 0:
            late += 1

    if not matched_name:
        return f"📊 RAPORT {title}\n\nBrak danych dla kierowcy: {driver_query}"

    avg = fmt_hours(hours_total / trips) if trips else "0h 0min"

    return (
        f"📊 RAPORT {title}\n"
        f"👤 Kierowca: {matched_name}\n\n"
        f"Oddane: {batteries} baterii\n"
        f"Trasy: {trips}\n"
        f"Średni czas: {avg}\n"
        f"Spóźnienia: {late}\n"
        f"Zarobek: £{earned:.2f}"
    )


def weekly_all_drivers_report():
    db = load_db()
    start_period, title = period_start("tydzien")

    summary = {}
    for trip in db["trips"]:
        if trip.get("end") is None:
            continue

        end = datetime.fromisoformat(trip["end"])
        if end < start_period:
            continue

        name = display_driver_name(trip.get("driver", "Nieznany"))
        s = summary.setdefault(name, {"batteries": 0, "trips": 0, "earned": 0.0, "late": 0, "hours": 0.0})
        s["batteries"] += trip_work_qty(trip)
        s["trips"] += 1
        s["earned"] += float(trip.get("earned", 0))
        s["hours"] += float(trip.get("hours", 0))

        if float(trip.get("late_hours", 0)) > 0:
            s["late"] += 1

    if not summary:
        return "📊 RAPORT TYGODNIOWY\nBrak danych."

    lines = ["📊 RAPORT TYGODNIOWY", "🕕 Automatyczny raport: poniedziałek 06:00", ""]
    total_batt = 0
    total_trips = 0
    total_earned = 0.0

    for name, s in sorted(summary.items(), key=lambda x: x[1]["batteries"], reverse=True):
        total_batt += s["batteries"]
        total_trips += s["trips"]
        total_earned += s["earned"]
        avg = fmt_hours(s["hours"] / s["trips"]) if s["trips"] else "0h 0min"

        lines += [
            f"{name}:",
            f"Oddane: {s['batteries']} baterii",
            f"Trasy: {s['trips']}",
            f"Średni czas: {avg}",
            f"Spóźnienia: {s['late']}",
            f"Zarobek: £{s['earned']:.2f}",
            ""
        ]

    lines += [
        "━━━━━━━━━━",
        "TOTAL FIRMA:",
        f"Oddane baterie: {total_batt}",
        f"Trasy: {total_trips}",
        "━━━━━━━━━━",
    ]

    return "\n".join(lines)


def report(period="dzis"):
    db = load_db()
    current = now()

    if period in ["tydzien", "week"]:
        start_period = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        title = "TYDZIEŃ"
    elif period in ["miesiac", "month"]:
        start_period = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        title = "MIESIĄC"
    else:
        start_period = current.replace(hour=0, minute=0, second=0, microsecond=0)
        title = "DZIŚ"

    summary = {}
    for trip in db["trips"]:
        if trip.get("end") is None:
            continue
        end = datetime.fromisoformat(trip["end"])
        if end < start_period:
            continue

        s = summary.setdefault(trip["driver"], {"batteries": 0, "trips": 0, "earned": 0.0, "late": 0, "hours": 0.0})
        s["batteries"] += trip_work_qty(trip)
        s["trips"] += 1
        s["earned"] += float(trip.get("earned", 0))
        s["hours"] += float(trip.get("hours", 0))
        if float(trip.get("late_hours", 0)) > 0:
            s["late"] += 1

    if not summary:
        return f"📊 RAPORT {title}\nBrak danych."

    lines = [f"📊 RAPORT {title}", ""]
    total_batt = total_trips = 0
    total_earned = 0.0

    for name, s in sorted(summary.items()):
        total_batt += s["batteries"]
        total_trips += s["trips"]
        total_earned += s["earned"]
        avg = fmt_hours(s["hours"] / s["trips"]) if s["trips"] else "0h 0min"
        lines += [
            f"{name}:",
            f"Oddane: {s['batteries']} baterii",
            f"Trasy: {s['trips']}",
            f"Średni czas: {avg}",
            f"Spóźnienia: {s['late']}",
            f"Zarobek: £{s['earned']:.2f}",
            ""
        ]

    return "\n".join(lines)


def leaderboard(period="dzis"):
    db = load_db()
    current = now()

    if period in ["tydzien", "week"]:
        start_period = (current - timedelta(days=current.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        title = "TYDZIEŃ"
    elif period in ["miesiac", "month"]:
        start_period = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        title = "MIESIĄC"
    else:
        start_period = current.replace(hour=0, minute=0, second=0, microsecond=0)
        title = "DZIŚ"

    scores = {}
    for trip in db["trips"]:
        if trip.get("end") is None:
            continue
        if datetime.fromisoformat(trip["end"]) < start_period:
            continue
        scores[trip["driver"]] = scores.get(trip["driver"], 0) + trip_work_qty(trip)

    if not scores:
        return f"🏆 RANKING {title}\nBrak danych."

    lines = [f"🏆 RANKING {title}", ""]
    for i, (name, qty) in enumerate(sorted(scores.items(), key=lambda x: x[1], reverse=True), start=1):
        lines.append(f"{i}. {name}: {qty} baterii")
    return "\n".join(lines)


def add_charging_job(text, chat_id):
    t = normalize_text(text)
    qty = find_number_after("wlozone|wlozylem|wlozylam|do ladowania", t)
    if qty is None:
        return None

    moved, warning = move_waiting_to_charging(qty)

    start = now()
    ready = start + timedelta(hours=CHARGE_TIME_HOURS)
    alarm = ready - timedelta(minutes=ALARM_BEFORE_MINUTES)

    if moved > 0:
        data = load_jobs()
        jobs = data.setdefault("jobs", [])
        job_id = len(jobs) + 1
        jobs.append({
            "id": job_id,
            "qty": moved,
            "chat_id": chat_id,
            "start_at": start.isoformat(),
            "ready_at": ready.isoformat(),
            "alarm_at": alarm.isoformat(),
            "alarm_sent": False,
            "ready_sent": False,
            "status": "charging"
        })
        save_jobs(data)

    return (
        f"🔋 Włożone do ładowania: {moved}\n"
        f"Start: {fmt_dt(start)}\n"
        f"Gotowe: {fmt_dt(ready)}\n"
        f"Alarm: {fmt_dt(alarm)}"
        f"{warning}\n\n"
        f"{status_report()}"
    )



def clock_report():
    """
    Zegarek: pokazuje odliczanie dla kierowców i ładowarek.
    """
    current = now()
    lines = ["⏱️ ZEGAREK", ""]

    db = load_db()
    active = [trip for trip in db.get("trips", []) if trip.get("end") is None]

    lines.append("🚗 KIEROWCY:")
    if not active:
        lines.append("Brak aktywnych tras.")
    else:
        for trip in active:
            start = datetime.fromisoformat(trip["start"])
            deadline = start + timedelta(hours=trip_time_limit_hours(trip.get("qty", 0)))
            left_minutes = int((deadline - current).total_seconds() // 60)

            if left_minutes >= 0:
                left_txt = f"zostało {left_minutes // 60}h {left_minutes % 60}min"
                state = "OK ✅"
            else:
                late = abs(left_minutes)
                left_txt = f"po czasie {late // 60}h {late % 60}min"
                state = "KONIEC CZASU ❌"

            lines.append(
                f"• {display_driver_name(trip.get('driver', 'Nieznany'))}: {trip.get('qty', 0)} baterii | "
                f"start {fmt_dt(start)} | deadline {fmt_dt(deadline)} | {left_txt} | {state}"
            )

    lines.append("")
    lines.append("🔋 ŁADOWARKI:")

    jobs_data = load_jobs()
    jobs = [j for j in jobs_data.get("jobs", []) if j.get("status") in ["charging", "alarm_sent"]]

    if not jobs:
        lines.append("Brak aktywnych ładowań.")
    else:
        for job in jobs:
            ready_at = datetime.fromisoformat(job["ready_at"])
            left_minutes = int((ready_at - current).total_seconds() // 60)

            if left_minutes >= 0:
                left_txt = f"gotowe za {left_minutes // 60}h {left_minutes % 60}min"
            else:
                late = abs(left_minutes)
                left_txt = f"powinny być gotowe od {late // 60}h {late % 60}min"

            lines.append(
                f"• ID {job.get('id')}: {job.get('qty', 0)} baterii | "
                f"gotowe {fmt_dt(ready_at)} | {left_txt}"
            )

    return "\n".join(lines)


def charging_status():
    data = load_jobs()
    jobs = [j for j in data.get("jobs", []) if j.get("status") in ["charging", "alarm_sent"]]
    if not jobs:
        return "🔋 Brak aktywnego ładowania.\n\n" + status_report()

    lines = ["🔋 AKTYWNE ŁADOWANIA", ""]
    current = now()

    for job in jobs:
        ready = datetime.fromisoformat(job["ready_at"])
        left = max(0, int((ready - current).total_seconds() // 60))
        lines += [f"ID: {job['id']}", f"Ilość: {job['qty']}", f"Gotowe: {fmt_dt(ready)}", f"Zostało: {left} min", ""]

    lines.append(status_report())
    return "\n".join(lines)



def driver_numbers_text():
    lines = [
        "🔢 NUMERY KIEROWCÓW",
        "",
        "1 — Luke Dobosz Od Petera",
        "2 — Michal Od Kasi Kosmet Londyn",
        "3 — Michal Kierowca Od Dobosza",
        "4 — Piter",
        "5 — Pawel Drewni Movano",
        "7 — Waldek Nw Lima Kierowca",
        "8 — Alex Puchalski Lima",
        "9 — Paulinka Moja Księżniczka",
        "10 — Krzysztof Kierowca Tomasz Pie",
        "11 — Pawel Hanslow Lima",
        "12 — Paulinka Moja Księżniczka",
        "13 — Kris",
        "14 — Martinez Kierowca Mitcham Na Lima",
        "",
        "Format trasy:",
        "1 50 start 12:00",
        "14 60 start 16:40"
    ]
    return "\n".join(lines)


def help_text():
    return (
        "📌 KOMENDY / MENU:\n\n"
        "Kliknij przycisk i wpisz samą liczbę.\n\n"
        "🚗 KIEROWCY:\n"
        "Najpierw podaj: Gotowe, Ładowarka, Oczekują.\n"
        "Dopiero potem Zabrane → wpisz np. 30\n"
        "Oddane → wpisz pełną liczbę z trasy, bot sam liczy ładowarki/oczekujące i zamyka trasę.\n"
        "Można też pisać ręcznie: 30 zabrane / oddane 61\n\n"
        "👷‍♂️ KIEROWCY — magazyn:\n"
        "Gotowe → liczba\n"
        "Ładowarka → liczba\n"
        "Oczekują → liczba\n\n"
        "👑 ADMIN — tylko admin:\n"
        "Depo → liczba\n"
        "aktualizacja depo 505 gotowe 197 oczekuje 114 ladowarki 133\n"
        "aktualizacja reset depo 505 gotowe 197 oczekuje 114 ladowarki 133\n"
        "aktualizacja trasa Waldek 60\n"
        "reset — uruchamia kreator uzupełniania danych\n"
        "W kreatorze wpisujesz depo, ładowarki, gotowe, oczekujące i trasy.\n"
        "Format trasy: trasa Marcin 55 start 14:52\n"
        "Na końcu wpisz: zatwierdz\n\n"
        "👑 ADMIN — komunikaty:\n"
        "Ogłoszenie → wpisz treść\n"
        "Alert → wpisz treść\n\n"
        "📊 RAPORT / STAN:\n"
        "status / stan / zegarek / raport dzis / ranking dzis\n"
        "raport Jan Kowalski\n"
        "raport dzis Jan Kowalski\n"
        "raport tydzien Jan Kowalski\n"
        "Raport tygodniowy firmy idzie automatycznie w poniedziałek o 06:00.\n"
    )


def resolve_driver_for_admin(query):
    """
    Tylko admin używa aliasów.
    Jeżeli zapytanie jest niejednoznaczne, zwracamy listę kandydatów zamiast zgadywać.
    """
    q = normalize_text(query).strip()
    matches = []

    for key, item in DRIVER_ID_BOOK.items():
        name = item.get("name", "")
        aliases = item.get("aliases", [])
        values = [key, name] + aliases
        normalized_values = [normalize_text(str(v)).strip() for v in values if v]

        if q in normalized_values:
            matches.append((key, item))
            continue

        # Dopuszczamy częściowe dopasowanie tylko jeśli nie jest to samo "michal".
        if q and q != "michal":
            if any(q in v for v in normalized_values):
                matches.append((key, item))

    if len(matches) == 1:
        return matches[0][1], None

    if len(matches) > 1:
        lines = ["Znalazłem kilku kierowców. Nie zgaduję:", ""]
        for key, item in matches:
            lines.append(f"{key}. {display_driver_name(item.get('name', 'Nieznany'))}")
        lines.append("")
        lines.append("Wpisz dokładniej: surmacz / pietrzak / tofik albo numer z listy.")
        return None, "\\n".join(lines)

    return None, "Nie znalazłem kierowcy. Użyj dokładnego aliasu, np. surmacz, pietrzak, tofik."


def handle_command(text, user, chat_id):
    t = normalize_text(text).strip()

    # SAFE F COMMAND
    if t in ["f", "/f", "fill", "/fill"]:
        if not is_admin(user):
            return "❌ Brak dostępu."
        clear_driver_flow(user.id)
        return start_manual_fill_flow(user, chat_id)


    if t in ["fill", "/fill", "f"]:
        moved = auto_move_waiting_to_chargers(chat_id)
        inv = load_inventory()
        return (
            "🔌 UZUPEŁNIANIE ŁADOWAREK\n\n"
            f"✅ Przeniesiono do ładowarek: {moved}\n\n"
            f"📦 Gotowe: {int(inv.get('ready',0))}\n"
            f"⏳ Oczekujące: {int(inv.get('waiting',0))}\n"
            f"🔌 W ładowarkach: {int(inv.get('charging',0))}"
        )


    if t in ["zadania", "/zadania", "licznik", "/licznik", "tasks"]:
        return tasks_report_text()

    # GLOBAL_G_COMMAND_EARLY
    # Komenda G musi działać przed obsługą flow/potwierdzeń.
    if t in ["g", "/g", "gotowe z ladowarek", "gotowe z ładowarek"]:
        clear_driver_flow(user.id)
        return start_g_command_flow(user, chat_id)

    if t in ["g", "/g", "gotowe z ladowarek", "gotowe z ładowarek"]:
        return start_g_command_flow(user, chat_id)
    db = load_db()
    name = get_driver_name(user)
    user_id = str(user.id)
    responses = []

    # ADMIN: dynamiczna liczba portów ładowania.
    # Działa z ukośnikiem i bez: /setchargers 140 albo setchargers 140.
    if t.startswith("/setchargers") or t.startswith("setchargers"):
        if not is_admin(user):
            return "❌ Brak dostępu."

        parts = t.split()
        if len(parts) < 2:
            return "Użycie: /setchargers 140"

        try:
            value = int(parts[1])
        except Exception:
            return "Użycie: /setchargers 140"

        if value < 1:
            return "Liczba portów musi być większa od 0."

        cfg = load_config()
        cfg["charger_slots"] = value
        save_config(cfg)

        moved = auto_move_waiting_to_chargers(chat_id)
        free = charger_free_slots()

        moved_line = f"\nAutomatycznie przeniesiono z oczekujących do ładowarek: {moved}" if moved > 0 else ""

        return (
            f"✅ Zmieniono liczbę portów ładowania\n\n"
            f"Nowy limit: {value}\n"
            f"Wolne miejsca: {free}"
            f"{moved_line}"
        )

    if t in ["/movecharging", "movecharging", "przeniesladowarki", "przenieśładowarki", "przenies_ladowarki"]:
        if user.id not in ADMIN_IDS:
            return "Brak dostępu."

        moved = auto_move_waiting_to_chargers(chat_id)
        inv = load_inventory()
        return (
            f"✅ Przeniesiono oczekujące do ładowarek: {moved}\n\n"
            f"⏳ Oczekujące: {int(inv.get('waiting', 0))}\n"
            f"🔌 W ładowarkach: {int(inv.get('charging', 0))}\n"
            f"🔌 Wolne porty: {charger_free_slots()}"
        )

    if t in ["/chargers", "chargers", "ladowarki", "ładowarki"]:
        total = get_charger_slots()
        inv = load_inventory()
        charging = int(inv.get("charging", 0))
        waiting = int(inv.get("waiting", 0))
        free = charger_free_slots()

        return (
            f"🔌 ŁADOWARKI\n\n"
            f"Porty razem: {total}\n"
            f"Zajęte: {charging}\n"
            f"Wolne: {free}\n"
            f"Oczekujące: {waiting}"
        )

    # Obsługa wyboru kierowcy po numerze po liście.
    restore_choice_reply = handle_restore_driver_choice(text, user, chat_id)
    if restore_choice_reply:
        return restore_choice_reply

    # Ręczne dodanie kierowcy:
    # dodaj id a 123456789 Adam Nowak
    add_driver_reply = add_driver_id_command(text)
    if add_driver_reply:
        if not is_admin(user):
            return "❌ Tylko administrator może dodawać kierowców."
        return add_driver_reply

    # RESET ma ZAWSZE pierwszeństwo, nawet gdy bot jest w środku formularza/wizarda.
    if t in ["reset", "reset wszystko", "reset system"]:
        if not is_admin(user):
            return "❌ Reset jest tylko dla administratora."
        USER_STATE.pop(user_id, None)
        clear_driver_flow(user_id)
        try:
            clear_reset_wizard(user_id)
        except Exception:
            pass
        return start_reset_wizard(user, chat_id)

    if t in ["anuluj", "cancel", "🔴 cancel", "stop"]:
        # Cancel ma czyścić tylko aktywny lokalny kreator użytkownika.
        # Nie rusza bazy JSON i nie czyści reset wizarda globalnie poza jego własną obsługą.
        USER_STATE.pop(user_id, None)
        clear_driver_flow(user_id)
        if load_reset_wizard().get(user_id) and is_admin(user):
            clear_reset_wizard(user_id)
            return "❌ Reset wizard przerwany. Dane systemu NIE zostały wyczyszczone."
        return "❌ Przerwano aktualny kreator. Możesz zacząć od nowa."

    # HARD PRIORITY: these commands must work even if an old wizard/USER_STATE is stuck.
    # This prevents "status" from being treated as a wizard answer and corrupting inventory.
    if t in ["status", "stan"]:
        return status_report()

    if t.startswith("trasy") or t.startswith("aktywni") or t.startswith("routes") or t.startswith("active"):
        return active_trips_text()

    if t in ["pomoc", "help", "/start", "start"]:
        return help_text()

    if t in ["moj id", "moje id"]:
        return f"Twoje Telegram ID: {user_id}"

    if t in ["numery", "numery kierowcow", "numery kierowców", "kody", "numbers"]:
        if not is_admin(user):
            return "❌ Numery kierowców są tylko dla administratora."
        return driver_numbers_text()

    if t in ["kierowcy", "lista kierowcow", "lista kierowców", "drivers"]:
        if not is_admin(user):
            return "❌ Lista kierowców jest tylko dla administratora."
        return drivers_list_text()

    if t in ["audit", "audyt", "log", "audit log"]:
        if not is_admin(user):
            return "❌ Audit log jest tylko dla administratora."
        data_audit = load_json(AUDIT_FILE, {"events": []})
        events = data_audit.get("events", [])[-20:]
        if not events:
            return "📋 AUDIT LOG\n\nBrak zdarzeń."
        lines = ["📋 AUDIT LOG — ostatnie 20 zdarzeń", ""]
        for ev in events:
            who = ev.get("user_name") or ev.get("user_id") or "unknown"
            event = ev.get("event", "")
            tm = ev.get("time", "")[11:19]
            details = ev.get("details") or {}
            msg = details.get("message") or details.get("blocked_reason") or ""
            if len(str(msg)) > 60:
                msg = str(msg)[:57] + "..."
            lines.append(f"• {tm} | {who} | {event}" + (f" | {msg}" if msg else ""))
        return "\n".join(lines)

    # HARD GUARD ENTERPRISE:
    # Jeżeli użytkownik jest na ekranie potwierdzenia, KAŻDA wiadomość idzie do flow.
    # Dzięki temu tekst typu "Do ładowarek: 0" blokuje OK i trafia do audit logu,
    # zamiast zostać zignorowanym przez menu/komendy.
    active_flow = load_driver_flow().get(user_id)
    if (
        active_flow
        and active_flow.get("step") in ["confirm_return_auto", "confirm_take"]
    ):
        return handle_driver_flow(text, user, chat_id)

    # Kreator resetu musi mieć pierwszeństwo przed USER_STATE i zwykłymi komendami.
    # To naprawia zatrzymanie po wpisaniu oczekujących w kroku 5/6.
    reset_wizard_reply = handle_reset_wizard(text, user, chat_id)
    if reset_wizard_reply:
        return reset_wizard_reply

    # Jeśli stary kreator po resecie jest aktywny, obsłuż go dopiero po reset_wizard.
    setup_reply = setup_wizard_handle(text, user_id, chat_id)
    if setup_reply:
        if not is_admin(user):
            return "❌ Kreator resetu jest tylko dla administratora."
        return setup_reply

    # Pełne komendy admina z czasem działają poza kreatorem.
    if is_admin(user):
        admin_restore_charge_reply = restore_charging_command(text, chat_id)
        if admin_restore_charge_reply:
            USER_STATE.pop(user_id, None)
            return admin_restore_charge_reply

        admin_restore_trip_reply = restore_trip_command(text, chat_id, user_id)
        if admin_restore_trip_reply:
            return admin_restore_trip_reply

        if "start" in t:
            return (
                "❌ Nie rozumiem tej komendy ze startem.\n\n"
                "Użyj np.:\n"
                "ladowarki 45 start 13:15\n"
                "trasa Adam 100 start 07:39\n"
                "Adam zabrane 100 start 07:39"
            )

    flow_reply = handle_driver_flow(text, user, chat_id)
    if flow_reply:
        return flow_reply

    # OK only confirms an active flow. Without an active confirmation, do nothing.
    # Important: do NOT clear driver_flow here, otherwise incomplete return confirmation breaks.
    if t in ["ok", "🟢 ok"]:
        return "ℹ️ Nothing to confirm right now."

    # Obsługa menu: kliknięty przycisk + następna wiadomość jako liczba/treść.
    if user_id in USER_STATE:
        state = USER_STATE.pop(user_id)
        action = state.get("action")

        if action in ["gotowe", "ladowarka", "oczekuja", "depo"]:
            if not only_number(text):
                USER_STATE[user_id] = state
                return (
                    "Wpisz samą liczbę, np. 30\n\n"
                    "Jeśli chcesz komendę z czasem, wpisz pełną komendę, np. ladowarki 45 start 13:15"
                )
            qty = int(text.strip())
            return handle_command(f"{action} {qty}", user, chat_id)

        if action in ["ogloszenie", "alert"]:
            if not is_admin(user):
                return "❌ Tylko administrator może wysyłać ogłoszenia i alerty."
            group_id = load_group().get("chat_id") or chat_id
            prefix = "📢 OGŁOSZENIE" if action == "ogloszenie" else "🚨 ALERT"
            return {
                "send_to": group_id,
                "text": f"{prefix}\n\n{text.strip()}",
                "confirm": "✅ Wysłane."
            }

    button_action = BUTTON_ACTIONS.get(t)
    if button_action:
        if button_action in ["gotowe", "ladowarka", "oczekuja", "depo"] and not is_admin(user):
            return "❌ Ta komenda jest tylko dla administratora."


        if button_action == "zabrane":
            USER_STATE.pop(user_id, None)
            return start_pickup_flow(user, chat_id)

        if button_action == "oddane":
            USER_STATE.pop(user_id, None)
            return start_return_flow(user, chat_id)

        USER_STATE[user_id] = {"action": button_action}
        labels = {
            "gotowe": "Enter current READY batteries:",
            "ladowarka": "Enter current batteries in CHARGING:",
            "oczekuja": "Enter current WAITING batteries:",
            "depo": "Enter current DEPOT TOTAL:",
        }
        return labels[button_action]

    text_action = admin_text_action(t)
    if text_action:
        if not is_admin(user):
            return "❌ Tylko administrator może wysyłać ogłoszenia i alerty."
        USER_STATE[user_id] = {"action": text_action}
        return "Type announcement text:" if text_action == "ogloszenie" else "Type alert text:"

    if t in ["moj id", "moje id"]:
        return f"Twoje Telegram ID: {user_id}"

    if t in ["numery", "numery kierowcow", "numery kierowców", "kody", "numbers"]:
        if not is_admin(user):
            return "❌ Numery kierowców są tylko dla administratora."
        return driver_numbers_text()

    if t in ["kierowcy", "lista kierowcow", "lista kierowców", "drivers"]:
        if not is_admin(user):
            return "❌ Lista kierowców jest tylko dla administratora."
        return drivers_list_text()


    reset_reply = reset_all_command(text)
    if reset_reply:
        if not is_admin(user):
            return "❌ Reset jest tylko dla administratora."
        return reset_reply

    restore_inventory_reply = restore_inventory_with_start_command(text, chat_id)
    if restore_inventory_reply:
        if not is_admin(user):
            return "❌ Odtwarzanie stanu jest tylko dla administratora."
        return restore_inventory_reply

    restore_trip_reply = restore_trip_command(text, chat_id, user_id)
    if restore_trip_reply:
        if not is_admin(user):
            return "❌ Odtwarzanie trasy jest tylko dla administratora."
        return restore_trip_reply

    restore_charging_reply = restore_charging_command(text, chat_id)
    if restore_charging_reply:
        if not is_admin(user):
            return "❌ Odtwarzanie ładowania jest tylko dla administratora."
        return restore_charging_reply

    route_reply = update_route_command(text, chat_id)
    if route_reply:
        if not is_admin(user):
            return "❌ Aktualizacja trasy jest tylko dla administratora."
        return route_reply

    update_reply = update_all_command(text, chat_id)
    if update_reply:
        if not is_admin(user):
            return "❌ Aktualizacja stanu jest tylko dla administratora."
        return update_reply

    if t.startswith("ogloszenie ") or t.startswith("alert "):
        if not is_admin(user):
            return "❌ Tylko administrator może wysyłać ogłoszenia i alerty."
        group_id = load_group().get("chat_id") or chat_id
        prefix = "📢 OGŁOSZENIE" if t.startswith("ogloszenie ") else "🚨 ALERT"
        original = text.split(" ", 1)[1].strip()
        return {
            "send_to": group_id,
            "text": f"{prefix}\n\n{original}",
            "confirm": "✅ Wysłane."
        }

    if t in ["pomoc", "help", "/start", "start"]:
        return help_text()

    if t == "ustaw grupe":
        save_group(chat_id)
        return "✅ Ta grupa została ustawiona jako grupa główna dla alertów."

    if t.startswith("raport"):
        words = t.split()
        period = "dzis"

        for p in ["dzis", "tydzien", "week", "miesiac", "month"]:
            if p in words:
                period = p

        # Raport konkretnego kierowcy:
        # raport Jan Kowalski
        # raport dzis Jan Kowalski
        # raport tydzien Jan Kowalski
        name_query = text
        name_query = re.sub(r"(?i)^\s*raport\s*", "", name_query).strip()
        name_query = re.sub(r"(?i)\b(dzis|dziś|tydzien|tydzień|week|miesiac|miesiąc|month)\b", "", name_query).strip()

        if name_query:
            return driver_report(name_query, period)

        return report(period)

    if t.startswith("ranking") or t.startswith("top"):
        period = "dzis"
        for p in ["dzis", "tydzien", "week", "miesiac", "month"]:
            if p in t.split():
                period = p
        return leaderboard(period)

    if t in ["ladowanie status", "status ladowania"]:
        return charging_status()

    charge_reply = add_charging_job(text, chat_id)
    if charge_reply:
        return charge_reply

    # Depo / pełna aktualizacja tylko admin.
    admin_inventory_words = ["depo", "depot", "magazyn", "mamy", "total"]
    if any(word in t for word in admin_inventory_words):
        if not is_admin(user):
            return "❌ Depo jest tylko dla administratora."
        inventory_reply = set_inventory_command(text)
        if inventory_reply:
            return inventory_reply

    # Kierowcy mogą wpisywać: gotowe X / ladowarka X / oczekuje X.
    driver_inventory_words = [
        "gotowe", "gotowych",
        "oczekuje", "oczekuja", "oczekujace", "oczekujacych",
        "ladowarka", "ladowarki", "w ladowarkach"
    ]
    if any(word in t for word in driver_inventory_words):
        inventory_reply = set_inventory_command(text)
        if inventory_reply:
            fields = inventory_fields_in_text(text)
            if fields:
                mark_driver_inventory_check(user_id, fields)
                if {"gotowe", "ladowarki", "oczekujace"}.issubset(get_driver_inventory_check(user_id)):
                    inventory_reply += "\n\n✅ Stan magazynu podany. Teraz możesz wpisać: zabrane X"
            return inventory_reply

    if t in ["status", "stan"]:
        return status_report()

    if t in ["zegarek", "czas", "odliczanie"]:
        return clock_report()

    if t.startswith("trasy") or t.startswith("aktywni") or t.startswith("routes") or t.startswith("active"):
        return active_trips_text()

    returned_qty = find_number_near(
        "return|returned|oddane|oddalem|oddałem|oddalam|oddałam|oddaje|oddaję|oddal|oddał|zwrot|zwrocilem|zwrocilam|zwracam",
        normalize_text(text)
    )
    take_qty = find_number_near(
        "pickup|picked|take|taken|zabrane|biore|biorę|bierze|wezme|wezmę|wzialem|wziąłem|wzielam|wzięłam|pobieram|pobralem|pobrałam|odebralem|odebrałem|odebralam|odebrałam|odbieram",
        normalize_text(text)
    )

    charged_inside = 0
    m = re.search(r"(?:w\s+tym|z\s+tego)\s+(\d+)\s+(?:naladowanych|naładowanych|gotowych)", normalize_text(text))
    if m:
        charged_inside = int(m.group(1))

    if returned_qty is not None and returned_qty < 1:
        return (
            "🚨 BŁĄD: nie można oddać 0 baterii.\n"
            "Minimum to 1.\n\n"
            "Jeśli to korekta, zgłoś ją adminowi."
        )

    if take_qty is not None and take_qty < 1:
        return "🚨 BŁĄD: nie można zabrać 0 baterii. Minimum to 1."

    if take_qty is not None:
        missing_msg = missing_inventory_check_text(user_id)
        if missing_msg:
            return start_pickup_flow(user, chat_id, pending_qty=take_qty)

    if returned_qty is not None:
        reply = start_return_flow(user, chat_id)
        if returned_qty >= 0:
            flow_reply = handle_driver_flow(str(returned_qty), user, chat_id)
            if flow_reply:
                return reply + "\n\n" + flow_reply
        return reply

    if take_qty is not None:
        inv = load_inventory()
        ready = int(inv.get("ready", 0))
        if take_qty > ready:
            return f"❌ Za mało gotowych baterii. Gotowe: {ready}, próbujesz zabrać: {take_qty}"

        existing = active_trip(db, user_id, user)
        if existing:
            responses.append(f"Uwaga: {name} ma już aktywną trasę ({existing['qty']} baterii). Najpierw wpisz: oddalem X")
        else:
            start_time = now()
            deadline = start_time + timedelta(hours=trip_time_limit_hours(take_qty))
            db["trips"].append({
                "driver": name,
                "user_id": user_id,
                "chat_id": chat_id,
                "start": start_time.isoformat(),
                "qty": take_qty,
                "end": None,
                "alert_sent": False
            })
            take_from_ready(take_qty)
            reset_driver_inventory_check(user_id)
            responses.append(
                f"{name} ✅\n"
                f"Start: {start_time.strftime('%H:%M')}\n"
                f"Pobrane: {take_qty}\n"
                f"Limit: {trip_time_limit_hours(take_qty)}h\n"
                f"Deadline: {deadline.strftime('%H:%M')}"
            )

    save_db(db)

    if responses:
        return "\n\n".join(responses)

    return ""


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    # Zapamiętujemy autora każdej wiadomości, którą bot widzi.
    remember_driver(update.message.from_user)

    # WAŻNE W GRUPIE:
    # Jeśli Telegram ma włączoną prywatność bota, bot może nie widzieć zwykłych wiadomości kierowców.
    # Dlatego admin może odpowiedzieć na wiadomość kierowcy tekstem: "dodaj kierowce".
    # Wtedy zapisujemy ID osoby, na której wiadomość admin odpowiedział.
    normalized_message = normalize_text(update.message.text).strip()
    if normalized_message in ["dodaj kierowce", "dodaj kierowcę", "zapisz kierowce", "zapisz kierowcę"]:
        keyboard = get_keyboard(update.message.from_user, update.effective_chat)

        if not is_admin(update.message.from_user):
            await update.message.reply_text("❌ Tylko administrator może ręcznie dodawać kierowców.", reply_markup=keyboard)
            return

        if not update.message.reply_to_message or not update.message.reply_to_message.from_user:
            await update.message.reply_text(
                "Odpowiedz tym tekstem na wiadomość kierowcy:\n\n"
                "dodaj kierowce",
                reply_markup=keyboard
            )
            return

        target_user = update.message.reply_to_message.from_user
        remember_driver(target_user)
        await update.message.reply_text(
            f"✅ Kierowca zapisany:\n"
            f"{get_driver_name(target_user)}\n"
            f"Telegram ID: {target_user.id}\n\n"
            "Teraz w resecie możesz wpisać np.:\n"
            f"{target_user.first_name or get_driver_name(target_user)} 55 start 14:52",
            reply_markup=keyboard
        )
        return

    # Dodatkowo, jeśli bot widzi odpowiedź na czyjąś wiadomość, też zapamięta tę osobę.
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        remember_driver(update.message.reply_to_message.from_user)

    keyboard = get_keyboard(update.message.from_user, update.effective_chat)
    reply = handle_command(update.message.text, update.message.from_user, update.message.chat_id)

    if isinstance(reply, tuple):
        reply_text, reply_keyboard = reply
        await update.message.reply_text(reply_text, reply_markup=reply_keyboard)
    elif isinstance(reply, dict):
        await context.bot.send_message(chat_id=reply["send_to"], text=reply["text"])
        await update.message.reply_text(reply.get("confirm", "✅ Gotowe."), reply_markup=keyboard)
    elif reply:
        await update.message.reply_text(reply, reply_markup=keyboard)


async def charging_scheduler(app: Application):
    while True:
        try:
            data = load_jobs()
            jobs = data.get("jobs", [])
            changed = False
            current = now()

            for job in jobs:
                if job.get("status") == "done":
                    continue

                chat_id = job.get("chat_id") or load_group().get("chat_id")
                qty = int(job.get("qty", 0))
                ready_at = datetime.fromisoformat(job["ready_at"])
                alarm_at = datetime.fromisoformat(job["alarm_at"])

                if not chat_id:
                    continue

                if not job.get("alarm_sent") and current >= alarm_at:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"⏰ ZEGAREK ŁADOWANIA\n"
                            f"🔋 Baterie będą gotowe za {ALARM_BEFORE_MINUTES} minut!\n"
                            f"Ilość: {qty}\n"
                            f"Gotowe o: {fmt_dt(ready_at)}"
                        )
                    )
                    job["alarm_sent"] = True
                    job["status"] = "alarm_sent"
                    changed = True

                if not job.get("ready_sent") and current >= ready_at:
                    moved = move_charging_to_ready(qty)
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"✅ BATERIE NAŁADOWANE\n"
                            f"🔋 Ilość: {moved}\n"
                            f"⏰ Gotowe od: {fmt_dt(ready_at)}\n\n"
                            f"{status_report()}"
                        )
                    )
                    job["ready_sent"] = True
                    job["status"] = "done"
                    changed = True

            if changed:
                save_jobs(data)

        except Exception as e:
            print("charging_scheduler error:", e)

        await asyncio.sleep(30)


async def driver_alerts(app: Application):
    while True:
        try:
            db = load_db()
            current = now()
            changed = False

            for trip in db["trips"]:
                if trip.get("end") is None:
                    start = datetime.fromisoformat(trip["start"])
                    deadline = start + timedelta(hours=trip_time_limit_hours(trip.get("qty", 0)))
                    qty = int(trip.get("qty", 0))
                    driver = display_driver_name(trip.get("driver", "Nieznany"))
                    chat_id = trip.get("chat_id") or load_group().get("chat_id")

                    if not chat_id:
                        continue

                    left_minutes = int((deadline - current).total_seconds() // 60)

                    if left_minutes <= 60 and left_minutes > 15 and not trip.get("alert_60_sent"):
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"⏰ ZEGAREK KIEROWCY\n"
                                f"🚗 {driver}\n"
                                f"🔋 W trasie: {qty} baterii\n"
                                f"Zostało około: {left_minutes} min\n"
                                f"Deadline: {fmt_dt(deadline)}"
                            )
                        )
                        trip["alert_60_sent"] = True
                        changed = True

                    if left_minutes <= 15 and left_minutes >= 0 and not trip.get("alert_15_sent"):
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🚨 ZA 15 MIN KONIEC CZASU\n"
                                f"🚗 {driver}\n"
                                f"🔋 W trasie: {qty} baterii\n"
                                f"Zostało: {left_minutes} min\n"
                                f"Deadline: {fmt_dt(deadline)}"
                            )
                        )
                        trip["alert_15_sent"] = True
                        changed = True

                    # ALERT SPÓŹNIENIA — tylko jeden raz na trasę.
                    # Telegram nie pozwala ustawić czerwonego koloru tekstu w zwykłej wiadomości,
                    # więc używamy czerwonych emoji + pogrubienia HTML.
                    if left_minutes < 0 and not trip.get("alert_sent"):
                        late = abs(left_minutes)
                        driver_user_id = str(trip.get("user_id", "")).strip()
                        target_chat_id = int(driver_user_id) if driver_user_id.isdigit() else chat_id

                        alert_text = (
                            f"🔴🚨 <b>SKOŃCZYŁ CI SIĘ CZAS</b> 🚨🔴\n\n"
                            f"🚗 Kierowca: {driver}\n"
                            f"🔋 Baterie w trasie: {qty}\n"
                            f"⏰ Deadline był o: {fmt_dt(deadline)}\n"
                            f"❌ Spóźnienie: {late // 60}h {late % 60}min\n\n"
                            f"To jest jedyny alert spóźnienia dla tej trasy."
                        )

                        try:
                            await app.bot.send_message(
                                chat_id=target_chat_id,
                                text=alert_text,
                                parse_mode="HTML"
                            )
                        except Exception:
                            # Jeżeli bot nie może napisać prywatnie do kierowcy,
                            # wysyła jeden alert na czat trasy/grupę jako fallback.
                            await app.bot.send_message(
                                chat_id=chat_id,
                                text=alert_text,
                                parse_mode="HTML"
                            )

                        trip["alert_sent"] = True
                        trip["last_overdue_alert_at"] = current.isoformat()
                        changed = True

            if changed:
                save_db(db)

        except Exception as e:
            print("driver_alerts error:", e)

        await asyncio.sleep(60)


async def weekly_report_scheduler(app:  Application):
    while True:
        try:
            current = now()

            # Poniedziałek 06:00-06:04
            if current.weekday() == 0 and current.hour == 6 and current.minute < 5:
                data = load_json(WEEKLY_REPORT_FILE, {"last_sent": None})
                today_key = current.strftime("%Y-%m-%d")

                if data.get("last_sent") != today_key:
                    chat_id = load_group().get("chat_id")
                    if chat_id:
                        await app.bot.send_message(chat_id=chat_id, text=weekly_all_drivers_report())
                        data["last_sent"] = today_key
                        save_json(WEEKLY_REPORT_FILE, data)

            await asyncio.sleep(60)

        except Exception as e:
            print("weekly_report_scheduler error:", e)
            await asyncio.sleep(60)


async def post_init(app: Application):
    asyncio.create_task(charging_scheduler(app))
    asyncio.create_task(driver_alerts(app))
    asyncio.create_task(weekly_report_scheduler(app))


def main():
    if not TELEGRAM_TOKEN:
        print("BRAK TOKENA TELEGRAM — ustaw zmienną środowiskową TELEGRAM_TOKEN")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.COMMAND, text_handler))
    print("Telegram Lime Battery Bot PRO v7.4 działa...")
    application.run_polling()


if __name__ == "__main__":
    main()











































































































