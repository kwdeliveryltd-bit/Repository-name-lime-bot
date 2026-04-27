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
DRIVER_FLOW_FILE = "telegram_driver_flow.json"
RESET_WIZARD_FILE = "telegram_reset_wizard.json"
WEEKLY_REPORT_FILE = "telegram_weekly_report.json"

# Wpisz swoje ID z Telegrama po komendzie: moj id
# Przykład: ADMIN_IDS = {"123456789"}
ADMIN_IDS = set()

USER_STATE = {}
TZ = ZoneInfo("Europe/London")

BASE_RATE = 2.00
PENALTY_PER_HOUR = 0.10
TIME_LIMIT_HOURS = 5
MIN_RATE = 1.00

CHARGE_TIME_HOURS = 4.5
ALARM_BEFORE_MINUTES = 15
LOW_READY_LIMIT = 50
CHARGER_CAPACITY = 133


def now():
    return datetime.now(TZ)


def normalize_text(text):
    return (
        (text or "")
        .lower()
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
        "updated_at": None,
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


def load_driver_flow():
    return load_json(DRIVER_FLOW_FILE, {})


def save_driver_flow(data):
    save_json(DRIVER_FLOW_FILE, data)


def clear_driver_flow(user_id):
    data = load_driver_flow()
    data.pop(str(user_id), None)
    save_driver_flow(data)


def load_reset_wizard():
    return load_json(RESET_WIZARD_FILE, {})


def save_reset_wizard(data):
    save_json(RESET_WIZARD_FILE, data)


def clear_reset_wizard(user_id):
    data = load_reset_wizard()
    data.pop(str(user_id), None)
    save_reset_wizard(data)


def is_admin(user):
    # Produkcyjnie ADMIN_IDS NIE może być puste.
    return str(user.id) in ADMIN_IDS


def get_driver_name(user):
    return user.full_name or user.first_name or str(user.id)


def only_number(text):
    return re.fullmatch(r"\s*\d+\s*", text or "") is not None


def number_from_text(text):
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def find_number_after(words, text):
    m = re.search(rf"(?:{words})\s+(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def find_number_before(words, text):
    m = re.search(rf"(\d+)\s+(?:{words})", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def find_number_near(words, text):
    qty = find_number_after(words, text)
    if qty is not None:
        return qty
    return find_number_before(words, text)


def fmt_hours(hours):
    total_minutes = int(round(hours * 60))
    return f"{total_minutes // 60}h {total_minutes % 60}min"


def fmt_dt(dt):
    return dt.astimezone(TZ).strftime("%H:%M")


def parse_time_today(time_text):
    try:
        cleaned = str(time_text).replace(".", ":")
        hour, minute = cleaned.split(":")
        hour = int(hour)
        minute = int(minute)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return now().replace(hour=hour, minute=minute, second=0, microsecond=0)
    except Exception:
        return None


def parse_wizard_time(text):
    t = normalize_text(text).strip()
    if t in ["teraz", "now"]:
        return now()
    m = re.search(r"(\d{1,2})[:.](\d{2})", text or "")
    if not m:
        return None
    return parse_time_today(f"{m.group(1)}:{m.group(2)}")


def get_keyboard(user=None):
    base = [
        ["Zabrane", "Oddane"],
        ["Gotowe", "Ładowarka", "Oczekują"],
        ["Pomoc"],
    ]
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


def admin_text_action(t):
    if t in ["ogloszenie", "ogłoszenie"]:
        return "ogloszenie"
    if t == "alert":
        return "alert"
    return None


def active_in_transit():
    db = load_db()
    return sum(int(t.get("qty", 0)) for t in db.get("trips", []) if t.get("end") is None)


def active_trip(db, user_id):
    for trip in reversed(db.get("trips", [])):
        if trip.get("user_id") == str(user_id) and trip.get("end") is None:
            return trip
    return None


def active_trip_details():
    db = load_db()
    details = {}
    for trip in db.get("trips", []):
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

    db = load_db()
    current = now()
    active_info = []
    for trip in db.get("trips", []):
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

    if active_info:
        lines += ["", "Kierowcy w trasie:"]
        for driver, qty, left_txt in sorted(active_info):
            lines.append(f"• {driver}: {qty} baterii ({left_txt})")

    lines += ["", f"🧮 Razem policzone: {counted}"]
    if diff > 0:
        lines.append(f"⚠️ Brakuje do depo: {diff}")
    elif diff < 0:
        lines.append(f"🚨 Nadwyżka ponad depo: {abs(diff)}")
    else:
        lines.append("✅ Zgadza się z depo")

    if ready < LOW_READY_LIMIT:
        lines += ["", f"🚨 ALARM: mało gotowych baterii ({ready})"]

    return "\n".join(lines)


def calc_trip(start_iso, qty):
    start = datetime.fromisoformat(start_iso)
    end = now()
    hours = (end - start).total_seconds() / 3600
    late_hours = max(0, hours - TIME_LIMIT_HOURS)
    penalty_steps = math.ceil(late_hours) if late_hours > 0 else 0
    rate = max(MIN_RATE, BASE_RATE - (penalty_steps * PENALTY_PER_HOUR))
    earned = int(qty) * rate
    return end, hours, late_hours, rate, earned


def take_from_ready(qty):
    inv = load_inventory()
    inv["ready"] = max(0, int(inv.get("ready", 0)) - int(qty))
    save_inventory(inv)


def charger_free_slots():
    inv = load_inventory()
    charging = int(inv.get("charging", 0))
    return max(0, CHARGER_CAPACITY - charging)


def move_waiting_to_charging(qty):
    inv = load_inventory()
    qty = int(qty)
    waiting = int(inv.get("waiting", 0))
    moved = min(qty, waiting)
    warning = "" if waiting >= qty else f"\n⚠️ Uwaga: oczekujących było tylko {waiting}, więc przeniesiono {moved}."
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


def add_charging_job_from_return(chat_id, qty):
    qty = int(qty)
    if qty <= 0:
        return
    start = now()
    ready_at = start + timedelta(hours=CHARGE_TIME_HOURS)
    alarm_at = ready_at - timedelta(minutes=ALARM_BEFORE_MINUTES)
    data = load_jobs()
    jobs = data.setdefault("jobs", [])
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
        "source": "return",
    })
    save_jobs(data)


def set_inventory_command(text):
    t = normalize_text(text)
    inv = load_inventory()
    changed = False
    notes = []

    qty = find_number_near("depo|depot|magazyn|mamy|total", t)
    if qty is not None:
        inv["depot_total"] = qty
        changed = True
        notes.append(f"✅ Depo total zapisane: {qty}")

    qty = find_number_near("gotowe|gotowych", t)
    if qty is not None:
        inv["ready"] = qty
        changed = True
        notes.append(f"✅ Gotowe zapisane: {qty}")

    qty = find_number_near("oczekuje|oczekuja|oczekujace|oczekujacych", t)
    if qty is not None:
        inv["waiting"] = qty
        changed = True
        notes.append(f"✅ Oczekujące zapisane: {qty}")

    qty = find_number_near("ladowarka|ladowarki|w ladowarkach|laduje sie|laduja sie", t)
    if qty is not None:
        inv["charging"] = qty
        changed = True
        notes.append(f"✅ W ładowarkach zapisane: {qty}")

    if changed:
        save_inventory(inv)
        return "\n".join(notes) + "\n\n" + status_report()
    return None


def update_all_command(text, reset_trips=False):
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
        return "❌ Brakuje danych: " + ", ".join(missing) + "\n\nUżyj:\naktualizacja depo 505 gotowe 197 oczekuje 114 ladowarki 133"

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


def restore_charging_command(text, chat_id):
    t = normalize_text(text.strip())
    m = re.search(r"^(?:ladowarki|ladowarka|ladowanie|wlozone)\s+(\d+)\s+start\s+(\d{1,2}[:.]\d{2})\s*$", t)
    if not m:
        return None
    qty = int(m.group(1))
    start_time = parse_time_today(m.group(2))
    if start_time is None:
        return "🚨 BŁĄD: niepoprawna godzina startu ładowania. Użyj np. 13:24."
    if qty < 1:
        return "🚨 BŁĄD: ładowarki muszą mieć minimum 1 baterię."
    if qty > CHARGER_CAPACITY:
        return f"❌ Ładowarki mają limit {CHARGER_CAPACITY}."

    ready_time = start_time + timedelta(hours=CHARGE_TIME_HOURS)
    alarm_time = ready_time - timedelta(minutes=ALARM_BEFORE_MINUTES)
    data = load_jobs()
    jobs = data.setdefault("jobs", [])
    jobs.append({
        "id": len(jobs) + 1,
        "qty": qty,
        "chat_id": chat_id,
        "start_at": start_time.isoformat(),
        "ready_at": ready_time.isoformat(),
        "alarm_at": alarm_time.isoformat(),
        "alarm_sent": now() >= alarm_time,
        "ready_sent": False,
        "status": "alarm_sent" if now() >= alarm_time else "charging",
        "manual": True,
        "restored": True,
    })
    save_jobs(data)

    inv = load_inventory()
    inv["charging"] = int(inv.get("charging", 0)) + qty
    save_inventory(inv)

    return (
        "✅ ODTWORZONO ŁADOWANIE\n\n"
        f"🔌 W ładowarkach: {qty}\n"
        f"Start: {fmt_dt(start_time)}\n"
        f"Gotowe: {fmt_dt(ready_time)}\n"
        f"Alarm: {fmt_dt(alarm_time)}\n\n"
        f"{status_report()}"
    )


def restore_trip_command(text, chat_id):
    raw = text.strip()
    m = re.search(
        r"^(?:trasa\s+)?(.+?)\s+(?:(?:zabrane|w trasie)\s+)?(\d+)\s+start\s+(\d{1,2}[:.]\d{2})\s*$",
        raw,
        re.IGNORECASE,
    )
    if not m:
        return None

    driver = m.group(1).strip()
    qty = int(m.group(2))
    start_time = parse_time_today(m.group(3))
    if start_time is None:
        return "🚨 BŁĄD: niepoprawna godzina startu. Użyj np. 14:52."
    if qty < 1:
        return "🚨 BŁĄD: trasa musi mieć minimum 1 baterię."

    db = load_db()
    wanted = normalize_text(driver)
    db["trips"] = [
        trip for trip in db.get("trips", [])
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
        "restored": True,
    })
    save_db(db)

    deadline = start_time + timedelta(hours=TIME_LIMIT_HOURS)
    return (
        "✅ ODTWORZONO TRASĘ\n\n"
        f"🚗 {driver}: {qty} baterii\n"
        f"Start: {fmt_dt(start_time)}\n"
        f"Deadline: {fmt_dt(deadline)}\n"
        "📌 Gotowe NIE zostały pomniejszone.\n\n"
        f"{status_report()}"
    )


