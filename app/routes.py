"""Mizani routes: auth, queue, tickets, yard, manager, HR."""
import os
from datetime import datetime, date, timedelta
from functools import wraps
from io import BytesIO

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, abort)
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import func, or_

from .models import (db, User, Material, PriceHistory, Lorry, Ticket,
                     WeighAttempt, AuditLog, log, ROLES,
                     Setting, get_setting, set_setting)
from . import daraja
from . import scale as scale_mod

bp = Blueprint("main", __name__)


# ---------------- helpers ----------------
def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if current_user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


OPS = ("manager", "operator_quarry", "operator_yard")


def next_ticket_no():
    # Derive from the ticket_no prefix itself — immune to created_at tz skew.
    prefix = "MIZ-" + date.today().strftime("%Y%m%d") + "-"
    mx = db.session.query(func.max(Ticket.ticket_no)).filter(
        Ticket.ticket_no.like(prefix + "%")).scalar()
    n = int(mx.rsplit("-", 1)[1]) + 1 if mx else 1
    return f"{prefix}{n:03d}"


def kg(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError, AttributeError):
        return None


def my_site():
    if current_user.role == "operator_quarry":
        return "quarry"
    if current_user.role == "operator_yard":
        return "yard"
    return request.args.get("site")  # manager: optional filter


def site_scope(query):
    site = my_site()
    if site in ("quarry", "yard"):
        query = query.filter(Ticket.site == site)
    return query


@bp.app_template_filter("kes")
def kes(v):
    return f"{(v or 0):,.2f}"


@bp.app_template_filter("kgfmt")
def kgfmt(v):
    return f"{(v or 0):,.0f}"


@bp.app_template_filter("dt")
def dt(v):
    return v.strftime("%d %b %Y, %H:%M") if v else "—"


@bp.app_template_filter("tm")
def tm(v):
    return v.strftime("%H:%M") if v else "—"


# ---------------- auth ----------------
@bp.route("/favicon.ico")
def favicon():
    from flask import send_from_directory
    import os
    return send_from_directory(
        os.path.join(bp.root_path, "static"), "favicon.svg",
        mimetype="image/svg+xml")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "").strip(),
                                 active=True).first()
        if u and u.check_password(request.form.get("password", "")):
            login_user(u)
            log(u, "login", detail=f"{u.role} signed in")
            db.session.commit()
            return redirect(url_for("main.index"))
        flash("Wrong username or password.", "err")
    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    log(current_user, "logout")
    db.session.commit()
    logout_user()
    return redirect(url_for("main.login"))


@bp.route("/")
@login_required
def index():
    r = current_user.role
    if r == "manager":
        return redirect(url_for("main.manager"))
    if r == "hr":
        return redirect(url_for("main.hr"))
    if r == "operator_yard":
        return redirect(url_for("main.yard"))
    return redirect(url_for("main.queue"))


# ---------------- queue board ----------------
QUEUE_COLS = [
    ("tare_captured", "Weighed in (tare)", "Tare recorded, waiting for loader"),
    ("loading", "Out loading in pit", "At the crusher / stockpile"),
    ("gross_due", "Back for gross (scale)", "On the weighbridge, ready"),
]


@bp.route("/queue")
@role_required(*OPS)
def queue():
    site = my_site() or "quarry"
    q = request.args.get("q", "").strip().upper()
    cols = []
    for status, title, sub in QUEUE_COLS:
        tq = Ticket.query.filter(Ticket.site == site, Ticket.status == status)
        if q:
            tq = tq.filter(or_(Ticket.plate.contains(q),
                               Ticket.driver_name.contains(q),
                               Ticket.ticket_no.contains(q)))
        cols.append((status, title, sub, tq.order_by(Ticket.created_at).all()))
    completed = site_scope(
        Ticket.query.filter(Ticket.status == "done")).order_by(
        Ticket.completed_at.desc()).limit(30).all()
    on_site = site_scope(Ticket.query.filter(
        Ticket.status.in_(["tare_captured", "loading", "gross_due"]))).count()
    if request.args.get("partial"):
        return render_template("_queue_board.html", cols=cols, site=site)
    return render_template("queue.html", cols=cols, site=site,
                           completed=completed, on_site=on_site, q=q)


