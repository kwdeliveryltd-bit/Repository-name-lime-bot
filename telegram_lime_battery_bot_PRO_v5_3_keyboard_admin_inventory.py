import asyncio
import json
import math
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = "8687096130:AAFyAcnPHovXDT8cTDPjg-dwuXBpCmKwqK0"
DB_FILE = "telegram_db.json"
INVENTORY_FILE = "telegram_inventory.json"
CHARGE_JOBS_FILE = "telegram_charge_jobs.json"
GROUP_FILE = "telegram_group.json"
DRIVER_CHECK_FILE = "telegram_driver_checks.json"
WEEKLY_REPORT_FILE = "telegram_weekly_report.json"

# Wpisz tutaj swoje ID z Telegrama po użyciu komendy: moj id
# Przykład: ADMIN_IDS = {"123456789"}
ADMIN_IDS = set()

# Pamięć klikniętych przycisków: użytkownik klika akcję, potem wpisuje samą liczbę.
USER_STATE = {}

TZ = ZoneInfo("Europe/London")

BASE_RATE = 2.00
PENALTY_PER_HOUR = 0.10
TIME_LIMIT_HOURS = 5
MIN_RATE = 1.00

CHARGE_TIME_HOURS = 4.5
ALARM_BEFORE_MINUTES = 15
LOW_READY_LIMIT = 50


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