def update_route_command(text, chat_id):
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
        m = re.search(r"(?:usun|usuń)\s+trasa\s+(.+?)\s*$", raw, re.IGNORECASE)
        if not m:
            return "❌ Użyj: usun trasa Pawel"
        driver_input = m.group(1).strip()
        qty = 0
    else:
        m = re.search(r"(?:(?:aktualizacja|korekta)\s+)?trasa\s+(.+?)\s+(\d+)\s*$", raw, re.IGNORECASE)
        if not m:
            return "❌ Użyj: aktualizacja trasa Waldek 60"
        driver_input = m.group(1).strip()
        qty = int(m.group(2))

    db = load_db()
    wanted = normalize_text(driver_input)
    matched_driver = None
    for trip in db.get("trips", []):
        if trip.get("end") is None:
            existing = trip.get("driver", "")
            existing_n = normalize_text(existing)
            if wanted == existing_n or wanted in existing_n or existing_n in wanted:
                matched_driver = existing
                break
    final_driver = matched_driver or driver_input

    db["trips"] = [
        trip for trip in db.get("trips", [])
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
            "manual": True,
        })
        msg = f"✅ AKTUALIZACJA TRASY\n\n🚗 {final_driver}: {qty} baterii\n📌 Magazyn NIE został pomniejszony."
    else:
        msg = f"✅ USUNIĘTO TRASĘ\n\n🚗 {final_driver}\n📌 Magazyn NIE został zmieniony."

    save_db(db)
    return f"{msg}\n\n{status_report()}"