@bp.route("/api/lorry/<plate>")
@login_required
def api_lorry(plate):
    l = Lorry.query.get(plate.strip().upper())
    if not l:
        return jsonify(None)
    return jsonify(dict(plate=l.plate, driver_name=l.driver_name,
                        driver_phone=l.driver_phone, transporter=l.transporter,
                        axle_config=l.axle_config, usual_tare_kg=l.usual_tare_kg,
                        visits=l.visits))


# ---------------- tickets ----------------
@bp.route("/tickets/new", methods=["GET", "POST"])
@role_required(*OPS)
def ticket_new():
    site = my_site() or "quarry"
    materials = Material.query.filter_by(active=True).order_by(Material.name).all()
    weigh_mode = get_setting(f"{site}.weigh_mode", "manual")
    if request.method == "POST":
        plate = request.form.get("plate", "").strip().upper()
        if not plate:
            flash("Number plate is required.", "err")
            return render_template("ticket_new.html", materials=materials, site=site,
                                   weigh_mode=weigh_mode)
        tare = kg(request.form.get("tare_kg"))
        if not tare or tare <= 0:
            flash("Enter a valid tare (empty) weight.", "err")
            return render_template("ticket_new.html", materials=materials, site=site,
                                   weigh_mode=weigh_mode)
        mat = Material.query.get(int(request.form.get("material_id")))
        via = "auto" if request.form.get("tare_via") == "auto" else "manual"
        read_by = f"{current_user.name} ({via})"
        t = Ticket(
            ticket_no=next_ticket_no(), site=site,
            type=request.form.get("type", "sale"),
            plate=plate, driver_name=request.form.get("driver_name", "").strip(),
            driver_phone=request.form.get("driver_phone", "").strip(),
            transporter=request.form.get("transporter", "").strip(),
            material=mat,
            target_tonnes=kg(request.form.get("target_tonnes")) or 0,
            tare_kg=tare, tare_read_by=read_by,
            tare_confirmed_by=request.form.get("tare_confirmed_by", "").strip() or None,
            tare_at=datetime.now(), created_by=current_user.name,
            status="loading",
            notes=request.form.get("notes", "").strip() or None)
        db.session.add(t)
        db.session.flush()
        db.session.add(WeighAttempt(ticket_id=t.id, kind="tare", weight_kg=tare,
                                   read_by=read_by,
                                   confirmed_by=t.tare_confirmed_by))
        # remember lorry
        l = Lorry.query.get(plate)
        if not l:
            l = Lorry(plate=plate, usual_tare_kg=tare)
            db.session.add(l)
        l.driver_name = t.driver_name or l.driver_name
        l.driver_phone = t.driver_phone or l.driver_phone
        l.transporter = t.transporter or l.transporter
        l.usual_tare_kg = tare
        l.visits = (l.visits or 0) + 1
        log(current_user, "ticket_created", t.ticket_no,
            f"{t.type} · {plate} · tare {tare:,.0f} kg")
        db.session.commit()
        flash(f"Ticket {t.ticket_no} created — lorry sent to loading.", "ok")
        return redirect(url_for("main.queue", site=site) if current_user.role == "manager"
                        else url_for("main.queue"))
    return render_template("ticket_new.html", materials=materials, site=site,
                           weigh_mode=weigh_mode)


@bp.route("/tickets/<int:tid>")
@login_required
def ticket_detail(tid):
    t = Ticket.query.get_or_404(tid)
    if not current_user.can("view_ticket", t) and current_user.role != "manager" \
            and current_user.role != "hr":
        abort(403)
    attempts = WeighAttempt.query.filter_by(ticket_id=t.id).order_by(WeighAttempt.at).all()
    from . import camera as camera_mod
    dvr_linked = bool(camera_mod.cfg.get("host") and camera_mod.cfg.get("user"))
    return render_template("ticket_detail.html", t=t, attempts=attempts,
                           dvr_linked=dvr_linked)


