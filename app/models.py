"""Mizani data models. SQLite by default; swap SQLALCHEMY_DATABASE_URI for Postgres later."""
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

ROLES = ("manager", "hr", "operator_quarry", "operator_yard")
SITES = ("quarry", "yard")
TICKET_TYPES = ("sale", "transfer")

# sale flow: tare_captured -> loading -> gross_due -> payment_due -> done
# transfer flow: tare_captured -> loading -> gross_due -> in_transit -> receiving -> done
STATUSES = ("tare_captured", "loading", "gross_due", "payment_due",
            "in_transit", "receiving", "done", "voided")


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(32), nullable=False)          # see ROLES
    site = db.Column(db.String(16))                          # quarry/yard/None(all)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)
    @property
    def role_label(self):
        return {"manager": "Manager", "hr": "HR",
                "operator_quarry": "Quarry Operator",
                "operator_yard": "Yard Operator"}.get(self.role, self.role)
    def can(self, action, ticket=None):
        """Central permission check. Manager is final."""
        if self.role == "manager":
            return True
        if action in ("view_all_sites", "manage_users", "manage_materials",
                      "void_ticket", "force_complete", "view_money", "view_reports"):
            return False
        if action in ("operate_quarry", "operate_yard"):
            want = "quarry" if action == "operate_quarry" else "yard"
            return self.role in ("operator_quarry", "operator_yard") and \
                (self.site in (want, None))
        if action == "hr_view":
            return self.role == "hr"
        if action == "view_ticket" and ticket is not None:
            if self.role == "hr":
                return False
            if self.role == "operator_yard":
                # yard sees its own tickets plus quarry transfers (it receives them)
                return ticket.site == "yard" or ticket.type == "transfer"
            return self.role == "operator_quarry" and \
                (ticket.site == "quarry" or self.site is None)
        return False


class Material(db.Model):
    __tablename__ = "materials"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    spec = db.Column(db.String(120))
    price_per_tonne = db.Column(db.Float, nullable=False, default=0)
    active = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class PriceHistory(db.Model):
    __tablename__ = "price_history"
    id = db.Column(db.Integer, primary_key=True)
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"), nullable=False)
    old_price = db.Column(db.Float, nullable=False)
    new_price = db.Column(db.Float, nullable=False)
    changed_by = db.Column(db.String(120))
    reason = db.Column(db.String(255))
    changed_at = db.Column(db.DateTime, default=datetime.now)
    material = db.relationship("Material", backref="history")


class Lorry(db.Model):
    """Known lorries: remembered tare + driver details."""
    __tablename__ = "lorries"
    plate = db.Column(db.String(24), primary_key=True)
    driver_name = db.Column(db.String(120))
    driver_phone = db.Column(db.String(32))
    transporter = db.Column(db.String(120))
    axle_config = db.Column(db.String(64))
    usual_tare_kg = db.Column(db.Float)
    visits = db.Column(db.Integer, default=0)