def start_pickup_flow(user, chat_id, pending_qty=None):
    data = load_driver_flow()
    data[str(user.id)] = {
        "type": "pickup",
        "chat_id": chat_id,
        "step": "ready",
        "driver": get_driver_name(user),
        "created_at": now().isoformat(),
        "ready": None,
        "charging": None,
        "waiting": None,
        "qty": pending_qty,
    }
    save_driver_flow(data)
    return (
        "🚗 KONTROLA PRZED ZABRANIEM\n\n"
        "Najpierw sprawdzamy magazyn, żeby nie rozjechały się stany.\n\n"
        + (f"Zapamiętałem, że chcesz zabrać: {pending_qty}.\n\n" if pending_qty else "")
        + "1/4 Podaj ilość GOTOWYCH baterii:"
    )


def start_return_flow(user, chat_id):
    db = load_db()
    trip = active_trip(db, user.id)
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
        "to_charging": 0,
        "to_waiting": 0,
        "take_extra": 0,
        "next_action": None,
    }
    save_driver_flow(data)
    return (
        "🔁 KONTROLA ZWROTU\n\n"
        f"Masz w trasie: {route_qty} baterii.\n\n"
        "1/3 Ile baterii oddajesz TERAZ?\n"
        "Bot sam rozdzieli: ładowarki / oczekujące."
    )


def handle_driver_flow(text, user, chat_id):
    data = load_driver_flow()
    key = str(user.id)
    flow = data.get(key)
    if not flow:
        return None

    t = normalize_text(text).strip()
    if t in ["anuluj", "cancel", "stop"]:
        clear_driver_flow(user.id)
        return "❌ Przerwano kontrolę. Możesz zacząć od nowa."

    text_steps = (
        flow.get("type") == "pickup" and flow.get("step") == "confirm_take"
    ) or (
        flow.get("type") == "return_auto" and flow.get("step") in ["next_action", "confirm_return_auto"]
    )

    qty = number_from_text(text)
    if not text_steps:
        if qty is None:
            return "Wpisz liczbę albo wpisz: anuluj"
        if qty < 0:
            return "Liczba nie może być ujemna."

    if flow.get("type") == "pickup":
        inv = load_inventory()

        if flow["step"] == "ready":
            flow["ready"] = qty
            flow["step"] = "charging"
            inv["ready"] = qty
            save_inventory(inv)
            data[key] = flow
            save_driver_flow(data)
            return f"✅ Gotowe zapisane: {qty}\n\n2/4 Podaj ilość baterii W ŁADOWARKACH:"

        if flow["step"] == "charging":
            if qty > CHARGER_CAPACITY:
                return f"❌ Ładowarki mają limit {CHARGER_CAPACITY}. Wpisz poprawną liczbę."
            flow["charging"] = qty
            flow["step"] = "waiting"
            inv["charging"] = qty
            save_inventory(inv)
            data[key] = flow
            save_driver_flow(data)
            return f"✅ Ładowarki zapisane: {qty}\n\n3/4 Podaj ilość baterii OCZEKUJĄCYCH:"

        if flow["step"] == "waiting":
            flow["waiting"] = qty
            inv["waiting"] = qty
            save_inventory(inv)

            if flow.get("qty"):
                flow["step"] = "confirm_take"
                data[key] = flow
                save_driver_flow(data)
                return (
                    "✅ Stany zapisane.\n\n"
                    f"Gotowe: {flow['ready']}\n"
                    f"W ładowarkach: {flow['charging']}\n"
                    f"Oczekujące: {flow['waiting']}\n"
                    f"Zabrane: {flow['qty']}\n\n"
                    "Wpisz: zatwierdz\nalbo: anuluj"
                )

            flow["step"] = "take_qty"
            data[key] = flow
            save_driver_flow(data)
            return (
                "✅ Stany zapisane.\n\n"
                f"Gotowe: {flow['ready']}\n"
                f"W ładowarkach: {flow['charging']}\n"
                f"Oczekujące: {flow['waiting']}\n\n"
                "4/4 Ile baterii ZABIERASZ?"
            )

        if flow["step"] == "confirm_take":
            if t not in ["zatwierdz", "zatwierdź", "ok", "potwierdz", "potwierdź"]:
                return "Wpisz: zatwierdz albo anuluj"
            qty_take = int(flow.get("qty", 0))
            return finish_pickup(user, chat_id, qty_take)

        if flow["step"] == "take_qty":
            return finish_pickup(user, chat_id, qty)

        clear_driver_flow(user.id)
        return "⚠️ Kreator pobrania się zaciął. Wpisz Zabrane jeszcze raz."

    if flow.get("type") == "return_auto":
        route_qty = int(flow.get("route_qty", 0))

        if flow["step"] == "returned_qty":
            if qty < 1:
                return "🚨 Nie można oddać 0. Minimum to 1."
            if qty > route_qty:
                return f"❌ Nie możesz oddać {qty}, bo w trasie masz {route_qty}."

            free = charger_free_slots()
            to_charging = min(qty, free)
            to_waiting = qty - to_charging

            flow["returned"] = qty
            flow["to_charging"] = to_charging
            flow["to_waiting"] = to_waiting
            flow["step"] = "next_action"
            data[key] = flow
            save_driver_flow(data)

            return (
                "✅ BOT ROZDZIELIŁ ZWROT:\n\n"
                f"Oddane teraz: {qty}\n"
                f"🔌 Wolne miejsca w ładowarkach: {free}\n"
                f"➡️ Do ładowarek: {to_charging}\n"
                f"➡️ Oczekujące: {to_waiting}\n"
                f"Zostaje z poprzedniej trasy: {route_qty - qty}\n\n"
                "2/3 Co dalej?\n"
                "Wpisz: koniec\nalbo: dobieram X"
            )

        if flow["step"] == "next_action":
            returned = int(flow.get("returned", 0))
            remaining_after_return = route_qty - returned

            if t in ["koniec", "zakoncz", "zakończ", "zamknij"]:
                flow["next_action"] = "finish"
                flow["take_extra"] = 0
            else:
                m = re.search(r"(?:dobieram|dobrac|dobrać|dobierasz|biore|biorę|zabieram)\s+(\d+)", t)
                if not m:
                    return "Wpisz: koniec albo dobieram X"
                extra = int(m.group(1))
                if extra < 1:
                    return "🚨 Dobranie musi być minimum 1."
                inv = load_inventory()
                ready = int(inv.get("ready", 0))
                if extra > ready:
                    return f"❌ Za mało gotowych do dobrania. Gotowe: {ready}, chcesz dobrać: {extra}"
                flow["next_action"] = "take_extra"
                flow["take_extra"] = extra

            flow["step"] = "confirm_return_auto"
            data[key] = flow
            save_driver_flow(data)

            finish_route = flow["next_action"] == "finish"
            ready_from_remaining = remaining_after_return if finish_route else 0
            final_route = 0 if finish_route else remaining_after_return + int(flow.get("take_extra", 0))

            return (
                "✅ PODSUMOWANIE:\n\n"
                f"Oddane: {returned}\n"
                f"Do ładowarek: {flow.get('to_charging', 0)}\n"
                f"Oczekujące: {flow.get('to_waiting', 0)}\n"
                f"Reszta jako gotowe przy końcu trasy: {ready_from_remaining}\n"
                f"Dobrane: {flow.get('take_extra', 0)}\n"
                f"Nowy stan w trasie: {final_route}\n\n"
                "3/3 Wpisz: zatwierdz albo anuluj"
            )

        if flow["step"] == "confirm_return_auto":
            if t not in ["zatwierdz", "zatwierdź", "ok", "potwierdz", "potwierdź"]:
                return "Wpisz: zatwierdz albo anuluj"
            return finish_return_auto(user, chat_id, flow)

    return None


