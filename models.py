from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()

class Block(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False) # e.g., "Block A"
    shops = db.relationship('Shop', back_populates='block', cascade='all, delete-orphan')

    def __repr__(self):
        return f"<Block {self.name}>"

class Shop(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_number = db.Column(db.String(50), nullable=False) # e.g., "12", "13"
    block_id = db.Column(db.Integer, db.ForeignKey('block.id'), index=True, nullable=False)
    lease_id = db.Column(db.Integer, db.ForeignKey('lease.id', ondelete='SET NULL'), index=True, nullable=True)
    
    block = db.relationship('Block', back_populates='shops')
    lease = db.relationship('Lease', back_populates='shops')
    
    __table_args__ = (
        db.UniqueConstraint('block_id', 'shop_number', name='_block_shop_uc'),
    )

    def __repr__(self):
        return f"<Shop {self.block.name} - {self.shop_number}>"

class Lease(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_name = db.Column(db.String(100), nullable=False)
    monthly_rent = db.Column(db.Float, nullable=True) # None if waived
    is_waived = db.Column(db.Boolean, default=False, nullable=False)
    waived_amount = db.Column(db.Float, nullable=True) # Fixed amount waived from each month's rent (for partial waivers)
    status = db.Column(db.String(20), default='Active', nullable=False) # 'Active', 'Inactive'
    inactive_date = db.Column(db.String(10), nullable=True) # 'YYYY-MM-DD' - date shop became inactive (for billing cutoffs)
    notes = db.Column(db.Text, nullable=True)
    
    # Relationships
    shops = db.relationship('Shop', back_populates='lease')
    transactions = db.relationship('Transaction', back_populates='lease', cascade='all, delete-orphan')
    audit_logs = db.relationship('AuditLog', back_populates='lease', cascade='all, delete-orphan')

    @property
    def current_balance(self):
        """Calculates running balance as the sum of all transaction amounts."""
        return sum(tx.amount for tx in self.transactions)

    def get_balance_before(self, start_date_str):
        """Calculates balance before a specific date (YYYY-MM-DD) for billing previous balances."""
        return sum(tx.amount for tx in self.transactions if tx.date < start_date_str)

    def get_rent_for_bill(self):
        """Returns monthly rent for billing, 0 if fully waived, reduced by any partial waiver."""
        if self.is_waived:
            return 0.0
        gross = self.monthly_rent or 0.0
        if self.waived_amount:
            return max(0.0, gross - self.waived_amount)
        return gross

    @property
    def formatted_shops(self):
        """Returns shop numbers joined by & (e.g. '12 & 13' or '05')"""
        if not self.shops:
            return "No Shop Assigned"
        sorted_shops = sorted([s.shop_number for s in self.shops], key=lambda x: (len(x), x))
        if len(sorted_shops) == 1:
            return sorted_shops[0]
        return " & ".join(sorted_shops)

    @property
    def formatted_blocks(self):
        """Returns block names joined (e.g. 'Block A' or 'Block A & Block B')"""
        if not self.shops:
            return "N/A"
        blocks = sorted(list(set([s.block.name for s in self.shops])))
        return " & ".join(blocks)

    def __repr__(self):
        return f"<Lease {self.tenant_name} - {self.formatted_shops}>"

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lease_id = db.Column(db.Integer, db.ForeignKey('lease.id', ondelete='CASCADE'), index=True, nullable=False)
    type = db.Column(db.String(20), index=True, nullable=False) # 'RENT', 'PAYMENT', 'ADJUSTMENT'
    amount = db.Column(db.Float, nullable=False) # Positive for rent/adjustments (dues increase), negative for payments (dues decrease)
    date = db.Column(db.String(10), index=True, nullable=False) # 'YYYY-MM-DD'
    billing_month = db.Column(db.String(7), index=True, nullable=True) # 'YYYY-MM' (only for RENT or related monthly transactions)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True, nullable=False)

    lease = db.relationship('Lease', back_populates='transactions')

    def __repr__(self):
        return f"<Transaction {self.type} - {self.amount} on {self.date}>"

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lease_id = db.Column(db.Integer, db.ForeignKey('lease.id', ondelete='SET NULL'), index=True, nullable=True)
    action = db.Column(db.String(50), nullable=False) # e.g. 'MANUAL_ADJUSTMENT', 'CREATE_LEASE', 'UPDATE_LEASE', 'DELETE_LEASE'
    description = db.Column(db.Text, nullable=False)
    performed_by = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True, nullable=False)

    lease = db.relationship('Lease', back_populates='audit_logs')

    def __repr__(self):
        return f"<AuditLog {self.action} by {self.performed_by}>"

class BillingCycle(db.Model):
    billing_month = db.Column(db.String(7), primary_key=True) # 'YYYY-MM'
    generated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    generated_by = db.Column(db.String(100), nullable=False)
    due_day = db.Column(db.Integer, default=15, nullable=False)  # Day of month rent is due (e.g. 15)
    lp_surcharge = db.Column(db.Float, default=1000.0, nullable=False)  # Late payment surcharge in PKR
    is_locked = db.Column(db.Boolean, default=False, nullable=False)  # Locked cycles cannot be edited/rolled back without passphrase

    def __repr__(self):
        return f"<BillingCycle {self.billing_month}>"

class InstallmentPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lease_id = db.Column(db.Integer, db.ForeignKey('lease.id', ondelete='CASCADE'), index=True, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    num_installments = db.Column(db.Integer, nullable=False)
    installment_amount = db.Column(db.Float, nullable=False)
    start_date = db.Column(db.String(10), nullable=False)  # 'YYYY-MM-DD'
    status = db.Column(db.String(20), default='Active', nullable=False)  # 'Active', 'Completed', 'Cancelled'
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    created_by = db.Column(db.String(100), nullable=False)

    lease = db.relationship('Lease', backref=db.backref('installment_plans', cascade='all, delete-orphan'))
    installments = db.relationship('Installment', back_populates='plan', cascade='all, delete-orphan', order_by='Installment.installment_number')

    @property
    def paid_count(self):
        return sum(1 for i in self.installments if i.status == 'Paid')

    @property
    def total_paid(self):
        return sum(i.amount_paid for i in self.installments)

    @property
    def next_unpaid(self):
        for i in self.installments:
            if i.status in ('Pending', 'Overdue', 'Partially Paid'):
                return i
        return None

    @property
    def progress_pct(self):
        if not self.total_amount:
            return 0
        return int((self.total_paid / self.total_amount) * 100)

    def __repr__(self):
        return f"<InstallmentPlan {self.id} - {self.status}>"

class Installment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('installment_plan.id', ondelete='CASCADE'), index=True, nullable=False)
    installment_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.String(10), index=True, nullable=False)  # 'YYYY-MM-DD'
    amount_due = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0, nullable=False)
    status = db.Column(db.String(20), default='Pending', nullable=False)  # 'Pending', 'Paid', 'Overdue', 'Partially Paid'
    paid_date = db.Column(db.String(10), nullable=True)

    plan = db.relationship('InstallmentPlan', back_populates='installments')

    @property
    def remaining(self):
        return self.amount_due - self.amount_paid

    def __repr__(self):
        return f"<Installment #{self.installment_number} - {self.status}>"

class AppSettings(db.Model):
    """Key-value store for application-wide settings (e.g. unlock passphrase)."""
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)

    def __repr__(self):
        return f"<AppSettings {self.key}={self.value}>"