class Ticket(db.Model):
    __tablename__ = "tickets"
    id = db.Column(db.Integer, primary_key=True)
    ticket_no = db.Column(db.String(32), unique=True, nullable=False)
    site = db.Column(db.String(16), nullable=False)          # quarry / yard
    type = db.Column(db.String(16), nullable=False)          # sale / transfer
    plate = db.Column(db.String(24), nullable=False, index=True)
    plate_image = db.Column(db.String(128))  # saved Dahua snapshot filename
    driver_name = db.Column(db.String(120))
    driver_phone = db.Column(db.String(32))
    transporter = db.Column(db.String(120))
    material_id = db.Column(db.Integer, db.ForeignKey("materials.id"))
    target_tonnes = db.Column(db.Float)
    tare_kg = db.Column(db.Float)
    tare_read_by = db.Column(db.String(120))
    tare_confirmed_by = db.Column(db.String(120))
    tare_at = db.Column(db.DateTime)
    gross_kg = db.Column(db.Float)
    gross_read_by = db.Column(db.String(120))
    gross_confirmed_by = db.Column(db.String(120))
    gross_at = db.Column(db.DateTime)
    net_kg = db.Column(db.Float)
    variance_kg = db.Column(db.Float)                        # net - target*1000
    amount_due = db.Column(db.Float, default=0)
    status = db.Column(db.String(24), default="tare_captured", index=True)
    # payment (sales only)
    payment_method = db.Column(db.String(16))                # mpesa / cash
    payment_status = db.Column(db.String(16), default="unpaid")  # unpaid/stk_pending/paid/failed
    mpesa_phone = db.Column(db.String(32))
    mpesa_code = db.Column(db.String(32))
    mpesa_checkout_id = db.Column(db.String(64))
    # transfer receiving (yard)
    arrival_gross_kg = db.Column(db.Float)
    arrival_tare_kg = db.Column(db.Float)
    delivered_net_kg = db.Column(db.Float)
    transit_variance_kg = db.Column(db.Float)                # delivered - dispatched net
    received_by = db.Column(db.String(120))
    received_at = db.Column(db.DateTime)
    # meta
    created_by = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    completed_at = db.Column(db.DateTime)
    forced = db.Column(db.Boolean, default=False)
    force_reason = db.Column(db.String(255))
    notes = db.Column(db.Text)
    material = db.relationship("Material")

    @property
    def net_tonnes(self):
        return round(self.net_kg / 1000, 2) if self.net_kg else 0

    def compute(self):
        if self.tare_kg and self.gross_kg:
            self.net_kg = round(self.gross_kg - self.tare_kg, 1)
            if self.target_tonnes:
                self.variance_kg = round(self.net_kg - self.target_tonnes * 1000, 1)
            if self.type == "sale" and self.material:
                self.amount_due = round(self.net_tonnes * self.material.price_per_tonne, 2)

    @property
    def target_kg(self):
        return round((self.target_tonnes or 0) * 1000, 1)

    @property
    def expected_gross_kg(self):
        return round((self.tare_kg or 0) + self.target_kg, 1)

    @property
    def variance_ok(self):
        if self.variance_kg is None or not self.target_tonnes:
            return None
        return abs(self.variance_kg) <= self.target_tonnes * 1000 * 0.01  # 1% tolerance


class WeighAttempt(db.Model):
    """Every weighing event incl. re-weighs."""
    __tablename__ = "weigh_attempts"
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    kind = db.Column(db.String(24), nullable=False)   # tare/gross/arrival_gross/arrival_tare
    weight_kg = db.Column(db.Float, nullable=False)
    read_by = db.Column(db.String(120))
    confirmed_by = db.Column(db.String(120))
    at = db.Column(db.DateTime, default=datetime.now)
    ticket = db.relationship("Ticket", backref="attempts")


class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=datetime.now, index=True)
    actor = db.Column(db.String(120))
    role = db.Column(db.String(32))
    action = db.Column(db.String(64), index=True)
    ticket_no = db.Column(db.String(32))
    detail = db.Column(db.Text)


def log(actor, action, ticket_no=None, detail=""):
    db.session.add(AuditLog(
        actor=getattr(actor, "name", str(actor)) if actor else "system",
        role=getattr(actor, "role", "") if actor else "",
        action=action, ticket_no=ticket_no, detail=detail))


class Setting(db.Model):
    __tablename__ = "settings"
    key = db.Column(db.String(64), primary_key=True)   # e.g. "quarry.weigh_mode"
    value = db.Column(db.String(64))


def get_setting(key, default=""):
    s = Setting.query.get(key)
    return s.value if s else default


def ensure_schema():
    """Prototype-grade migrations for the single-file SQLite DB.

    create_all() can't add columns to existing tables, so new columns are
    added here idempotently. (When we move to Postgres, replace with Alembic.)
    """
    from sqlalchemy import text
    existing = {c[1] for c in
                db.session.execute(text("PRAGMA table_info(tickets)")).fetchall()}
    for col, ddl in [("plate_image", "VARCHAR(128)")]:
        if col not in existing:
            db.session.execute(text(f"ALTER TABLE tickets ADD COLUMN {col} {ddl}"))
    db.session.commit()


def set_setting(key, value):
    s = Setting.query.get(key)
    if not s:
        s = Setting(key=key, value=value)
        db.session.add(s)
    else:
        s.value = value