def finish_pickup(user, chat_id, qty_take):
    qty_take = int(qty_take)
    if qty_take < 1:
        return "🚨 Nie można zabrać 0. Minimum to 1."

    inv = load_inventory()
    ready = int(inv.get("ready", 0))
    if qty_take > ready:
        return f"❌ Za mało gotowych baterii. Gotowe: {ready}, próbujesz zabrać: {qty_take}"

    db = load_db()
    existing = active_trip(db, user.id)
    if existing:
        clear_driver_flow(user.id)
        return f"Uwaga: masz już aktywną trasę ({existing['qty']} baterii). Najpierw wpisz: Oddane"

    start_time = now()
    deadline = start_time + timedelta(hours=TIME_LIMIT_HOURS)
    db["trips"].append({
        "driver": get_driver_name(user),
        "user_id": str(user.id),
        "chat_id": chat_id,
        "start": start_time.isoformat(),
        "qty": qty_take,
        "end": None,
        "alert_sent": False,
    })
    save_db(db)

    inv["ready"] = ready - qty_take
    save_inventory(inv)
    clear_driver_flow(user.id)

    return (
        f"{get_driver_name(user)} ✅\n"
        f"Start: {start_time.strftime('%H:%M')}\n"
        f"Pobrane: {qty_take}\n"
        f"Limit: {TIME_LIMIT_HOURS}h\n"
        f"Deadline: {deadline.strftime('%H:%M')}\n\n"
        f"{status_report()}"
    )