def _record_gross(t, gross, confirmed_by, via):
    """Shared gross-capture logic for manual typing and auto indicator read."""
    t.gross_kg = gross
    t.gross_read_by = f"{current_user.name} ({via})"
    t.gross_confirmed_by = confirmed_by
    t.gross_at = datetime.now()
    t.compute()
    db.session.add(WeighAttempt(ticket_id=t.id, kind="gross", weight_kg=gross,
                                read_by=t.gross_read_by,
                                confirmed_by=confirmed_by))
    if t.type == "transfer":
        if t.status in ("tare_captured", "loading", "gross_due"):
            t.status = "in_transit"
        msg = f"Transfer {t.ticket_no} dispatched — net {t.net_tonnes} t. Yard notified."
    else:
        if t.status in ("tare_captured", "loading", "gross_due"):
            t.status = "payment_due"
        if t.variance_ok:
            msg = f"Gross locked: net {t.net_tonnes} t — ON TARGET."
        elif (t.variance_kg or 0) < 0:
            msg = (f"Gross locked: net {t.net_tonnes} t — "
                   f"UNDER TARGET, still need +{abs(t.variance_kg):,.0f} kg. "
                   f"Top up and re-weigh — or proceed, it'll be logged.")
        else:
            msg = (f"Gross locked: net {t.net_tonnes} t — "
                   f"OVER TARGET by {t.variance_kg:,.0f} kg.")
    log(current_user, "gross_captured", t.ticket_no,
        f"gross {gross:,.0f} kg ({via}) → net {t.net_kg:,.0f} kg")
    db.session.commit()
    flash(msg, "ok" if (t.variance_ok or t.type == "transfer") else "err")


