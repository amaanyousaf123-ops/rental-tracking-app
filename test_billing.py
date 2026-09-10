import unittest
import os
import sys

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Prevent app.py from initializing the production database on import
os.environ['TESTING'] = 'True'

from app import app, db
from models import Block, Shop, Lease, Transaction, AuditLog, BillingCycle

class BillingSystemTestCase(unittest.TestCase):
    def setUp(self):
        """Sets up a temporary in-memory database for testing the billing system rules."""
        app.config['TESTING'] = True
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        app.config['WTF_CSRF_ENABLED'] = False
        
        # Initialize Flask-SQLAlchemy only once to avoid AssertionError on re-initialization
        if "sqlalchemy" not in app.extensions:
            db.init_app(app)
        
        self.app_context = app.app_context()
        self.app_context.push()
        
        db.create_all()
        
        # Seed blocks and shops
        self.b1 = Block(name="Block A")
        self.b2 = Block(name="Block B")
        db.session.add_all([self.b1, self.b2])
        db.session.commit()
        
        self.s1 = Shop(shop_number="12", block_id=self.b1.id)
        self.s2 = Shop(shop_number="13", block_id=self.b1.id)
        self.s3 = Shop(shop_number="01", block_id=self.b2.id)
        db.session.add_all([self.s1, self.s2, self.s3])
        db.session.commit()
        
        self.client = app.test_client()

    def tearDown(self):
        """Cleans up the database and pops context."""
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_lease_creation_and_formatting(self):
        """Verifies that active leases are created correctly, formatted properly, and that shops cannot be double-assigned."""
        lease1 = Lease(tenant_name="M. Zubair", monthly_rent=10000.0, is_waived=False, status="Active")
        # Assign multiple shops to test combined lease (one-to-many via FK)
        self.s1.lease = lease1
        self.s2.lease = lease1
        db.session.add(lease1)
        db.session.commit()
        
        self.assertEqual(lease1.current_balance, 0.0)
        self.assertEqual(lease1.formatted_shops, "12 & 13")
        self.assertEqual(lease1.formatted_blocks, "Block A")
        
        # Verify shops are now occupied (lease_id is set)
        db.session.expire_all()
        s1_reloaded = db.session.get(Shop, self.s1.id)
        self.assertIsNotNone(s1_reloaded.lease_id, "Shop should have a lease_id after being assigned")
        self.assertEqual(s1_reloaded.lease_id, lease1.id)

    def test_ledger_balance_calculations(self):
        """Verifies the core mathematical formula: New Balance = Prev + Rent - Paid."""
        lease = Lease(tenant_name="Amaan", monthly_rent=15000.0, is_waived=False, status="Active")
        lease.shops.append(self.s3)
        db.session.add(lease)
        db.session.commit()
        
        # 1. Starting Dues Adjustment (e.g. 5,000.00 from historical migration)
        tx1 = Transaction(lease_id=lease.id, type='ADJUSTMENT', amount=5000.0, date="2026-08-01", description="Migrated dues")
        db.session.add(tx1)
        db.session.commit()
        self.assertEqual(lease.current_balance, 5000.0)
        
        # 2. Charge Rent (15,000.00)
        tx2 = Transaction(lease_id=lease.id, type='RENT', amount=15000.0, date="2026-08-01", billing_month="2026-08")
        db.session.add(tx2)
        db.session.commit()
        self.assertEqual(lease.current_balance, 20000.0)
        
        # 3. Log Payment Received (12,000.00, entered as -12000 in ledger)
        tx3 = Transaction(lease_id=lease.id, type='PAYMENT', amount=-12000.0, date="2026-08-15", description="Rent check #89")
        db.session.add(tx3)
        db.session.commit()
        
        # Balance = 5000 (Prev) + 15000 (Rent) - 12000 (Paid) = 8000
        self.assertEqual(lease.current_balance, 8000.0)

    def test_waived_shops_rent(self):
        """Verifies that waived rent shops return 0 rent and do not accrue dues on charge runs."""
        lease_waived = Lease(tenant_name="Waived Shop Tenant", monthly_rent=None, is_waived=True, status="Active")
        lease_waived.shops.append(self.s1)
        db.session.add(lease_waived)
        db.session.commit()
        
        # Get rent charge amount
        rent_charge = lease_waived.get_rent_for_bill()
        self.assertEqual(rent_charge, 0.0)
        
        # Log rent transaction
        tx = Transaction(lease_id=lease_waived.id, type='RENT', amount=rent_charge, date="2026-08-01", billing_month="2026-08")
        db.session.add(tx)
        db.session.commit()
        
        # Balance remains 0
        self.assertEqual(lease_waived.current_balance, 0.0)

    def test_manual_override_dues_correction(self):
        """Verifies that manual overrides insert correct delta adjustment and align balance to target."""
        lease = Lease(tenant_name="Tenant Override", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s2)
        db.session.add(lease)
        db.session.commit()
        
        # Charge rent (10,000)
        tx1 = Transaction(lease_id=lease.id, type='RENT', amount=10000.0, date="2026-08-01", billing_month="2026-08")
        db.session.add(tx1)
        db.session.commit()
        self.assertEqual(lease.current_balance, 10000.0)
        
        # Admin wants to override balance to 3,000.00
        current_bal = lease.current_balance
        target_bal = 3000.0
        delta = target_bal - current_bal # 3000 - 10000 = -7000
        
        tx_override = Transaction(lease_id=lease.id, type='ADJUSTMENT', amount=delta, date="2026-08-20", description="Override")
        db.session.add(tx_override)
        db.session.commit()
        
        self.assertEqual(lease.current_balance, 3000.0)
        self.assertEqual(delta, -7000.0)

    def test_edit_tenant_arrears(self):
        """Verifies editing tenant arrears directly via the edit_tenant form route."""
        lease = Lease(tenant_name="Edit Arrears Tenant", monthly_rent=12000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()
        
        # Initial balance is 0. Edit tenant to set arrears to 15000.0
        response = self.client.post(
            f"/tenants/edit/{lease.id}",
            data={
                "tenant_name": "Edit Arrears Tenant",
                "status": "Active",
                "shop_ids": [str(self.s1.id)],
                "monthly_rent": "12000",
                "current_balance": "15000",
                "operator_name": "Test Admin"
            },
            follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        
        db.session.expire_all()
        reloaded_lease = db.session.get(Lease, lease.id)
        self.assertEqual(reloaded_lease.current_balance, 15000.0)
        
        # Check transaction ledger for adjustment
        adj_tx = Transaction.query.filter_by(lease_id=lease.id, type='ADJUSTMENT').first()
        self.assertIsNotNone(adj_tx)
        self.assertEqual(adj_tx.amount, 15000.0)

    def test_billing_cycle_rollback(self):
        """Verifies that rolling back a billing cycle deletes rent transactions and restores previous balances."""
        lease1 = Lease(tenant_name="Tenant A", monthly_rent=8000.0, is_waived=False, status="Active")
        lease1.shops.append(self.s1)
        
        lease2 = Lease(tenant_name="Tenant B", monthly_rent=12000.0, is_waived=False, status="Active")
        lease2.shops.append(self.s3)
        
        db.session.add_all([lease1, lease2])
        db.session.commit()
        
        # Run billing cycle for 2026-08
        month = "2026-08"
        cycle = BillingCycle(billing_month=month, generated_by="Operator A")
        db.session.add(cycle)
        
        tx_l1 = Transaction(lease_id=lease1.id, type='RENT', amount=8000.0, date="2026-08-01", billing_month=month)
        tx_l2 = Transaction(lease_id=lease2.id, type='RENT', amount=12000.0, date="2026-08-01", billing_month=month)
        
        db.session.add_all([tx_l1, tx_l2])
        db.session.commit()
        
        self.assertEqual(lease1.current_balance, 8000.0)
        self.assertEqual(lease2.current_balance, 12000.0)
        self.assertEqual(BillingCycle.query.count(), 1)
        self.assertEqual(Transaction.query.filter_by(type='RENT').count(), 2)
        
        # Perform rollback
        # 1. Delete rent charges of the month
        rent_txs = Transaction.query.filter_by(type='RENT', billing_month=month).all()
        for tx in rent_txs:
            db.session.delete(tx)
        # 2. Delete the cycle record
        cycle_rec = db.session.get(BillingCycle, month)
        db.session.delete(cycle_rec)
        db.session.commit()
        
        # Assert database state is restored
        self.assertEqual(lease1.current_balance, 0.0)
        self.assertEqual(lease2.current_balance, 0.0)
        self.assertEqual(BillingCycle.query.count(), 0)
        self.assertEqual(Transaction.query.filter_by(type='RENT').count(), 0)

    def test_installment_partial_payment(self):
        """Verifies partial payment logic on installments, including progress calculation and completing the plan."""
        from models import InstallmentPlan, Installment
        
        lease = Lease(tenant_name="Test Tenant Plan", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()
        
        # Create an installment plan of PKR 12,000 in 3 installments of PKR 4,000
        plan = InstallmentPlan(
            lease_id=lease.id,
            total_amount=12000.0,
            num_installments=3,
            installment_amount=4000.0,
            start_date="2026-08-01",
            created_by="Admin"
        )
        db.session.add(plan)
        db.session.flush()
        
        for i in range(1, 4):
            inst = Installment(
                plan_id=plan.id,
                installment_number=i,
                due_date=f"2026-08-0{i}",
                amount_due=4000.0
            )
            db.session.add(inst)
        db.session.commit()
        
        # Verify initial state
        self.assertEqual(plan.progress_pct, 0)
        self.assertEqual(plan.total_paid, 0.0)
        next_unpaid = plan.next_unpaid
        self.assertEqual(next_unpaid.installment_number, 1)
        
        # Pay PKR 1,500 partially towards installment 1
        response = self.client.post(
            f"/installments/{plan.id}/pay",
            data={
                "installment_id": next_unpaid.id,
                "amount": "1500",
                "operator_name": "Admin"
            },
            follow_redirects=True
        )
        
        # Check installment and plan status
        db.session.expire_all()
        reloaded_inst = db.session.get(Installment, next_unpaid.id)
        self.assertEqual(reloaded_inst.amount_paid, 1500.0)
        self.assertEqual(reloaded_inst.status, "Partially Paid")
        self.assertEqual(plan.progress_pct, 12)  # 1500 / 12000 * 100 = 12.5 -> int(12.5) = 12
        self.assertEqual(plan.total_paid, 1500.0)
        self.assertEqual(lease.current_balance, -1500.0)  # Payment decreases dues (starts at 0, so -1500)
        
        # Pay the remaining PKR 2,500 for installment 1
        next_unpaid = plan.next_unpaid
        self.assertEqual(next_unpaid.id, reloaded_inst.id)  # Next unpaid should still be installment 1
        
        response = self.client.post(
            f"/installments/{plan.id}/pay",
            data={
                "installment_id": next_unpaid.id,
                "amount": "2500",
                "operator_name": "Admin"
            },
            follow_redirects=True
        )
        
        db.session.expire_all()
        reloaded_inst = db.session.get(Installment, next_unpaid.id)
        self.assertEqual(reloaded_inst.amount_paid, 4000.0)
        self.assertEqual(reloaded_inst.status, "Paid")
        self.assertEqual(plan.progress_pct, 33)  # 4000 / 12000 * 100 = 33
        
        # Next unpaid should now be installment 2
        next_unpaid_2 = plan.next_unpaid
        self.assertEqual(next_unpaid_2.installment_number, 2)

    def test_dashboard_aging_and_efficiency(self):
        """Verifies dashboard collection efficiency and dues aging calculation rules."""
        from datetime import datetime, timedelta
        from app import get_current_dates
        
        current_month_str, today_str = get_current_dates()
        
        lease = Lease(tenant_name="Dashboard Test Tenant", monthly_rent=20000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()
        
        # Add RENT transaction for current month (PKR 20,000)
        tx_rent = Transaction(lease_id=lease.id, type='RENT', amount=20000.0, date=today_str, billing_month=current_month_str)
        db.session.add(tx_rent)
        
        # Add payment for current month (PKR 5,000)
        tx_pay = Transaction(lease_id=lease.id, type='PAYMENT', amount=-5000.0, date=today_str)
        db.session.add(tx_pay)
        
        # Add an older rent transaction (e.g. 45 days ago) of PKR 10,000
        old_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        tx_old = Transaction(lease_id=lease.id, type='RENT', amount=10000.0, date=old_date, billing_month="2026-07")
        db.session.add(tx_old)
        
        db.session.commit()
        
        # Current Balance = 20000 (Rent) - 5000 (Payment) + 10000 (Old Rent) = 25,000
        self.assertEqual(lease.current_balance, 25000.0)
        
        # Request Dashboard to trigger the calculations
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        
        # Retrieve context or test DB metrics directly
        active_leases = Lease.query.filter_by(status='Active').all()
        total_rent_this_month = sum(tx.amount for tx in Transaction.query.filter(
            Transaction.type == 'RENT',
            Transaction.date.like(f"{current_month_str}%")
        ).all())
        total_collected_this_month = abs(sum(tx.amount for tx in Transaction.query.filter(
            Transaction.type == 'PAYMENT',
            Transaction.date.like(f"{current_month_str}%")
        ).all()))
        
        self.assertEqual(total_rent_this_month, 20000.0)
        self.assertEqual(total_collected_this_month, 5000.0)
        efficiency = (total_collected_this_month / total_rent_this_month) * 100
        self.assertEqual(efficiency, 25.0)
        
        aging_buckets = {'age_0_30': 0.0, 'age_31_60': 0.0, 'age_60_plus': 0.0}
        today = datetime.now().date()
        for l in active_leases:
            rem = l.current_balance
            if rem <= 0: continue
            debits = sorted(
                [tx for tx in l.transactions if tx.type == 'RENT' or (tx.type == 'ADJUSTMENT' and tx.amount > 0)],
                key=lambda x: (x.date, x.id),
                reverse=True
            )
            for tx in debits:
                if rem <= 0: break
                tx_date = datetime.strptime(tx.date, "%Y-%m-%d").date()
                days_old = (today - tx_date).days
                allocated = min(tx.amount, rem)
                if days_old <= 30:
                    aging_buckets['age_0_30'] += allocated
                elif days_old <= 60:
                    aging_buckets['age_31_60'] += allocated
                else:
                    aging_buckets['age_60_plus'] += allocated
                rem -= allocated
            if rem > 0:
                aging_buckets['age_60_plus'] += rem
                
        self.assertEqual(aging_buckets['age_0_30'], 20000.0)
        self.assertEqual(aging_buckets['age_31_60'], 5000.0)
        self.assertEqual(aging_buckets['age_60_plus'], 0.0)

    def test_billing_cycle_custom_lp_and_due_date(self):
        """Verifies that billing cycle can be generated with custom due day and LP surcharge."""
        lease = Lease(tenant_name="Tenant X", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()

        response = self.client.post(
            "/billing/generate",
            data={
                "billing_month": "2026-10",
                "operator_name": "Test Operator",
                "due_day": "20",
                "lp_surcharge": "1500"
            },
            follow_redirects=True
        )
        self.assertEqual(response.status_code, 200)
        
        cycle = BillingCycle.query.get("2026-10")
        self.assertIsNotNone(cycle)
        self.assertEqual(cycle.due_day, 20)
        self.assertEqual(cycle.lp_surcharge, 1500.0)

    def test_auto_apply_lp_surcharge(self):
        """Verifies that a late payment fee is auto-charged when the next month is generated if previous dues weren't paid by due date."""
        lease = Lease(tenant_name="Late Payer", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()
        
        # 1. Generate August billing cycle (due 15th August)
        self.client.post(
            "/billing/generate",
            data={
                "billing_month": "2026-08",
                "operator_name": "Operator",
                "due_day": "15",
                "lp_surcharge": "1000"
            }
        )
        
        # Balance is now 10000. No payments made.
        # 2. Generate September billing cycle (should trigger LP surcharge for August)
        self.client.post(
            "/billing/generate",
            data={
                "billing_month": "2026-09",
                "operator_name": "Operator",
                "due_day": "15",
                "lp_surcharge": "1000"
            }
        )
        
        # Reloader transactions
        db.session.expire_all()
        late_fee_tx = Transaction.query.filter_by(lease_id=lease.id, type='LATE_FEE').first()
        self.assertIsNotNone(late_fee_tx)
        self.assertEqual(late_fee_tx.amount, 1000.0)
        self.assertEqual(late_fee_tx.billing_month, "2026-08") # LP is for August
        self.assertEqual(late_fee_tx.date, "2026-09-01")

    def test_billing_cycle_lock_and_passphrase_rollback(self):
        """Verifies that generation of a new cycle locks the previous one and that rollbacks on locked cycles require the correct passphrase."""
        from models import AppSettings
        # Set passphrase setting in AppSettings
        setting = AppSettings(key='unlock_passphrase', value='TEST-UNLOCK')
        db.session.add(setting)
        
        lease = Lease(tenant_name="Tenant Y", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()
        
        # 1. Generate August billing cycle
        self.client.post(
            "/billing/generate",
            data={
                "billing_month": "2026-08",
                "operator_name": "Operator",
                "due_day": "15",
                "lp_surcharge": "1000"
            }
        )
        
        # 2. Generate September billing cycle (should lock August)
        self.client.post(
            "/billing/generate",
            data={
                "billing_month": "2026-09",
                "operator_name": "Operator",
                "due_day": "15",
                "lp_surcharge": "1000"
            }
        )
        
        db.session.expire_all()
        aug_cycle = BillingCycle.query.get("2026-08")
        self.assertTrue(aug_cycle.is_locked)
        
        # Try rolling back August without correct passphrase -> should fail
        response = self.client.post(
            "/billing/rollback/2026-08",
            data={
                "operator_name": "Operator",
                "unlock_phrase": "WRONG-PHRASE"
            },
            follow_redirects=True
        )
        self.assertIn(b"This billing cycle is LOCKED", response.data)
        
        # August cycle should still exist
        db.session.expire_all()
        self.assertIsNotNone(BillingCycle.query.get("2026-08"))
        
        # Rollback September cycle (since it's the latest, it can be rolled back first)
        response_sep = self.client.post(
            "/billing/rollback/2026-09",
            data={
                "operator_name": "Operator",
                "unlock_phrase": ""
            },
            follow_redirects=True
        )
        self.assertIsNotNone(response_sep)
        
        # Now August is the latest cycle. Try rolling it back with correct passphrase: "TEST-UNLOCK Operator"
        response_rollback = self.client.post(
            "/billing/rollback/2026-08",
            data={
                "operator_name": "Operator",
                "unlock_phrase": "TEST-UNLOCK Operator"
            },
            follow_redirects=True
        )
        self.assertIn(b"has been rolled back successfully", response_rollback.data)
        db.session.expire_all()
        self.assertIsNone(BillingCycle.query.get("2026-08"))

    def test_arrears_reflected_in_billing_details(self):
        """Verifies that editing arrears for a tenant updates the billing cycle report and total payable amount."""
        lease = Lease(tenant_name="Arrears Billing Test Tenant", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()

        # 1. Generate August billing cycle
        self.client.post(
            "/billing/generate",
            data={
                "billing_month": "2026-08",
                "operator_name": "Test Admin",
                "due_day": "15",
                "lp_surcharge": "1000"
            },
            follow_redirects=True
        )

        # Initial billing report check for August (Adjustments should be 0, Payable should be 10000)
        res_before = self.client.get("/billing/view/2026-08")
        self.assertIn(b"Arrears Billing Test Tenant", res_before.data)
        self.assertIn(b"10,000.00", res_before.data)

        # 2. Override arrears for August billing cycle to set new target balance to 25000 (Delta +15000)
        res_override = self.client.post(
            f"/tenants/override/{lease.id}?month=2026-08",
            data={
                "target_balance": "25000.0",
                "reason": "Arrears confirmed by admin",
                "operator_name": "Test Admin",
                "billing_month": "2026-08"
            },
            follow_redirects=True
        )
        self.assertEqual(res_override.status_code, 200)

        # 3. Check billing report for August: should show adjustment and updated total payable 25,000.00
        res_after = self.client.get("/billing/view/2026-08")
        self.assertIn(b"15,000.00", res_after.data) # Adjustment amount
        self.assertIn(b"25,000.00", res_after.data) # Total payable amount

    def test_regenerate_billing_with_updated_tenant_values(self):
        """Verifies that regenerating an unlocked cycle updates rent charges when tenant rent changes."""
        lease = Lease(tenant_name="Regen Tenant", monthly_rent=10000.0, is_waived=False, status="Active")
        lease.shops.append(self.s1)
        db.session.add(lease)
        db.session.commit()

        # 1. First billing run (rent = 10000)
        self.client.post(
            "/billing/generate",
            data={"billing_month": "2026-09", "operator_name": "Admin", "due_day": "15", "lp_surcharge": "1000"},
            follow_redirects=True
        )

        res1 = self.client.get("/billing/view/2026-09")
        self.assertIn(b"10,000.00", res1.data)

        # 2. Update tenant rent to 18000
        self.client.post(
            f"/tenants/edit/{lease.id}",
            data={
                "tenant_name": "Regen Tenant",
                "status": "Active",
                "shop_ids": [str(self.s1.id)],
                "monthly_rent": "18000",
                "operator_name": "Admin"
            },
            follow_redirects=True
        )

        # 3. Regenerate billing for 2026-09
        res_regen = self.client.post(
            "/billing/generate",
            data={"billing_month": "2026-09", "operator_name": "Admin", "due_day": "15", "lp_surcharge": "1000"},
            follow_redirects=True
        )
        self.assertIn(b"regenerated with updated tenant values", res_regen.data)

        res2 = self.client.get("/billing/view/2026-09")
        self.assertIn(b"18,000.00", res2.data)

    def test_lp_surcharge_rule_paid_vs_unpaid_in_prev_month(self):
        """Verifies that LP surcharge is removed if a tenant paid ANYTHING in previous month, and applied only if paid NOTHING."""
        l_paid = Lease(tenant_name="Paid Tenant", monthly_rent=10000.0, is_waived=False, status="Active")
        l_unpaid = Lease(tenant_name="Unpaid Tenant", monthly_rent=10000.0, is_waived=False, status="Active")
        l_paid.shops.append(self.s1)
        l_unpaid.shops.append(self.s2)
        db.session.add_all([l_paid, l_unpaid])
        db.session.commit()

        # 1. Generate August 2026
        self.client.post("/billing/generate", data={"billing_month": "2026-08", "operator_name": "Admin", "due_day": "15", "lp_surcharge": "1000"}, follow_redirects=True)

        # 2. Paid Tenant makes a partial payment of 5000 in August
        self.client.post("/payment", data={"lease_id": str(l_paid.id), "amount": "5000", "date": "2026-08-10", "operator_name": "Admin"}, follow_redirects=True)

        # 3. Generate September 2026 (triggers LP check for August)
        self.client.post("/billing/generate", data={"billing_month": "2026-09", "operator_name": "Admin", "due_day": "15", "lp_surcharge": "1000"}, follow_redirects=True)

        # Paid Tenant MUST NOT have LP surcharge
        lp_paid = Transaction.query.filter_by(lease_id=l_paid.id, type='LATE_FEE', billing_month='2026-08').first()
        self.assertIsNone(lp_paid)

        # Unpaid Tenant MUST have LP surcharge
        lp_unpaid = Transaction.query.filter_by(lease_id=l_unpaid.id, type='LATE_FEE', billing_month='2026-08').first()
        self.assertIsNotNone(lp_unpaid)
        self.assertEqual(lp_unpaid.amount, 1000.0)

if __name__ == '__main__':
    unittest.main()