def finish_return_auto(user, chat_id, flow):
    db = load_db()
    trip = active_trip(db, user.id)
    if not trip:
        clear_driver_flow(user.id)
        return "Brak aktywnej trasy do rozliczenia."

    original_qty = int(trip.get("qty", 0))
    returned = int(flow.get("returned", 0))
    to_charging = int(flow.get("to_charging", 0))
    to_waiting = int(flow.get("to_waiting", 0))
    take_extra = int(flow.get("take_extra", 0))
    remaining_after_return = original_qty - returned
    finish_route = flow.get("next_action") == "finish"
    ready_from_remaining = remaining_after_return if finish_route else 0
    final_route_qty = 0 if finish_route else remaining_after_return + take_extra

    if returned > original_qty:
        clear_driver_flow(user.id)
        return f"❌ Błąd: oddajesz więcej ({returned}) niż masz w trasie ({original_qty})."

    free = charger_free_slots()
    if to_charging > free:
        clear_driver_flow(user.id)
        return f"❌ W międzyczasie zmieniły się ładowarki. Wolne miejsca: {free}. Zacznij zwrot od nowa."

    inv = load_inventory()
    ready_before = int(inv.get("ready", 0))
    if take_extra > ready_before:
        clear_driver_flow(user.id)
        return f"❌ Za mało gotowych do dobrania. Gotowe: {ready_before}, chcesz dobrać: {take_extra}"

    end_time, hours, late_hours, rate, earned = calc_trip(trip["start"], returned)

    closed_part = dict(trip)
    closed_part["qty"] = returned
    closed_part["end"] = end_time.isoformat()
    closed_part["returned"] = returned
    closed_part["charged_inside"] = 0
    closed_part["hours"] = hours
    closed_part["late_hours"] = late_hours
    closed_part["rate"] = rate
    closed_part["earned"] = earned
    closed_part["return_auto"] = True

    if final_route_qty > 0:
        trip["qty"] = final_route_qty
        trip["start"] = end_time.isoformat()
        trip["alert_sent"] = False
        trip["alert_60_sent"] = False
        trip["alert_15_sent"] = False
        trip.pop("last_overdue_alert_at", None)
        db["trips"].append(closed_part)
    else:
        trip["end"] = end_time.isoformat()
        trip["returned"] = original_qty
        trip["charged_inside"] = ready_from_remaining
        trip["hours"] = hours
        trip["late_hours"] = late_hours
        trip["rate"] = rate
        trip["earned"] = earned

    save_db(db)

    inv["charging"] = int(inv.get("charging", 0)) + to_charging
    inv["waiting"] = int(inv.get("waiting", 0)) + to_waiting
    inv["ready"] = int(inv.get("ready", 0)) + ready_from_remaining - take_extra
    save_inventory(inv)

    if to_charging > 0:
        add_charging_job_from_return(chat_id, to_charging)

    clear_driver_flow(user.id)
    state = "OK ✅" if late_hours <= 0 else f"SPÓŹNIONY ❌ ({fmt_hours(late_hours)})"

    return (
        "✅ ZWROT ZAPISANY\n\n"
        f"Kierowca: {get_driver_name(user)}\n"
        f"Oddane: {returned}\n"
        f"Do ładowarek: {to_charging}\n"
        f"Oczekujące: {to_waiting}\n"
        f"Reszta trasy jako gotowe: {ready_from_remaining}\n"
        f"Dobrane: {take_extra}\n"
        f"Zostaje w trasie: {final_route_qty}\n"
        f"Czas: {fmt_hours(hours)}\n"
        f"Status: {state}\n"
        f"Zarobek za oddane: £{earned:.2f}\n\n"
        f"{status_report()}"
    )


def start_reset_wizard(user, chat_id):
    save_db({"trips": []})
    save_inventory({"depot_total": 0, "ready": 0, "waiting": 0, "charging": 0, "updated_at": None})
    save_jobs({"jobs": []})
    save_driver_flow({})

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
    }
    save_reset_wizard(data)

    return "✅ RESET SYSTEMU ZROBIONY\n\nTeraz uzupełnimy dane krok po kroku.\n\n1/6 Podaj stan DEPO, np.:\n504"