@bp.route("/tickets/<int:tid>/gross", methods=["POST"])
@role_required(*OPS)
def ticket_gross(tid):
    t = Ticket.query.get_or_404(tid)
    if get_setting(f"{t.site}.weigh_mode", "manual") == "auto":
        live = scale_mod.read_live(t.site)
        if live and live["fresh"]:
            if not live["stable"]:
                flash(f"Scale not stable ({live['kg']:,.0f} kg) — wait for it to settle, then lock.", "err")
                return redirect(url_for("main.ticket_detail", tid=tid))
            gross = live["kg"]
            via = "auto"
        else:
            # Reader down: fall back to the manual input — weighing never stops.
            gross = kg(request.form.get("gross_kg"))
            via = "manual (reader down)"
            if not gross:
                flash("No live reading — the scale reader isn't running. Type the weight manually.", "err")
                return redirect(url_for("main.ticket_detail", tid=tid))
    else:
        gross = kg(request.form.get("gross_kg"))
        via = "manual"
    if not gross or gross <= 0:
        flash("Enter a valid gross (loaded) weight.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    if t.tare_kg and gross <= t.tare_kg:
        flash("Gross weight must be more than the tare weight.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    _record_gross(t, gross,
                  request.form.get("gross_confirmed_by", "").strip() or None,
                  via)
    return redirect(url_for("main.ticket_detail", tid=tid))


@bp.route("/api/scale/live")
@login_required
def api_scale_live():
    site = request.args.get("site", "quarry")
    live = scale_mod.read_live(site)
    mode = get_setting(f"{site}.weigh_mode", "manual")
    try:
        expected = float(request.args.get("expected", 0)) or None
    except (TypeError, ValueError):
        expected = None
    if request.args.get("format") == "json":
        return {"kg": live["kg"] if live else None,
                "stable": bool(live and live["stable"]),
                "fresh": bool(live and live["fresh"]),
                "mode": mode}
    return render_template("_scale_live.html", live=live, mode=mode,
                           site=site, expected=expected)


@bp.route("/tickets/<int:tid>/pay-mpesa", methods=["POST"])
@role_required(*OPS)
def pay_mpesa(tid):
    t = Ticket.query.get_or_404(tid)
    phone = request.form.get("mpesa_phone", "").strip().replace(" ", "")
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if not phone.startswith("254") or len(phone) != 12:
        flash("Enter a valid Safaricom number, e.g. 0722 411902.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    try:
        crid = daraja.stk_push(phone, t.amount_due, t.ticket_no)
    except Exception as e:
        t.payment_status = "failed"
        log(current_user, "stk_failed", t.ticket_no, str(e))
        db.session.commit()
        flash(f"STK push failed: {e}", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    t.payment_method = "mpesa"
    t.payment_status = "stk_pending"
    t.mpesa_phone = phone
    t.mpesa_checkout_id = crid
    log(current_user, "stk_sent", t.ticket_no,
        f"KES {t.amount_due:,.2f} → {phone} ({crid})")
    db.session.commit()
    flash("STK push sent — waiting for the customer to enter their M-Pesa PIN.", "ok")
    return redirect(url_for("main.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/pay-mpesa-simulate", methods=["POST"])
@role_required(*OPS)
def pay_mpesa_simulate(tid):
    """Demo mode only: pretend the customer approved the STK prompt."""
    t = Ticket.query.get_or_404(tid)
    if not daraja.cfg.get("demo"):
        abort(403)
    t.payment_status = "paid"
    t.mpesa_code = "QHX" + "".join(str((t.id * 7 + i) % 10) for i in range(7))
    log(current_user, "stk_paid_demo", t.ticket_no, f"code {t.mpesa_code}")
    db.session.commit()
    flash(f"Demo: payment confirmed — {t.mpesa_code}.", "ok")
    return redirect(url_for("main.ticket_receipt", tid=tid, fresh=1))


@bp.route("/tickets/<int:tid>/pay-mpesa-code", methods=["POST"])
@role_required(*OPS)
def pay_mpesa_code(tid):
    t = Ticket.query.get_or_404(tid)
    code = request.form.get("mpesa_code", "").strip().upper()
    if len(code) < 6:
        flash("Enter the M-Pesa confirmation code from the customer's SMS.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    t.payment_method = "mpesa"
    t.payment_status = "paid"
    t.mpesa_code = code
    log(current_user, "mpesa_code_manual", t.ticket_no, f"code {code}")
    db.session.commit()
    flash("Payment recorded.", "ok")
    return redirect(url_for("main.ticket_receipt", tid=tid, fresh=1))


@bp.route("/tickets/<int:tid>/pay-cash", methods=["POST"])
@role_required(*OPS)
def pay_cash(tid):
    t = Ticket.query.get_or_404(tid)
    t.payment_method = "cash"
    t.payment_status = "paid"
    log(current_user, "cash_paid", t.ticket_no, f"KES {t.amount_due:,.2f}")
    db.session.commit()
    flash("Cash payment recorded.", "ok")
    return redirect(url_for("main.ticket_receipt", tid=tid, fresh=1))


@bp.route("/tickets/<int:tid>/complete", methods=["POST"])
@role_required(*OPS)
def ticket_complete(tid):
    t = Ticket.query.get_or_404(tid)
    if t.type == "sale" and t.payment_status != "paid" and not t.forced:
        flash("Payment must be completed (or manager-forced) before clearing.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    off_target = t.variance_ok is False
    ack = request.form.get("ack_variance") == "1"
    # The weighbridge never waits for the manager: off-target completion is
    # allowed with the operator's explicit acknowledgment (logged). The
    # manager reviews every one of these in their alerts feed.
    if off_target and not ack and not t.forced and current_user.role != "manager":
        flash("Ticket is off target — confirm you're proceeding with the actual weight.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    t.status = "done"
    t.completed_at = datetime.now()
    if off_target:
        log(current_user, "completed_off_target", t.ticket_no,
            f"net {t.net_tonnes} t vs target {t.target_tonnes} t "
            f"(variance {t.variance_kg:+,.0f} kg) — operator acknowledged")
    else:
        log(current_user, "ticket_completed", t.ticket_no, "gate pass issued")
    db.session.commit()
    flash(f"{t.ticket_no} cleared — gate pass ready.", "ok")
    return redirect(url_for("main.ticket_receipt", tid=tid))


@bp.route("/tickets/<int:tid>/void", methods=["POST"])
@role_required("manager")
def ticket_void(tid):
    t = Ticket.query.get_or_404(tid)
    t.status = "voided"
    log(current_user, "ticket_voided", t.ticket_no,
        f"reason: {request.form.get('reason', '')}")
    db.session.commit()
    flash(f"{t.ticket_no} voided (logged).", "ok")
    return redirect(url_for("main.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/force", methods=["POST"])
@role_required("manager")
def ticket_force(tid):
    t = Ticket.query.get_or_404(tid)
    reason = request.form.get("reason", "").strip()
    t.forced = True
    t.force_reason = reason
    log(current_user, "force_complete", t.ticket_no, f"reason: {reason}")
    db.session.commit()
    flash("Manager override recorded.", "ok")
    return redirect(url_for("main.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/receipt")
@login_required
def ticket_receipt(tid):
    t = Ticket.query.get_or_404(tid)
    clearable = (t.type == "sale" and t.status != "done"
                 and t.payment_status == "paid"
                 and (t.variance_ok is not False or t.forced
                      or current_user.role == "manager"))
    return render_template("receipt.html", t=t, clearable=clearable,
                           fresh=request.args.get("fresh"))


@bp.route("/history")
@role_required(*OPS)
def history():
    q = site_scope(Ticket.query.filter(Ticket.status.in_(("done", "voided"))))
    f = request.args.get("f", "").strip().upper()
    if f:
        q = q.filter(or_(Ticket.plate.contains(f), Ticket.ticket_no.contains(f),
                         Ticket.driver_name.contains(f)))
    tickets = q.order_by(Ticket.completed_at.desc()).limit(100).all()
    return render_template("history.html", tickets=tickets, f=f)


# ---------------- yard ----------------
@bp.route("/yard")
@role_required("manager", "operator_yard")
def yard():
    incoming = Ticket.query.filter(Ticket.type == "transfer",
                                   Ticket.status == "in_transit"
                                   ).order_by(Ticket.gross_at).all()
    receiving = Ticket.query.filter(Ticket.type == "transfer",
                                    Ticket.status == "receiving").all()
    cols = []
    for status, title, sub in QUEUE_COLS:
        tq = Ticket.query.filter(Ticket.site == "yard", Ticket.status == status)
        cols.append((status, title, sub, tq.order_by(Ticket.created_at).all()))
    return render_template("yard.html", incoming=incoming, receiving=receiving,
                           cols=cols)


@bp.route("/tickets/<int:tid>/arrive", methods=["POST"])
@role_required("manager", "operator_yard")
def ticket_arrive(tid):
    t = Ticket.query.get_or_404(tid)
    gross = kg(request.form.get("arrival_gross_kg"))
    if not gross or gross <= 0:
        flash("Enter the arrival gross weight.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    t.arrival_gross_kg = gross
    t.status = "receiving"
    via = "auto" if request.form.get("via") == "auto" else "manual"
    db.session.add(WeighAttempt(ticket_id=t.id, kind="arrival_gross",
                                weight_kg=gross,
                                read_by=f"{current_user.name} ({via})"))
    log(current_user, "transfer_arrived", t.ticket_no, f"arrival gross {gross:,.0f} kg")
    db.session.commit()
    flash("Arrival gross recorded — offload, then capture the empty weight.", "ok")
    return redirect(url_for("main.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/receive", methods=["POST"])
@role_required("manager", "operator_yard")
def ticket_receive(tid):
    t = Ticket.query.get_or_404(tid)
    tare = kg(request.form.get("arrival_tare_kg"))
    if not tare or tare <= 0 or (t.arrival_gross_kg and tare >= t.arrival_gross_kg):
        flash("Enter a valid arrival tare weight.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    t.arrival_tare_kg = tare
    t.delivered_net_kg = round(t.arrival_gross_kg - tare, 1)
    t.transit_variance_kg = round(t.delivered_net_kg - (t.net_kg or 0), 1)
    t.received_by = current_user.name
    t.received_at = datetime.now()
    t.status = "done"
    t.completed_at = datetime.now()
    via = "auto" if request.form.get("via") == "auto" else "manual"
    db.session.add(WeighAttempt(ticket_id=t.id, kind="arrival_tare",
                                weight_kg=tare,
                                read_by=f"{current_user.name} ({via})"))
    flag = "OK" if abs(t.transit_variance_kg) <= (t.net_kg or 0) * 0.005 else "SHORTFALL"
    log(current_user, "transfer_received", t.ticket_no,
        f"delivered {t.delivered_net_kg:,.0f} kg, variance {t.transit_variance_kg:,.0f} kg [{flag}]")
    db.session.commit()
    flash(f"Transfer received — transit variance {t.transit_variance_kg:+,.0f} kg [{flag}].",
          "ok" if flag == "OK" else "err")
    return redirect(url_for("main.yard"))


# ---------------- manager ----------------
def kpis(site=None):
    today = date.today()
    base = Ticket.query.filter(func.date(Ticket.created_at) == today,
                               Ticket.status != "voided")
    if site in ("quarry", "yard"):
        base = base.filter(Ticket.site == site)
    done = base.filter(Ticket.status == "done")
    dispatched = base.filter(Ticket.type == "transfer").with_entities(
        func.coalesce(func.sum(Ticket.net_kg), 0)).scalar() or 0
    sold = done.filter(Ticket.type == "sale").with_entities(
        func.coalesce(func.sum(Ticket.net_kg), 0)).scalar() or 0
    revenue = done.filter(Ticket.type == "sale",
                          Ticket.payment_status == "paid").with_entities(
        func.coalesce(func.sum(Ticket.amount_due), 0)).scalar() or 0
    pending = base.filter(Ticket.type == "sale",
                          Ticket.payment_status != "paid",
                          Ticket.status != "done").with_entities(
        func.coalesce(func.sum(Ticket.amount_due), 0)).scalar() or 0
    tvar = done.filter(Ticket.type == "transfer").with_entities(
        func.coalesce(func.sum(Ticket.transit_variance_kg), 0)).scalar() or 0
    return dict(dispatched=dispatched / 1000, sold=sold / 1000,
                revenue=revenue, pending=pending, tvar=tvar / 1000)


@bp.route("/manager")
@role_required("manager")
def manager():
    site = request.args.get("site")
    k = kpis(site)
    alerts = (AuditLog.query.filter(
        AuditLog.action.in_(("force_complete", "ticket_voided",
                              "transfer_received", "stk_failed",
                              "completed_off_target")))
        .order_by(AuditLog.at.desc()).limit(12).all())
    activity = site_scope(Ticket.query).order_by(
        Ticket.created_at.desc()).limit(25).all()
    return render_template("manager.html", k=k, alerts=alerts,
                           activity=activity, site=site)


@bp.route("/reports")
@role_required("manager")
def reports():
    site = request.args.get("site", "")
    mat = request.args.get("material", "")
    dfrom = request.args.get("dfrom", "")
    dto = request.args.get("dto", "")
    q = Ticket.query.filter(Ticket.status == "done")
    if site in ("quarry", "yard"):
        q = q.filter(Ticket.site == site)
    if mat:
        q = q.filter(Ticket.material_id == int(mat))
    if dfrom:
        q = q.filter(func.date(Ticket.created_at) >= dfrom)
    if dto:
        q = q.filter(func.date(Ticket.created_at) <= dto)
    tickets = q.order_by(Ticket.completed_at.desc()).limit(500).all()
    # per-material summary
    bymat = (db.session.query(Material.name,
                              func.count(Ticket.id),
                              func.coalesce(func.sum(Ticket.net_kg), 0),
                              func.coalesce(func.sum(Ticket.amount_due), 0))
             .join(Ticket, Ticket.material_id == Material.id)
             .filter(Ticket.status == "done", Ticket.type == "sale")
             .group_by(Material.name).all())
    transfers = (Ticket.query.filter(Ticket.type == "transfer",
                                     Ticket.status == "done")
                 .order_by(Ticket.received_at.desc()).limit(100).all())
    materials = Material.query.filter_by(active=True).all()
    return render_template("reports.html", tickets=tickets, bymat=bymat,
                           transfers=transfers, materials=materials,
                           site=site, mat=mat, dfrom=dfrom, dto=dto)


@bp.route("/reports/export")
@role_required("manager")
def reports_export():
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Tickets"
    headers = ["Ticket", "Date", "Site", "Type", "Plate", "Driver", "Material",
               "Target(t)", "Tare(kg)", "Gross(kg)", "Net(kg)", "Amount(KES)",
               "Pay method", "Pay status", "M-Pesa code", "Transit var(kg)"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
    for t in Ticket.query.order_by(Ticket.created_at.desc()).limit(2000).all():
        ws.append([t.ticket_no, t.created_at.strftime("%Y-%m-%d %H:%M"), t.site,
                   t.type, t.plate, t.driver_name,
                   t.material.name if t.material else "", t.target_tonnes,
                   t.tare_kg, t.gross_kg, t.net_kg, t.amount_due,
                   t.payment_method, t.payment_status, t.mpesa_code,
                   t.transit_variance_kg])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    log(current_user, "export_excel", detail="ticket export")
    db.session.commit()
    return send_file(bio, as_attachment=True,
                     download_name=f"mizani-tickets-{date.today()}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/materials", methods=["GET", "POST"])
@role_required("manager")
def materials():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        price = kg(request.form.get("price_per_tonne"))
        if name and price is not None and price >= 0:
            db.session.add(Material(name=name,
                                    spec=request.form.get("spec", "").strip(),
                                    price_per_tonne=price))
            log(current_user, "material_added", detail=f"{name} @ {price}")
            db.session.commit()
            flash("Material added.", "ok")
        return redirect(url_for("main.materials"))
    mats = Material.query.order_by(Material.name).all()
    return render_template("materials.html", materials=mats)


@bp.route("/materials/<int:mid>/price", methods=["POST"])
@role_required("manager")
def material_price(mid):
    m = Material.query.get_or_404(mid)
    new = kg(request.form.get("price_per_tonne"))
    if new is None or new < 0:
        flash("Invalid price.", "err")
        return redirect(url_for("main.materials"))
    if new != m.price_per_tonne:
        db.session.add(PriceHistory(material_id=m.id, old_price=m.price_per_tonne,
                                   new_price=new, changed_by=current_user.name,
                                   reason=request.form.get("reason", "").strip()))
        log(current_user, "price_changed", detail=f"{m.name}: {m.price_per_tonne} → {new}")
        m.price_per_tonne = new
        db.session.commit()
        flash(f"{m.name} repriced — old tickets untouched.", "ok")
    return redirect(url_for("main.materials"))


@bp.route("/users", methods=["GET", "POST"])
@role_required("manager")
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        if username and not User.query.filter_by(username=username).first():
            u = User(username=username, name=request.form.get("name", "").strip(),
                     role=request.form.get("role", "operator_quarry"),
                     site=request.form.get("site") or None)
            u.set_password(request.form.get("password", "mizani123"))
            db.session.add(u)
            log(current_user, "user_created", detail=f"{username} ({u.role})")
            db.session.commit()
            flash(f"User {username} created.", "ok")
        return redirect(url_for("main.users"))
    users = User.query.order_by(User.role, User.name).all()
    return render_template("users.html", users=users, roles=ROLES)


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@role_required("manager")
def user_toggle(uid):
    u = User.query.get_or_404(uid)
    if u.id == current_user.id:
        flash("You cannot deactivate yourself.", "err")
    else:
        u.active = not u.active
        log(current_user, "user_toggled", detail=f"{u.username} → {u.active}")
        db.session.commit()
    return redirect(url_for("main.users"))


def _hr_can_manage(u):
    """HR manages operators only — manager and HR accounts stay with the
    manager. Nobody deactivates themselves."""
    if u.id == current_user.id:
        return False
    if current_user.role == "manager":
        return True
    return u.role not in ("manager", "hr")


@bp.route("/hr/staff/new", methods=["POST"])
@role_required("hr", "manager")
def hr_staff_new():
    username = request.form.get("username", "").strip().lower()
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "operator_quarry")
    password = request.form.get("password", "").strip()
    if current_user.role == "hr" and role not in ("operator_quarry", "operator_yard"):
        flash("HR can only create operator accounts — manager and HR accounts are created by the manager.", "err")
    elif not username or not name:
        flash("Name and username are required.", "err")
    elif User.query.filter_by(username=username).first():
        flash("That username is already taken.", "err")
    elif len(password) < 6:
        flash("Password must be at least 6 characters.", "err")
    elif role not in ROLES:
        flash("Unknown role.", "err")
    else:
        site = "quarry" if role == "operator_quarry" else "yard" if role == "operator_yard" else None
        u = User(username=username, name=name, role=role, site=site)
        u.set_password(password)
        db.session.add(u)
        log(current_user, "user_created", detail=f"{username} ({u.role_label})")
        db.session.commit()
        flash(f"Staff account {username} created.", "ok")
    return redirect(url_for("main.hr", tab="staff"))


@bp.route("/hr/staff/<int:uid>/toggle", methods=["POST"])
@role_required("hr", "manager")
def hr_staff_toggle(uid):
    u = User.query.get_or_404(uid)
    if not _hr_can_manage(u):
        flash("You can't change that account — manager and HR accounts are managed by the manager.", "err")
    else:
        u.active = not u.active
        log(current_user, "user_toggled", detail=f"{u.username} → {'active' if u.active else 'inactive'}")
        db.session.commit()
        flash(f"{u.username} {'reactivated' if u.active else 'deactivated'}.", "ok")
    return redirect(url_for("main.hr", tab="staff"))


@bp.route("/hr/staff/<int:uid>/reset-password", methods=["POST"])
@role_required("hr", "manager")
def hr_staff_reset(uid):
    u = User.query.get_or_404(uid)
    pw = request.form.get("new_password", "").strip()
    if not _hr_can_manage(u):
        flash("You can't change that account — manager and HR accounts are managed by the manager.", "err")
    elif len(pw) < 6:
        flash("Password must be at least 6 characters.", "err")
    else:
        u.set_password(pw)
        log(current_user, "password_reset", detail=f"{u.username}")
        db.session.commit()
        flash(f"Password reset for {u.username} — tell them the new one.", "ok")
    return redirect(url_for("main.hr", tab="staff"))


@bp.route("/tickets/<int:tid>/capture-plate", methods=["POST"])
@role_required(*OPS)
def capture_plate(tid):
    """Grab a still from the Dahua DVR and attach it to the ticket."""
    from . import camera as camera_mod
    t = Ticket.query.get_or_404(tid)
    img = camera_mod.snapshot()
    if not img:
        flash("Could not reach the DVR — check DAHUA_HOST/USER/PASSWORD and that it's on the office LAN.", "err")
        return redirect(url_for("main.ticket_detail", tid=tid))
    os.makedirs(os.path.join("data", "plates"), exist_ok=True)
    fname = f"{t.ticket_no}.jpg"
    with open(os.path.join("data", "plates", fname), "wb") as f:
        f.write(img)
    t.plate_image = fname
    log(current_user, "plate_captured", t.ticket_no, f"{len(img)} bytes from DVR")
    db.session.commit()
    flash("Plate snapshot attached.", "ok")
    return redirect(url_for("main.ticket_detail", tid=tid))


@bp.route("/plates/<fname>")
@login_required
def plate_image(fname):
    from flask import send_from_directory
    if ".." in fname or "/" in fname:
        abort(404)
    return send_from_directory(os.path.join(os.getcwd(), "data", "plates"), fname)


# ---------------- settings ----------------
@bp.route("/settings", methods=["GET", "POST"])
@role_required("manager")
def settings():
    if request.method == "POST":
        for site in ("quarry", "yard"):
            mode = request.form.get(f"{site}_weigh_mode", "manual")
            if mode in ("manual", "auto"):
                set_setting(f"{site}.weigh_mode", mode)
        log(current_user, "settings_changed", detail="weigh modes updated")
        db.session.commit()
        flash("Settings saved.", "ok")
        return redirect(url_for("main.settings"))
    cfg = {s: get_setting(f"{s}.weigh_mode", "manual") for s in ("quarry", "yard")}
    live = {s: scale_mod.read_live(s) for s in ("quarry", "yard")}
    return render_template("settings.html", cfg=cfg, live=live)


# ---------------- HR ----------------
@bp.route("/hr")
@role_required("hr", "manager")
def hr():
    tab = request.args.get("tab", "staff")
    staff = User.query.order_by(User.role, User.name).all()
    who = request.args.get("who", "")
    aq = AuditLog.query
    if who:
        aq = aq.filter(AuditLog.actor.contains(who))
    activity = aq.order_by(AuditLog.at.desc()).limit(120).all()
    # shift summaries: tickets handled per operator today
    shifts = (db.session.query(User.name, User.role,
                               func.count(Ticket.id),
                               func.coalesce(func.sum(Ticket.net_kg), 0),
                               func.min(Ticket.created_at),
                               func.max(Ticket.created_at))
              .join(Ticket, Ticket.created_by == User.name)
              .filter(func.date(Ticket.created_at) == date.today())
              .group_by(User.name, User.role).all())
    return render_template("hr.html", tab=tab, staff=staff,
                           activity=activity, shifts=shifts, who=who)
