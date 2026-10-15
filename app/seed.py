"""Seed demo data: users, materials, known lorries, sample tickets."""
from datetime import datetime, timedelta
from .models import db, User, Material, Lorry, Ticket, WeighAttempt, AuditLog, log

MATERIALS = [
    ("Ballast 20mm (Graded)", "Class A Blue Trap Stone", 1850.00),
    ("Ballast 14mm Standard", "Graded crushed stone", 1750.00),
    ("Ballast 10mm Fine", "Graded crushed stone", 1650.00),
    ("Ballast 6mm Chips", "Graded chips", 1500.00),
    ("Quarry Dust (0-6mm)", "Crusher dust", 900.00),
    ("Hardcore Quarry Spall", "Hand-picked spall", 1200.00),
    ("Building Stone 9x9", "Machine cut", 85.00),  # per piece, demo only
]

USERS = [
    ("manager", "manager123", "David Mwangi", "manager", None),
    ("hr", "hr123", "Grace Njeri", "hr", None),
    ("quarry", "quarry123", "Kiprono Koech", "operator_quarry", "quarry"),
    ("yard", "yard123", "Samuel Otieno", "operator_yard", "yard"),
]

LORRIES = [
    ("KDA 782P", "Joseph Mwangi", "0722 411902", "Mwangi Heavy Haulage", "3-axle 6x4 rigid", 8420),
    ("KCB 904L", "Hassan Noor", "0733 208114", "Apex Roadworks", "4-axle tipper", 8150),
    ("KDG 441X", "James Kariuki", "0712 990345", "Apex Roadworks", "3-axle tipper", 9200),
    ("KDE 319M", "Peter Otieno", "0725 773210", "Self Haul", "2-axle tipper", 7890),
    ("KDD 655A", "Francis Mutua", "0731 556789", "Kundan Singh Ltd", "Tri-axle tipper", 8300),
    ("KBZ 188F", "Sammy Ndegwa", "0720 334455", "JTG Internal Fleet", "3-axle tipper", 8650),
]


def seed():
    if User.query.first():
        return
    for username, pw, name, role, site in USERS:
        u = User(username=username, name=name, role=role, site=site)
        u.set_password(pw)
        db.session.add(u)
    mats = {}
    for name, spec, price in MATERIALS:
        m = Material(name=name, spec=spec, price_per_tonne=price)
        db.session.add(m)
        mats[name] = m
    db.session.flush()
    for plate, driver, phone, transporter, axle, tare in LORRIES:
        db.session.add(Lorry(plate=plate, driver_name=driver, driver_phone=phone,
                             transporter=transporter, axle_config=axle,
                             usual_tare_kg=tare, visits=12))
    now = datetime.now()
    demo = [
        # (site, type, plate, driver, phone, material, target_t, tare, gross, status, pay_method, pay_status, mpesa_code, mins_ago)
        ("quarry", "sale", "KDA 782P", "Joseph Mwangi", "0722411902", "Ballast 20mm (Graded)", 24.0, 8420, None, "loading", None, "unpaid", None, 42),
        ("quarry", "sale", "KCB 904L", "Hassan Noor", "0733208114", "Ballast 14mm Standard", 20.0, 8150, None, "loading", None, "unpaid", None, 55),
        ("quarry", "transfer", "KBZ 188F", "Sammy Ndegwa", "0720334455", "Ballast 10mm Fine", 22.0, 8650, 30650, "in_transit", None, "unpaid", None, 70),
        ("quarry", "sale", "KDG 441X", "James Kariuki", "0712990345", "Quarry Dust (0-6mm)", 28.0, 9200, 37200, "payment_due", "mpesa", "stk_pending", None, 95),
        ("quarry", "sale", "KCA 118J", "Daniel Kiprop", "0722888777", "Ballast 20mm (Graded)", 24.0, 8200, 32240, "done", "mpesa", "paid", "QHX7T2K9P1", 180),
        ("quarry", "sale", "KCP 488D", "Brian Wekesa", "0733111222", "Hardcore Quarry Spall", 18.0, 9050, 27550, "done", "cash", "paid", None, 240),
        ("yard", "sale", "KBX 770U", "George Kamau", "0722444333", "Quarry Dust (0-6mm)", 28.0, 8400, 36520, "done", "mpesa", "paid", "QHX7R8M2N4", 200),
        ("quarry", "sale", "KDE 319M", "Peter Otieno", "0725773210", "Hardcore Quarry Spall", 18.0, 7890, None, "tare_captured", None, "unpaid", None, 8),
    ]
    seq = 1
    for (site, typ, plate, driver, phone, mat, target, tare, gross,
         status, paym, pays, mcode, mins) in demo:
        t = Ticket(
            ticket_no=f"MIZ-{now.strftime('%Y%m%d')}-{seq:03d}",
            site=site, type=typ, plate=plate, driver_name=driver,
            driver_phone=phone, material=mats[mat], target_tonnes=target,
            tare_kg=tare, tare_read_by="Kiprono Koech", tare_confirmed_by="Peter (window)",
            tare_at=now - timedelta(minutes=mins),
            created_by="Kiprono Koech",
            created_at=now - timedelta(minutes=mins),
            status=status, payment_method=paym, payment_status=pays,
            mpesa_phone=phone if paym == "mpesa" else None, mpesa_code=mcode)
        if gross:
            t.gross_kg = gross
            t.gross_read_by = "Kiprono Koech"
            t.gross_at = now - timedelta(minutes=mins - 20)
            t.compute()
        if status == "done":
            t.completed_at = now - timedelta(minutes=mins - 40)
        db.session.add(t)
        seq += 1
    db.session.commit()
    # fix: ticket_no uses seq; recompute cleanly
    for t in Ticket.query.order_by(Ticket.id).all():
        pass
    db.session.commit()
    log(None, "seed", detail="Demo data seeded")
    db.session.commit()