def load_json(path, default):
    if not os.path.exists(path):
        save_json(path, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        save_json(path, default)
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_db():
    return load_json(DB_FILE, {"trips": []})


def save_db(db):
    save_json(DB_FILE, db)


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



def get_driver_name(user):
    return user.full_name or user.first_name or str(user.id)


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
    # TEST MODE: gdy ADMIN_IDS jest puste, każdy może testować admin-komendy.
    # Produkcyjnie wpisz swoje ID: ADMIN_IDS = {"123456789"}
    return not ADMIN_IDS or str(user.id) in ADMIN_IDS


def get_keyboard(user=None):
    """Menu przycisków pod polem wpisywania w Telegramie."""

    # 👷‍♂️ KIEROWCY
    base = [
        ["Zabrane", "Oddane"],
        ["Gotowe", "Ładowarka", "Oczekują"],
        ["Pomoc"],
    ]

    # 👑 ADMIN – dodatkowe opcje
    if user is not None and is_admin(user):
        base += [
            ["Status", "Trasy"],
            ["Depo", "Ogłoszenie", "Alert"],
        ]

    return ReplyKeyboardMarkup(base, resize_keyboard=True, one_time_keyboard=False)


BUTTON_ACTIONS = {
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
    if t in ["ogloszenie", "ogłoszenie"]:
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
            name = trip.get("driver", "Nieznany")
            details[name] = details.get(name, 0) + int(trip.get("qty", 0))

    return details


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
                deadline = start + timedelta(hours=TIME_LIMIT_HOURS)
                left_minutes = int((deadline - current).total_seconds() // 60)
                if left_minutes >= 0:
                    left_txt = f"zostało {left_minutes // 60}h {left_minutes % 60}min"
                else:
                    late = abs(left_minutes)
                    left_txt = f"spóźnienie {late // 60}h {late % 60}min"
                active_info.append((trip.get("driver", "Nieznany"), int(trip.get("qty", 0)), left_txt))

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

    if ready < LOW_READY_LIMIT:
        lines.append("")
        lines.append(f"🚨 ALARM: mało gotowych baterii ({ready})")

    return "\n".join(lines)





def parse_time_today(time_text):
    cleaned = time_text.replace(".", ":")
    hour, minute = cleaned.split(":")
    return now().replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)


def restore_trip_command(text, chat_id):
    """
    Po resecie ręcznie odtwarza aktywną trasę:
    Marcin zabrane 55 start 14:52
    Karol trasa 40 start 13.27

    Nie odejmuje z gotowych — to jest odtworzenie stanu.
    """
    raw = text.strip()

    match = re.search(
        r"^(.+?)\s+(?:zabrane|trasa|w trasie)\s+(\d+)\s+start\s+(\d{1,2}[:.]\d{2})\s*$",
        raw,
        re.IGNORECASE
    )

    if not match:
        return None

    driver = match.group(1).strip()
    qty = int(match.group(2))
    start_time = parse_time_today(match.group(3))

    if qty < 1:
        return "🚨 BŁĄD: trasa musi mieć minimum 1 baterię."

    db = load_db()
    wanted = normalize_text(driver)

    db["trips"] = [
        trip for trip in db["trips"]
        if not (
            trip.get("end") is None
            and (
                normalize_text(trip.get("driver", "")) == wanted
                or wanted in normalize_text(trip.get("driver", ""))
                or normalize_text(trip.get("driver", "")) in wanted
            )
        )
    ]

    db["trips"].append({
        "driver": driver,
        "user_id": f"manual:{wanted}",
        "chat_id": chat_id,
        "start": start_time.isoformat(),
        "qty": qty,
        "end": None,
        "alert_sent": False,
        "manual": True,
        "restored": True
    })

    save_db(db)

    deadline = start_time + timedelta(hours=TIME_LIMIT_HOURS)

    return (
        f"✅ ODTWORZONO TRASĘ\n\n"
        f"🚗 {driver}: {qty} baterii\n"
        f"Start: {fmt_dt(start_time)}\n"
        f"Deadline: {fmt_dt(deadline)}\n"
        f"📌 Gotowe NIE zostały pomniejszone.\n\n"
        f"{status_report()}"
    )


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
        r"^(?:ladowarki|ladowarka|ladowanie|wlozone)\s+(\d+)\s+start\s+(\d{1,2}[:.]\d{2})\s*$",
        t,
        re.IGNORECASE
    )

    if not match:
        return None

    qty = int(match.group(1))
    start_time = parse_time_today(match.group(2))

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
    inv["ready"] = max(0, int(inv.get("ready", 0)) - int(qty))
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


def active_trip(db, user_id):
    for trip in reversed(db["trips"]):
        if trip["user_id"] == str(user_id) and trip.get("end") is None:
            return trip
    return None


def calc_trip(start_iso, qty):
    start = datetime.fromisoformat(start_iso)
    end = now()
    hours = (end - start).total_seconds() / 3600
    late_hours = max(0, hours - TIME_LIMIT_HOURS)
    penalty_steps = math.ceil(late_hours) if late_hours > 0 else 0
    rate = max(MIN_RATE, BASE_RATE - (penalty_steps * PENALTY_PER_HOUR))
    earned = qty * rate
    return end, hours, late_hours, rate, earned


def active_trips_text():
    db = load_db()
    lines = []
    current = now()

    for trip in db["trips"]:
        if trip.get("end") is None:
            start = datetime.fromisoformat(trip["start"])
            deadline = start + timedelta(hours=TIME_LIMIT_HOURS)
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
        batteries += int(trip.get("returned", 0))
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

        name = trip.get("driver", "Nieznany")
        s = summary.setdefault(name, {"batteries": 0, "trips": 0, "earned": 0.0, "late": 0, "hours": 0.0})
        s["batteries"] += int(trip.get("returned", 0))
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
        f"Do wypłaty: £{total_earned:.2f}",
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
        s["batteries"] += int(trip.get("returned", 0))
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

    lines += ["━━━━━━━━━━", "TOTAL FIRMA:", f"Oddane baterie: {total_batt}", f"Trasy: {total_trips}", f"Do wypłaty: £{total_earned:.2f}", "━━━━━━━━━━"]
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
        scores[trip["driver"]] = scores.get(trip["driver"], 0) + int(trip.get("returned", 0))

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


def help_text():
    return (
        "📌 KOMENDY / MENU:\n\n"
        "Kliknij przycisk i wpisz samą liczbę.\n\n"
        "🚗 KIEROWCY:\n"
        "Najpierw podaj: Gotowe, Ładowarka, Oczekują.\n"
        "Dopiero potem Zabrane → wpisz np. 30\n"
        "Oddane → wpisz np. 61, minimum to 1.\n"
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
        "reset\n"
        "depo 494 / gotowe 77 / oczekuje 77\n"
        "ladowarki 57 start 13:24\n"
        "Marcin zabrane 55 start 14:52\n\n"
        "👑 ADMIN — komunikaty:\n"
        "Ogłoszenie → wpisz treść\n"
        "Alert → wpisz treść\n\n"
        "📊 RAPORT / STAN:\n"
        "status / stan / raport dzis / ranking dzis\n"
        "raport Jan Kowalski\n"
        "raport dzis Jan Kowalski\n"
        "raport tydzien Jan Kowalski\n"
        "Raport tygodniowy firmy idzie automatycznie w poniedziałek o 06:00.\n"
    )

def handle_command(text, user, chat_id):
    t = normalize_text(text)
    db = load_db()
    name = get_driver_name(user)
    user_id = str(user.id)
    responses = []

    # Obsługa menu: kliknięty przycisk + następna wiadomość jako liczba/treść.
    if user_id in USER_STATE:
        state = USER_STATE.pop(user_id)
        action = state.get("action")

        if action in ["zabrane", "oddane", "gotowe", "ladowarka", "oczekuja", "depo"]:
            if not only_number(text):
                USER_STATE[user_id] = state
                return "Wpisz samą liczbę, np. 30"
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
        if button_action in ["depo"] and not is_admin(user):
            return "❌ Depo jest tylko dla administratora."
        USER_STATE[user_id] = {"action": button_action}
        labels = {
            "zabrane": "Ile baterii zabrane?",
            "oddane": "Ile baterii oddane?",
            "gotowe": "Podaj aktualną liczbę GOTOWYCH baterii:",
            "ladowarka": "Podaj aktualną liczbę baterii W ŁADOWARKACH:",
            "oczekuja": "Podaj aktualną liczbę OCZEKUJĄCYCH baterii:",
            "depo": "Podaj aktualny DEPO TOTAL:",
        }
        return labels[button_action]

    text_action = admin_text_action(t)
    if text_action:
        if not is_admin(user):
            return "❌ Tylko administrator może wysyłać ogłoszenia i alerty."
        USER_STATE[user_id] = {"action": text_action}
        return "Wpisz treść ogłoszenia:" if text_action == "ogloszenie" else "Wpisz treść alertu:"

    if t in ["moj id", "moje id"]:
        return f"Twoje Telegram ID: {user_id}"

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

    restore_trip_reply = restore_trip_command(text, chat_id)
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

    if t.startswith("trasy") or t.startswith("aktywni"):
        return active_trips_text()

    returned_qty = find_number_near(
        "oddane|oddalem|oddałem|oddalam|oddałam|oddaje|oddaję|oddal|oddał|zwrot|zwrocilem|zwrocilam|zwracam",
        normalize_text(text)
    )
    take_qty = find_number_near(
        "zabrane|biore|biorę|bierze|wezme|wezmę|wzialem|wziąłem|wzielam|wzięłam|pobieram|pobralem|pobrałam|odebralem|odebrałem|odebralam|odebrałam|odbieram",
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
            return missing_msg

    if returned_qty is not None:
        trip = active_trip(db, user_id)
        if not trip:
            responses.append("Brak aktywnej trasy do zamknięcia.")
        elif returned_qty > int(trip["qty"]):
            responses.append(f"❌ Błąd: oddajesz więcej ({returned_qty}) niż wziąłeś/wzięłaś ({trip['qty']}).")
        else:
            end_time, hours, late_hours, rate, earned = calc_trip(trip["start"], returned_qty)
            trip["end"] = end_time.isoformat()
            trip["returned"] = returned_qty
            trip["charged_inside"] = charged_inside
            trip["hours"] = hours
            trip["late_hours"] = late_hours
            trip["rate"] = rate
            trip["earned"] = earned

            ready_added, waiting_added = returned_to_inventory(returned_qty, charged_inside)
            state = "OK ✅" if late_hours <= 0 else f"SPÓŹNIONY ❌ ({fmt_hours(late_hours)})"
            responses.append(
                f"{name} zamknął/zamknęła trasę:\n"
                f"Oddane: {returned_qty}\n"
                f"Naładowane/gotowe: {ready_added}\n"
                f"Oczekujące: {waiting_added}\n"
                f"Czas: {fmt_hours(hours)}\n"
                f"Status: {state}\n"
                f"Zarobek: £{earned:.2f}"
            )

    if take_qty is not None:
        existing = active_trip(db, user_id)
        if existing:
            responses.append(f"Uwaga: {name} ma już aktywną trasę ({existing['qty']} baterii). Najpierw wpisz: oddalem X")
        else:
            start_time = now()
            deadline = start_time + timedelta(hours=TIME_LIMIT_HOURS)
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
                f"Limit: {TIME_LIMIT_HOURS}h\n"
                f"Deadline: {deadline.strftime('%H:%M')}"
            )

    save_db(db)

    if responses:
        return "\n\n".join(responses)

    return ""


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    keyboard = get_keyboard(update.message.from_user)
    reply = handle_command(update.message.text, update.message.from_user, update.message.chat_id)

    if isinstance(reply, dict):
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
                        text=f"🚨 Baterie będą gotowe za {ALARM_BEFORE_MINUTES} minut!\n🔋 Ilość: {qty}"
                    )
                    job["alarm_sent"] = True
                    job["status"] = "alarm_sent"
                    changed = True

                if not job.get("ready_sent") and current >= ready_at:
                    moved = move_charging_to_ready(qty)
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=f"✅ BATERIE GOTOWE\n🔋 Ilość: {moved}\n\n{status_report()}"
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
                    hours = (current - start).total_seconds() / 3600

                    if hours >= TIME_LIMIT_HOURS and not trip.get("alert_sent"):
                        chat_id = trip.get("chat_id") or load_group().get("chat_id")
                        if chat_id:
                            await app.bot.send_message(
                                chat_id=chat_id,
                                text=f"🚨 UWAGA!\n{trip['driver']} kończy czas / jest spóźniony!\n⏱ Limit: {TIME_LIMIT_HOURS}h"
                            )
                        trip["alert_sent"] = True
                        changed = True

            if changed:
                save_db(db)

        except Exception as e:
            print("driver_alerts error:", e)

        await asyncio.sleep(60)



async def weekly_report_scheduler(app: Application):
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
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "WKLEJ_TUTAJ_NOWY_TOKEN":
        print("BRAK TOKENA TELEGRAM — wklej token w pliku albo ustaw TELEGRAM_TOKEN")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.COMMAND, text_handler))
    print("Telegram Lime Battery Bot PRO v6.1 działa...")
    application.run_polling()


if __name__ == "__main__":
    main()