def handle_reset_wizard(text, user, chat_id):
    data = load_reset_wizard()
    key = str(user.id)
    wiz = data.get(key)
    if not wiz:
        return None
    if not is_admin(user):
        return None

    t = normalize_text(text).strip()
    if t in ["anuluj", "cancel", "stop"]:
        clear_reset_wizard(user.id)
        return "❌ Reset wizard przerwany."

    if t in ["zatwierdz", "zatwierdź", "ok", "koniec"]:
        if wiz.get("step") != "trips":
            return "Jeszcze nie skończyliśmy. Uzupełnij aktualny krok albo wpisz: anuluj"

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
                "reset_wizard": True,
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
                "reset_wizard": True,
            })
        save_db(db)
        clear_reset_wizard(user.id)
        return "✅ RESET ZATWIERDZONY\n\nStany i trasy zapisane.\n\n" + status_report()

    step = wiz.get("step")

    if step == "depo":
        qty = number_from_text(text)
        if qty is None:
            return "Podaj samą liczbę DEPO, np. 504"
        wiz["depot_total"] = qty
        wiz["step"] = "charging"
        data[key] = wiz
        save_reset_wizard(data)
        return "✅ Depo zapisane.\n\n2/6 Podaj ile jest W ŁADOWARKACH, np.:\n133\n\nJeśli zero, wpisz: 0"

    if step == "charging":
        qty = number_from_text(text)
        if qty is None:
            return "Podaj samą liczbę baterii w ładowarkach, np. 133"
        if qty < 0:
            return "Liczba nie może być ujemna."
        if qty > CHARGER_CAPACITY:
            return f"❌ Ładowarki mają limit {CHARGER_CAPACITY}."
        wiz["charging"] = qty
        if qty == 0:
            wiz["charge_start"] = None
            wiz["step"] = "ready"
            data[key] = wiz
            save_reset_wizard(data)
            return "✅ Ładowarki zapisane: 0\n\n3/6 Podaj ile jest GOTOWYCH, np.:\n77"
        wiz["step"] = "charge_start"
        data[key] = wiz
        save_reset_wizard(data)
        return "✅ Ładowarki zapisane.\n\n3/6 Podaj START ładowania, np.:\n06:30\n\nMożesz też wpisać: teraz"

    if step == "charge_start":
        dt = parse_wizard_time(text)
        if not dt:
            return "Podaj godzinę startu ładowania, np. 06:30 albo wpisz: teraz"
        wiz["charge_start"] = dt.isoformat()
        wiz["step"] = "ready"
        data[key] = wiz
        save_reset_wizard(data)
        return "✅ Start ładowania zapisany.\n\n4/6 Podaj ile jest GOTOWYCH, np.:\n77"

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
        return "✅ Gotowe zapisane.\n\n5/6 Podaj ile jest OCZEKUJĄCYCH, np.:\n275"

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
        patterns = [
            r"^trasa\s+(.+?)\s+(\d+)\s+start\s+(\d{1,2}[:.]\d{2})$",
            r"^(.+?)\s+zabrane\s+(\d+)\s+start\s+(\d{1,2}[:.]\d{2})$",
            r"^(.+?)\s+(\d+)\s+start\s+(\d{1,2}[:.]\d{2})$",
        ]
        m = None
        for p in patterns:
            m = re.search(p, text.strip(), re.IGNORECASE)
            if m:
                break
        if not m:
            return "Nie rozumiem trasy.\n\nUżyj np.:\ntrasa Adam 100 start 07:39\nalbo wpisz: zatwierdz"

        driver = m.group(1).strip()
        qty = int(m.group(2))
        dt = parse_wizard_time(m.group(3))
        if not dt:
            return "Niepoprawna godzina. Użyj np. 07:39"
        if qty < 1:
            return "Trasa musi mieć minimum 1 baterię."

        wiz.setdefault("trips", []).append({"driver": driver, "qty": qty, "start": dt.isoformat()})
        data[key] = wiz
        save_reset_wizard(data)
        total_routes = sum(int(x["qty"]) for x in wiz.get("trips", []))
        return (
            f"✅ Dodano trasę:\n{driver}: {qty} baterii, start {fmt_dt(dt)}\n\n"
            f"Razem w trasie z resetu: {total_routes}\n\n"
            "Dodaj następnego kierowcę albo wpisz: zatwierdz"
        )

    return "Błąd kreatora resetu. Wpisz: reset"


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
        jobs.append({
            "id": len(jobs) + 1,
            "qty": moved,
            "chat_id": chat_id,
            "start_at": start.isoformat(),
            "ready_at": ready.isoformat(),
            "alarm_at": alarm.isoformat(),
            "alarm_sent": False,
            "ready_sent": False,
            "status": "charging",
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


def active_trips_text():
    db = load_db()
    current = now()
    lines = []
    for trip in db.get("trips", []):
        if trip.get("end") is None:
            start = datetime.fromisoformat(trip["start"])
            deadline = start + timedelta(hours=TIME_LIMIT_HOURS)
            left_minutes = int((deadline - current).total_seconds() // 60)
            if left_minutes >= 0:
                left_txt = f"zostało {left_minutes // 60}h {left_minutes % 60}min"
                state = "OK ✅"
            else:
                late = abs(left_minutes)
                left_txt = f"spóźnienie {late // 60}h {late % 60}min"
                state = "SPÓŹNIONY ❌"
            lines.append(
                f"{trip['driver']}: {trip['qty']} baterii, start {fmt_dt(start)}, deadline {fmt_dt(deadline)}, {left_txt}, {state}"
            )
    return "🚚 AKTYWNE TRASY\n" + ("\n".join(lines) if lines else "Brak aktywnych tras.")


def clock_report():
    current = now()
    lines = ["⏱️ ZEGAREK", "", "🚗 KIEROWCY:"]
    db = load_db()
    active = [trip for trip in db.get("trips", []) if trip.get("end") is None]
    if not active:
        lines.append("Brak aktywnych tras.")
    else:
        for trip in active:
            start = datetime.fromisoformat(trip["start"])
            deadline = start + timedelta(hours=TIME_LIMIT_HOURS)
            left_minutes = int((deadline - current).total_seconds() // 60)
            if left_minutes >= 0:
                left_txt = f"zostało {left_minutes // 60}h {left_minutes % 60}min"
                state = "OK ✅"
            else:
                late = abs(left_minutes)
                left_txt = f"po czasie {late // 60}h {late % 60}min"
                state = "KONIEC CZASU ❌"
            lines.append(
                f"• {trip.get('driver', 'Nieznany')}: {trip.get('qty', 0)} baterii | start {fmt_dt(start)} | deadline {fmt_dt(deadline)} | {left_txt} | {state}"
            )

    lines += ["", "🔋 ŁADOWARKI:"]
    jobs = [j for j in load_jobs().get("jobs", []) if j.get("status") in ["charging", "alarm_sent"]]
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
            lines.append(f"• ID {job.get('id')}: {job.get('qty', 0)} baterii | gotowe {fmt_dt(ready_at)} | {left_txt}")
    return "\n".join(lines)


def charging_status():
    jobs = [j for j in load_jobs().get("jobs", []) if j.get("status") in ["charging", "alarm_sent"]]
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
    return bool(q) and (q == saved or q in saved or saved in q)


def driver_report(driver_query, period="dzis"):
    db = load_db()
    start_period, title = period_start(period)
    matched_name = None
    batteries = trips = late = 0
    earned = hours_total = 0.0

    for trip in db.get("trips", []):
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
        f"📊 RAPORT {title}\n👤 Kierowca: {matched_name}\n\n"
        f"Oddane: {batteries} baterii\nTrasy: {trips}\nŚredni czas: {avg}\nSpóźnienia: {late}\nZarobek: £{earned:.2f}"
    )


def report(period="dzis"):
    db = load_db()
    start_period, title = period_start(period)
    summary = {}
    for trip in db.get("trips", []):
        if trip.get("end") is None:
            continue
        end = datetime.fromisoformat(trip["end"])
        if end < start_period:
            continue
        s = summary.setdefault(trip.get("driver", "Nieznany"), {"batteries": 0, "trips": 0, "earned": 0.0, "late": 0, "hours": 0.0})
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
        lines += [name + ":", f"Oddane: {s['batteries']} baterii", f"Trasy: {s['trips']}", f"Średni czas: {avg}", f"Spóźnienia: {s['late']}", f"Zarobek: £{s['earned']:.2f}", ""]
    lines += ["━━━━━━━━━━", "TOTAL FIRMA:", f"Oddane baterie: {total_batt}", f"Trasy: {total_trips}", f"Do wypłaty: £{total_earned:.2f}", "━━━━━━━━━━"]
    return "\n".join(lines)


def weekly_all_drivers_report():
    return report("tydzien").replace("📊 RAPORT TYDZIEŃ", "📊 RAPORT TYGODNIOWY\n🕕 Automatyczny raport: poniedziałek 06:00")


def leaderboard(period="dzis"):
    db = load_db()
    start_period, title = period_start(period)
    scores = {}
    for trip in db.get("trips", []):
        if trip.get("end") is None:
            continue
        if datetime.fromisoformat(trip["end"]) < start_period:
            continue
        scores[trip.get("driver", "Nieznany")] = scores.get(trip.get("driver", "Nieznany"), 0) + int(trip.get("returned", 0))
    if not scores:
        return f"🏆 RANKING {title}\nBrak danych."
    lines = [f"🏆 RANKING {title}", ""]
    for i, (name, qty) in enumerate(sorted(scores.items(), key=lambda x: x[1], reverse=True), start=1):
        lines.append(f"{i}. {name}: {qty} baterii")
    return "\n".join(lines)


def help_text():
    return (
        "📌 KOMENDY / MENU:\n\n"
        "Kliknij przycisk i wpisz samą liczbę.\n\n"
        "🚗 KIEROWCY:\n"
        "Zabrane → bot pyta: gotowe, ładowarki, oczekujące, ilość zabrania.\n"
        "Oddane → wpisz ile oddajesz, potem: koniec albo dobieram X.\n\n"
        "👑 ADMIN:\n"
        "reset — kreator uzupełniania danych\n"
        "aktualizacja depo 505 gotowe 197 oczekuje 114 ladowarki 133\n"
        "aktualizacja trasa Waldek 60\n"
        "ladowarki 57 start 13:24\n"
        "trasa Adam 100 start 07:39\n\n"
        "📊 RAPORT / STAN:\n"
        "status / stan / zegarek / trasy / raport dzis / ranking dzis\n"
        "raport Jan Kowalski / raport tydzien Jan Kowalski"
    )


def handle_command(text, user, chat_id):
    t = normalize_text(text).strip()
    user_id = str(user.id)

    if t in ["reset", "reset wszystko", "reset system"]:
        if not is_admin(user):
            return "❌ Reset jest tylko dla administratora."
        USER_STATE.pop(user_id, None)
        clear_driver_flow(user_id)
        clear_reset_wizard(user_id)
        return start_reset_wizard(user, chat_id)

    if t in ["anuluj", "cancel", "stop"]:
        USER_STATE.pop(user_id, None)
        clear_driver_flow(user_id)
        clear_reset_wizard(user_id)
        return "❌ Przerwano aktualny kreator. Możesz zacząć od nowa."

    reset_wizard_reply = handle_reset_wizard(text, user, chat_id)
    if reset_wizard_reply:
        return reset_wizard_reply

    flow_reply = handle_driver_flow(text, user, chat_id)
    if flow_reply:
        return flow_reply

    if user_id in USER_STATE:
        state = USER_STATE.pop(user_id)
        action = state.get("action")
        if action in ["gotowe", "ladowarka", "oczekuja", "depo"]:
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
            return {"send_to": group_id, "text": f"{prefix}\n\n{text.strip()}", "confirm": "✅ Wysłane."}

    button_action = BUTTON_ACTIONS.get(t)
    if button_action:
        if button_action == "depo" and not is_admin(user):
            return "❌ Depo jest tylko dla administratora."
        if button_action == "zabrane":
            USER_STATE.pop(user_id, None)
            return start_pickup_flow(user, chat_id)
        if button_action == "oddane":
            USER_STATE.pop(user_id, None)
            return start_return_flow(user, chat_id)
        USER_STATE[user_id] = {"action": button_action}
        labels = {
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

    if t in ["pomoc", "help", "/start", "start"]:
        return help_text()

    if t == "ustaw grupe":
        if not is_admin(user):
            return "❌ Tylko administrator może ustawić grupę."
        save_group(chat_id)
        return "✅ Ta grupa została ustawiona jako grupa główna dla alertów."

    if is_admin(user):
        restore_charge = restore_charging_command(text, chat_id)
        if restore_charge:
            return restore_charge

        restore_trip = restore_trip_command(text, chat_id)
        if restore_trip:
            return restore_trip

        route_reply = update_route_command(text, chat_id)
        if route_reply:
            return route_reply

        update_reply = update_all_command(text)
        if update_reply:
            return update_reply

    if "start" in t and is_admin(user):
        return (
            "❌ Nie rozumiem tej komendy ze startem.\n\n"
            "Użyj np.:\n"
            "ladowarki 45 start 13:15\n"
            "trasa Adam 100 start 07:39\n"
            "Adam zabrane 100 start 07:39"
        )

    if t.startswith("ogloszenie ") or t.startswith("alert "):
        if not is_admin(user):
            return "❌ Tylko administrator może wysyłać ogłoszenia i alerty."
        group_id = load_group().get("chat_id") or chat_id
        prefix = "📢 OGŁOSZENIE" if t.startswith("ogloszenie ") else "🚨 ALERT"
        original = text.split(" ", 1)[1].strip()
        return {"send_to": group_id, "text": f"{prefix}\n\n{original}", "confirm": "✅ Wysłane."}

    if t.startswith("raport"):
        words = t.split()
        period = "dzis"
        for p in ["dzis", "tydzien", "week", "miesiac", "month"]:
            if p in words:
                period = p
        name_query = re.sub(r"(?i)^\s*raport\s*", "", text).strip()
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

    if t in ["status", "stan"]:
        return status_report()

    if t in ["zegarek", "czas", "odliczanie"]:
        return clock_report()

    if t.startswith("trasy") or t.startswith("aktywni"):
        return active_trips_text()

    if t in ["ladowanie status", "status ladowania"]:
        return charging_status()

    charge_reply = add_charging_job(text, chat_id)
    if charge_reply:
        return charge_reply

    admin_inventory_words = ["depo", "depot", "magazyn", "mamy", "total"]
    if any(word in t for word in admin_inventory_words):
        if not is_admin(user):
            return "❌ Depo jest tylko dla administratora."
        inventory_reply = set_inventory_command(text)
        if inventory_reply:
            return inventory_reply

    driver_inventory_words = [
        "gotowe", "gotowych",
        "oczekuje", "oczekuja", "oczekujace", "oczekujacych",
        "ladowarka", "ladowarki", "w ladowarkach",
    ]
    if any(word in t for word in driver_inventory_words):
        inventory_reply = set_inventory_command(text)
        if inventory_reply:
            return inventory_reply

    returned_qty = find_number_near(
        "oddane|oddalem|oddałem|oddalam|oddałam|oddaje|oddaję|oddal|oddał|zwrot|zwrocilem|zwrocilam|zwracam",
        t,
    )
    take_qty = find_number_near(
        "zabrane|biore|biorę|bierze|wezme|wezmę|wzialem|wziąłem|wzielam|wzięłam|pobieram|pobralem|pobrałam|odebralem|odebrałem|odebralam|odebrałam|odbieram",
        t,
    )

    if returned_qty is not None:
        if returned_qty < 1:
            return "🚨 BŁĄD: nie można oddać 0 baterii. Minimum to 1."
        reply = start_return_flow(user, chat_id)
        flow_reply = handle_driver_flow(str(returned_qty), user, chat_id)
        return reply + ("\n\n" + flow_reply if flow_reply else "")

    if take_qty is not None:
        if take_qty < 1:
            return "🚨 BŁĄD: nie można zabrać 0 baterii. Minimum to 1."
        return start_pickup_flow(user, chat_id, pending_qty=take_qty)

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
                        text=(
                            "⏰ ZEGAREK ŁADOWANIA\n"
                            f"🔋 Baterie będą gotowe za {ALARM_BEFORE_MINUTES} minut!\n"
                            f"Ilość: {qty}\n"
                            f"Gotowe o: {fmt_dt(ready_at)}"
                        ),
                    )
                    job["alarm_sent"] = True
                    job["status"] = "alarm_sent"
                    changed = True

                if not job.get("ready_sent") and current >= ready_at:
                    moved = move_charging_to_ready(qty)
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "✅ BATERIE NAŁADOWANE\n"
                            f"🔋 Ilość: {moved}\n"
                            f"⏰ Gotowe od: {fmt_dt(ready_at)}\n\n"
                            f"{status_report()}"
                        ),
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

            for trip in db.get("trips", []):
                if trip.get("end") is not None:
                    continue

                start = datetime.fromisoformat(trip["start"])
                deadline = start + timedelta(hours=TIME_LIMIT_HOURS)
                qty = int(trip.get("qty", 0))
                driver = trip.get("driver", "Nieznany")
                chat_id = trip.get("chat_id") or load_group().get("chat_id")
                if not chat_id:
                    continue

                left_minutes = int((deadline - current).total_seconds() // 60)

                if 15 < left_minutes <= 60 and not trip.get("alert_60_sent"):
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "⏰ ZEGAREK KIEROWCY\n"
                            f"🚗 {driver}\n"
                            f"🔋 W trasie: {qty} baterii\n"
                            f"Zostało około: {left_minutes} min\n"
                            f"Deadline: {fmt_dt(deadline)}"
                        ),
                    )
                    trip["alert_60_sent"] = True
                    changed = True

                if 0 <= left_minutes <= 15 and not trip.get("alert_15_sent"):
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "🚨 ZA 15 MIN KONIEC CZASU\n"
                            f"🚗 {driver}\n"
                            f"🔋 W trasie: {qty} baterii\n"
                            f"Zostało: {left_minutes} min\n"
                            f"Deadline: {fmt_dt(deadline)}"
                        ),
                    )
                    trip["alert_15_sent"] = True
                    changed = True

                if left_minutes < 0 and not trip.get("alert_sent"):
                    late = abs(left_minutes)
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "❌ SKOŃCZYŁ CI SIĘ CZAS\n"
                            f"🚗 Kierowca: {driver}\n"
                            f"🔋 Baterie w trasie: {qty}\n"
                            f"Deadline był o: {fmt_dt(deadline)}\n"
                            f"Spóźnienie: {late // 60}h {late % 60}min"
                        ),
                    )
                    trip["alert_sent"] = True
                    trip["last_overdue_alert_at"] = current.isoformat()
                    changed = True

                if left_minutes < -30 and trip.get("alert_sent"):
                    last_iso = trip.get("last_overdue_alert_at")
                    last_dt = datetime.fromisoformat(last_iso) if last_iso else start
                    if (current - last_dt).total_seconds() >= 1800:
                        late = abs(left_minutes)
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                "🚨 NADAL PO CZASIE\n"
                                f"🚗 Kierowca: {driver}\n"
                                f"🔋 Baterie w trasie: {qty}\n"
                                f"Spóźnienie: {late // 60}h {late % 60}min"
                            ),
                        )
                        trip["last_overdue_alert_at"] = current.isoformat()
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

    if not ADMIN_IDS:
        print("UWAGA: ADMIN_IDS jest puste. Komendy admina będą zablokowane.")
        print("Najpierw wpisz w kodzie swoje Telegram ID, np. ADMIN_IDS =ADMIN_IDS = {"6030936882"}

    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(MessageHandler(filters.COMMAND, text_handler))
    print("Telegram Lime Battery Bot PRO fixed działa...")
    application.run_polling()


if __name__ == "__main__":
    main()
