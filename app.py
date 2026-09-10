import os
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from models import db, Block, Shop, Lease, Transaction, AuditLog, BillingCycle, InstallmentPlan, Installment, AppSettings
from sqlalchemy import or_
from database import init_db
from utils import get_local_ip, generate_excel_export, generate_tenant_statement_excel

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rental-tracking-app-secret-key-2026'
# Database file path inside instance folder
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///billing.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize Database
if os.environ.get('TESTING') != 'True':
    init_db(app)

# Helper function to get current YYYY-MM and date YYYY-MM-DD
def get_current_dates():
    now = datetime.now()
    return now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")

# Inject current IP and formatting filters to templates
@app.context_processor
def inject_global_vars():
    return {
        'lan_ip': get_local_ip(),
        'current_time': datetime.now(),
        'datetime': datetime,
        'format_currency': lambda val: f"{val:,.2f}" if isinstance(val, (int, float)) else val
    }

# ----------------- ROUTES -----------------

@app.route('/')
def dashboard():
    """Admin dashboard showing metrics, block-wise breakdown, and recent logs."""
    current_month_str, _ = get_current_dates()
    
    # 1. Core Metrics
    from sqlalchemy.orm import subqueryload, joinedload
    
    # Eager load shops (with blocks) and transactions to prevent N+1 queries during breakdown and aging
    active_leases = Lease.query.filter_by(status='Active').options(
        subqueryload(Lease.shops).joinedload(Shop.block),
        subqueryload(Lease.transactions)
    ).all()
    
    # Database level aggregates (significantly faster than fetching models to python)
    total_dues_outstanding = db.session.query(db.func.sum(Transaction.amount)).join(Lease).filter(Lease.status == 'Active').scalar() or 0.0
    
    # Collection this month (direct SQL sum)
    total_collected_this_month = abs(db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.type == 'PAYMENT',
        Transaction.date.like(f"{current_month_str}%")
    ).scalar() or 0.0)
    
    # Rent charged this month (direct SQL sum)
    total_rent_this_month = db.session.query(db.func.sum(Transaction.amount)).filter(
        Transaction.type == 'RENT',
        Transaction.date.like(f"{current_month_str}%")
    ).scalar() or 0.0
    
    active_tenants_count = len(active_leases)
    
    # Count vacant shops (shops not in active leases)
    vacant_shops_count = Shop.query.filter_by(lease_id=None).count()

    # Calculate Collection Efficiency
    if total_rent_this_month > 0:
        collection_efficiency = (total_collected_this_month / total_rent_this_month) * 100
    else:
        collection_efficiency = 100.0 if total_collected_this_month > 0 else 0.0

    # Calculate Dues Aging Breakdown (uses pre-loaded in-memory transactions)
    aging_buckets = {
        'age_0_30': 0.0,
        'age_31_60': 0.0,
        'age_60_plus': 0.0
    }
    today = datetime.now().date()
    
    for lease in active_leases:
        remaining_balance = lease.current_balance
        if remaining_balance <= 0:
            continue
        
        # Sort debit transactions descending by date, then by ID (runs completely in memory)
        debits = sorted(
            [tx for tx in lease.transactions if tx.type == 'RENT' or (tx.type == 'ADJUSTMENT' and tx.amount > 0)],
            key=lambda x: (x.date, x.id),
            reverse=True
        )
        
        for tx in debits:
            if remaining_balance <= 0:
                break
            
            try:
                tx_date = datetime.strptime(tx.date, "%Y-%m-%d").date()
                days_old = (today - tx_date).days
            except Exception:
                days_old = 0
                
            allocated = min(tx.amount, remaining_balance)
            if days_old <= 30:
                aging_buckets['age_0_30'] += allocated
            elif days_old <= 60:
                aging_buckets['age_31_60'] += allocated
            else:
                aging_buckets['age_60_plus'] += allocated
            remaining_balance -= allocated
            
        # Allocate any residual positive balance to the oldest bucket
        if remaining_balance > 0:
            aging_buckets['age_60_plus'] += remaining_balance

    # 2. Block-wise outstanding breakdown
    # To avoid double counting, we attribute lease balance to the block of the first shop in the lease.
    block_dues = {}
    blocks = Block.query.all()
    for b in blocks:
        block_dues[b.name] = 0.0
    block_dues["No Block Assigned"] = 0.0
    
    for l in active_leases:
        bal = l.current_balance
        if l.shops:
            primary_block = l.shops[0].block.name
            block_dues[primary_block] = block_dues.get(primary_block, 0.0) + bal
        else:
            block_dues["No Block Assigned"] += bal
            
    # Clean up empty block labels
    block_dues = {k: v for k, v in block_dues.items() if v != 0.0 or k in [b.name for b in blocks]}

    # 3. Recent Collections
    # Eager load lease info to avoid N+1 queries when showing recent payments
    recent_payments = Transaction.query.filter_by(type='PAYMENT').options(
        joinedload(Transaction.lease).subqueryload(Lease.shops).joinedload(Shop.block)
    ).order_by(
        Transaction.created_at.desc()
    ).limit(5).all()

    # 4. Financial Trends for last 6 billing cycles
    recent_cycles = BillingCycle.query.order_by(BillingCycle.billing_month.desc()).limit(6).all()
    recent_cycles = sorted(recent_cycles, key=lambda x: x.billing_month)
    
    trends_months = [c.billing_month for c in recent_cycles]
    trends_rent = []
    trends_collected = []
    
    for month in trends_months:
        # Total rent charged (direct SQL sum)
        total_rent = db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.type == 'RENT',
            Transaction.billing_month == month
        ).scalar() or 0.0
        trends_rent.append(total_rent)
        
        # Total collections (direct SQL sum)
        start_date = f"{month}-01"
        end_date = f"{month}-31"
        total_collected = abs(db.session.query(db.func.sum(Transaction.amount)).filter(
            Transaction.type == 'PAYMENT',
            Transaction.date >= start_date,
            Transaction.date <= end_date
        ).scalar() or 0.0)
        trends_collected.append(total_collected)

    # 5. Active installment plans count
    active_plans_count = InstallmentPlan.query.filter_by(status='Active').count()

    return render_template(
        'dashboard.html',
        total_dues_outstanding=total_dues_outstanding,
        total_collected_this_month=total_collected_this_month,
        total_rent_this_month=total_rent_this_month,
        active_tenants_count=active_tenants_count,
        vacant_shops_count=vacant_shops_count,
        block_dues=block_dues,
        recent_payments=recent_payments,
        current_month_str=current_month_str,
        block_names=list(block_dues.keys()),
        block_dues_list=list(block_dues.values()),
        trends_months=trends_months,
        trends_rent=trends_rent,
        trends_collected=trends_collected,
        active_plans_count=active_plans_count,
        collection_efficiency=collection_efficiency,
        aging_buckets=aging_buckets
    )

@app.route('/tenants')
def tenants():
    """Tenant/Lease management screen."""
    from sqlalchemy.orm import subqueryload, joinedload
    
    # Eager load shops and transactions to prevent N+1 queries
    active_leases = Lease.query.filter_by(status='Active').options(
        subqueryload(Lease.shops).joinedload(Shop.block),
        subqueryload(Lease.transactions)
    ).all()
    
    inactive_leases = Lease.query.filter_by(status='Inactive').options(
        subqueryload(Lease.shops).joinedload(Shop.block),
        subqueryload(Lease.transactions)
    ).all()
    
    # Eager load block for vacant shops list
    vacant_shops = Shop.query.filter_by(lease_id=None).options(
        joinedload(Shop.block)
    ).join(Block).order_by(Block.name, Shop.shop_number).all()
    
    return render_template(
        'tenants.html',
        active_leases=active_leases,
        inactive_leases=inactive_leases,
        vacant_shops=vacant_shops
    )

@app.route('/tenants/add', methods=['POST'])
def add_tenant():
    """Creates a new lease/tenant, assigning selected shops."""
    tenant_name = request.form.get('tenant_name', '').strip()
    is_waived = 'is_waived' in request.form
    rent_input = request.form.get('monthly_rent', '').strip()
    starting_balance_input = request.form.get('starting_balance', '').strip()
    shop_ids = request.form.getlist('shop_ids')
    notes = request.form.get('notes', '').strip()
    operator_name = request.form.get('operator_name', '').strip()

    if not tenant_name or not operator_name:
        flash("Tenant Name and Operator Name are required.", "error")
        return redirect(url_for('tenants'))

    # Handle rent amount validation
    monthly_rent = None
    if not is_waived:
        try:
            monthly_rent = float(rent_input)
            if monthly_rent < 0:
                raise ValueError()
        except ValueError:
            flash("Please enter a valid monthly rent amount.", "error")
            return redirect(url_for('tenants'))

    # Handle partial waiver amount validation
    waived_amount = None
    waive_input = request.form.get('waived_amount', '').strip()
    if waive_input and not is_waived:
        try:
            waived_amount = float(waive_input)
            if waived_amount < 0:
                raise ValueError()
            if monthly_rent is not None and waived_amount > monthly_rent:
                flash("Waived amount cannot exceed the monthly rent.", "error")
                return redirect(url_for('tenants'))
        except ValueError:
            flash("Please enter a valid waiver amount.", "error")
            return redirect(url_for('tenants'))

    # Handle starting balance validation
    starting_balance = 0.0
    if starting_balance_input:
        try:
            starting_balance = float(starting_balance_input)
        except ValueError:
            flash("Please enter a valid starting balance.", "error")
            return redirect(url_for('tenants'))

    # Verify shops are vacant
    if not shop_ids:
        flash("You must assign at least one shop to the tenant.", "error")
        return redirect(url_for('tenants'))
        
    shop_ids = [int(sid) for sid in shop_ids]
    occupied_shops = Shop.query.filter(Shop.id.in_(shop_ids), Shop.lease_id != None).all()
    if occupied_shops:
        flash("One or more selected shops are already occupied.", "error")
        return redirect(url_for('tenants'))

    # Create Lease
    new_lease = Lease(
        tenant_name=tenant_name,
        monthly_rent=monthly_rent,
        is_waived=is_waived,
        waived_amount=waived_amount,
        status='Active',
        notes=notes
    )
    db.session.add(new_lease)
    db.session.flush()  # Flush to get new_lease.id

    # Assign Shops by setting their lease_id FK directly
    for sid in shop_ids:
        shop = db.session.get(Shop, sid)
        if shop:
            shop.lease_id = new_lease.id

    # If starting balance is provided, log it as an initial adjustment transaction
    _, today_str = get_current_dates()
    if starting_balance != 0.0:
        latest_cycle = BillingCycle.query.order_by(BillingCycle.billing_month.desc()).first()
        target_month = latest_cycle.billing_month if latest_cycle else today_str[:7]
        adj_date = today_str if today_str[:7] == target_month else f"{target_month}-01"
        starting_tx = Transaction(
            lease_id=new_lease.id,
            type='ADJUSTMENT',
            amount=starting_balance,
            date=adj_date,
            billing_month=target_month,
            description="Initial opening balance migration"
        )
        db.session.add(starting_tx)

    # Log Audit Trail
    audit = AuditLog(
        lease_id=new_lease.id,
        action='CREATE_LEASE',
        description=f"Created active lease for tenant '{tenant_name}' covering shop(s) {new_lease.formatted_shops} in {new_lease.formatted_blocks}. Starting balance: PKR {starting_balance:,.2f}.",
        performed_by=operator_name
    )
    db.session.add(audit)
    db.session.commit()

    flash(f"Tenant lease for {tenant_name} added successfully.", "success")
    return redirect(url_for('tenants'))

@app.route('/tenants/edit/<int:lease_id>', methods=['GET', 'POST'])
def edit_tenant(lease_id):
    """Edits an existing lease/tenant (rent, waiver, notes, and shops)."""
    lease = Lease.query.get_or_404(lease_id)
    
    # Find vacant shops + shops currently assigned to this lease
    available_shops = Shop.query.filter(or_(Shop.lease_id == None, Shop.lease_id == lease_id)).join(Block).order_by(Block.name, Shop.shop_number).all()

    if request.method == 'POST':
        tenant_name = request.form.get('tenant_name', '').strip()
        is_waived = 'is_waived' in request.form
        rent_input = request.form.get('monthly_rent', '').strip()
        status = request.form.get('status', 'Active')
        inactive_date = request.form.get('inactive_date', '').strip() or None
        shop_ids = request.form.getlist('shop_ids')
        notes = request.form.get('notes', '').strip()
        operator_name = request.form.get('operator_name', '').strip()

        if not tenant_name or not operator_name:
            flash("Tenant Name and Operator Name are required.", "error")
            return redirect(url_for('edit_tenant', lease_id=lease_id))

        # Rent validation
        monthly_rent = None
        if not is_waived:
            try:
                monthly_rent = float(rent_input)
                if monthly_rent < 0:
                    raise ValueError()
            except ValueError:
                flash("Please enter a valid monthly rent amount.", "error")
                return redirect(url_for('edit_tenant', lease_id=lease_id))

        # Partial waiver amount validation
        waived_amount = None
        waive_input = request.form.get('waived_amount', '').strip()
        if waive_input and not is_waived:
            try:
                waived_amount = float(waive_input)
                if waived_amount < 0:
                    raise ValueError()
                if monthly_rent is not None and waived_amount > monthly_rent:
                    flash("Waived amount cannot exceed the monthly rent.", "error")
                    return redirect(url_for('edit_tenant', lease_id=lease_id))
            except ValueError:
                flash("Please enter a valid waiver amount.", "error")
                return redirect(url_for('edit_tenant', lease_id=lease_id))

        # Check shops assignment (only if active)
        if status == 'Active' and not shop_ids:
            flash("Active leases must be assigned to at least one shop.", "error")
            return redirect(url_for('edit_tenant', lease_id=lease_id))

        # Shop conflicts
        shop_ids = [int(sid) for sid in shop_ids]
        conflicting_shops = Shop.query.filter(Shop.id.in_(shop_ids), Shop.lease_id != None, Shop.lease_id != lease_id).all()
        if conflicting_shops:
            flash("One or more selected shops are already occupied by another active lease.", "error")
            return redirect(url_for('edit_tenant', lease_id=lease_id))

        # Document modifications for Audit Log
        changes = []
        if lease.tenant_name != tenant_name:
            changes.append(f"Name changed from '{lease.tenant_name}' to '{tenant_name}'")
            lease.tenant_name = tenant_name
        if lease.is_waived != is_waived:
            changes.append(f"Waiver changed from {lease.is_waived} to {is_waived}")
            lease.is_waived = is_waived
        if lease.monthly_rent != monthly_rent:
            changes.append(f"Monthly rent changed from {lease.monthly_rent} to {monthly_rent}")
            lease.monthly_rent = monthly_rent

        # Full waiver overrides any partial waiver amount
        if is_waived:
            waived_amount = None
        if lease.waived_amount != waived_amount:
            changes.append(f"Waived amount changed from {lease.waived_amount} to {waived_amount}")
            lease.waived_amount = waived_amount
        if lease.status != status:
            changes.append(f"Status changed from {lease.status} to {status}")
            lease.status = status

        # Handle inactive_date based on status
        if status == 'Inactive':
            if inactive_date:
                if lease.inactive_date != inactive_date:
                    changes.append(f"Inactive date set to {inactive_date}")
                    lease.inactive_date = inactive_date
            elif lease.inactive_date is None:
                # Default to today if not provided
                _, today_str = get_current_dates()
                lease.inactive_date = today_str
                changes.append(f"Inactive date set to {today_str}")
        else:
            if lease.inactive_date is not None:
                changes.append("Inactive date cleared (lease reactivated)")
                lease.inactive_date = None
        
        # Check shop list modifications
        old_shops_str = lease.formatted_shops
        current_shop_ids = [s.id for s in lease.shops]
        if set(current_shop_ids) != set(shop_ids):
            # Unassign all current shops (set their lease_id to NULL)
            for s in lease.shops:
                s.lease_id = None
            db.session.flush()
            # Reassign new shops
            if status == 'Active':
                for sid in shop_ids:
                    shop = db.session.get(Shop, sid)
                    if shop:
                        shop.lease_id = lease.id
            changes.append(f"Shops reassigned from [{old_shops_str}] to [{lease.formatted_shops}]")

        # Arrears / Current Balance modification check
        balance_input = request.form.get('current_balance', '').strip()
        if balance_input != '':
            try:
                target_balance = float(balance_input)
                current_bal = lease.current_balance
                delta = target_balance - current_bal
                if abs(delta) > 0.001:
                    _, today_str = get_current_dates()
                    latest_cycle = BillingCycle.query.order_by(BillingCycle.billing_month.desc()).first()
                    target_month = latest_cycle.billing_month if latest_cycle else today_str[:7]
                    adj_date = today_str if today_str[:7] == target_month else f"{target_month}-01"
                    adjust_tx = Transaction(
                        lease_id=lease.id,
                        type='ADJUSTMENT',
                        amount=delta,
                        date=adj_date,
                        billing_month=target_month,
                        description=f"Arrears Adjustment: Balance set from PKR {current_bal:,.2f} to PKR {target_balance:,.2f}"
                    )
                    db.session.add(adjust_tx)
                    changes.append(f"Arrears balance adjusted from PKR {current_bal:,.2f} to PKR {target_balance:,.2f} (Delta: {delta:+,.2f})")
            except ValueError:
                flash("Please enter a valid numeric arrears balance.", "error")
                return redirect(url_for('edit_tenant', lease_id=lease_id))

        if lease.notes != notes:
            changes.append("Notes updated")
            lease.notes = notes

        if changes:
            # Create Audit Log
            audit = AuditLog(
                lease_id=lease.id,
                action='UPDATE_LEASE',
                description=f"Updated lease for '{lease.tenant_name}'. Changes: " + ", ".join(changes),
                performed_by=operator_name
            )
            db.session.add(audit)
            db.session.commit()
            flash("Tenant lease updated successfully.", "success")
        else:
            flash("No changes were made.", "info")

        return redirect(url_for('tenants'))

    return render_template(
        'edit_tenant.html',
        lease=lease,
        available_shops=available_shops
    )

@app.route('/tenants/delete/<int:lease_id>', methods=['POST'])
def delete_tenant(lease_id):
    """Soft deletes a lease by setting it to Inactive and releasing its shops."""
    lease = Lease.query.get_or_404(lease_id)
    operator_name = request.form.get('operator_name', '').strip()

    if not operator_name:
        flash("Operator Name is required to deactivate a tenant lease.", "error")
        return redirect(url_for('tenants'))

    old_shops_str = lease.formatted_shops
    lease.status = 'Inactive'
    if not lease.inactive_date:
        _, today_str = get_current_dates()
        lease.inactive_date = today_str
    # Release assigned shops by nullifying their lease_id FK
    for shop in lease.shops:
        shop.lease_id = None

    # Log Audit
    audit = AuditLog(
        lease_id=lease.id,
        action='DEACTIVATE_LEASE',
        description=f"Deactivated lease for tenant '{lease.tenant_name}' (inactive since {lease.inactive_date}). Freeing shops: {old_shops_str}. Final balance preserved: PKR {lease.current_balance:,.2f}.",
        performed_by=operator_name
    )
    db.session.add(audit)
    db.session.commit()

    flash(f"Lease for {lease.tenant_name} deactivated and shop(s) released.", "success")
    return redirect(url_for('tenants'))

@app.route('/tenants/override/<int:lease_id>', methods=['GET', 'POST'])
@app.route('/tenants/edit-arrears/<int:lease_id>', methods=['GET', 'POST'])
def override_balance(lease_id):
    """Allows manual correction/override of a tenant's balance, creating an audit trail."""
    lease = Lease.query.get_or_404(lease_id)
    cycles = BillingCycle.query.order_by(BillingCycle.billing_month.desc()).all()
    month = request.args.get('month') or request.form.get('month') or ''
    
    if request.method == 'POST':
        target_balance_str = request.form.get('target_balance', '').strip()
        operator_name = request.form.get('operator_name', '').strip()
        reason = request.form.get('reason', '').strip()
        target_month = request.form.get('billing_month', '').strip() or month

        if not target_balance_str or not operator_name or not reason:
            flash("All fields are required for a balance override.", "error")
            return redirect(url_for('override_balance', lease_id=lease_id, month=month))

        try:
            target_balance = float(target_balance_str)
        except ValueError:
            flash("Please enter a valid numeric target balance.", "error")
            return redirect(url_for('override_balance', lease_id=lease_id, month=month))

        current_balance = lease.current_balance
        delta = target_balance - current_balance

        if delta == 0.0:
            flash("The target balance matches the current balance. No adjustment was created.", "info")
            if month and db.session.get(BillingCycle, month):
                return redirect(url_for('billing_details', month=month))
            return redirect(url_for('tenants'))

        _, today_str = get_current_dates()
        if not target_month:
            target_month = cycles[0].billing_month if cycles else today_str[:7]

        adj_date = today_str if today_str[:7] == target_month else f"{target_month}-01"
        
        # Insert adjustment transaction
        adjust_tx = Transaction(
            lease_id=lease.id,
            type='ADJUSTMENT',
            amount=delta,
            date=adj_date,
            billing_month=target_month,
            description=f"Manual Override: Adjusted balance to PKR {target_balance:,.2f}. Reason: {reason}"
        )
        db.session.add(adjust_tx)

        # Log Audit Trail
        audit = AuditLog(
            lease_id=lease.id,
            action='MANUAL_OVERRIDE',
            description=f"Manual balance override for '{lease.tenant_name}'. Adjusted from PKR {current_balance:,.2f} to PKR {target_balance:,.2f} (Delta: {delta:+,.2f}). Reason: {reason}",
            performed_by=operator_name
        )
        db.session.add(audit)
        db.session.commit()

        flash(f"Balance for {lease.tenant_name} manually adjusted to PKR {target_balance:,.2f}.", "success")
        if month and db.session.get(BillingCycle, month):
            return redirect(url_for('billing_details', month=month))
        return redirect(url_for('tenants'))

    return render_template('override_balance.html', lease=lease, cycles=cycles, selected_month=month)

# ----------------- PAYMENT ROUTES -----------------

@app.route('/payment', methods=['GET', 'POST'])
def payment():
    """Logs payments received against active leases."""
    from sqlalchemy.orm import joinedload, subqueryload
    
    active_leases = Lease.query.filter_by(status='Active').options(
        subqueryload(Lease.shops).joinedload(Shop.block),
        subqueryload(Lease.transactions)
    ).order_by(Lease.tenant_name).all()
    _, today_str = get_current_dates()

    if request.method == 'POST':
        lease_id = request.form.get('lease_id')
        amount_str = request.form.get('amount', '').strip()
        date_str = request.form.get('date', '').strip()
        operator_name = request.form.get('operator_name', '').strip()
        description = request.form.get('description', '').strip()

        if not lease_id or not amount_str or not date_str or not operator_name:
            flash("All fields except description are required.", "error")
            return redirect(url_for('payment'))

        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError()
        except ValueError:
            flash("Please enter a positive numeric payment amount.", "error")
            return redirect(url_for('payment'))

        lease = Lease.query.get(lease_id)
        if not lease:
            flash("Tenant lease not found.", "error")
            return redirect(url_for('payment'))

        # Create PAYMENT transaction (reducing dues, so negative amount)
        pay_tx = Transaction(
            lease_id=lease.id,
            type='PAYMENT',
            amount=-amount,
            date=date_str,
            description=description or "Monthly Rent Payment"
        )
        db.session.add(pay_tx)

        # Log Audit Trail
        audit = AuditLog(
            lease_id=lease.id,
            action='LOG_PAYMENT',
            description=f"Received payment of PKR {amount:,.2f} from tenant '{lease.tenant_name}' for shop(s) {lease.formatted_shops}. Date: {date_str}.",
            performed_by=operator_name
        )
        db.session.add(audit)
        db.session.commit()

        flash(f"Payment of PKR {amount:,.2f} logged successfully for {lease.tenant_name}.", "success")
        return redirect(url_for('payment'))

    # Fetch recent payment history (last 25 payments)
    recent_payments = Transaction.query.filter_by(type='PAYMENT').options(
        joinedload(Transaction.lease).subqueryload(Lease.shops).joinedload(Shop.block)
    ).order_by(Transaction.created_at.desc()).limit(25).all()

    return render_template('log_payment.html', leases=active_leases, today_str=today_str, recent_payments=recent_payments)


@app.route('/payment/delete/<int:tx_id>', methods=['POST'])
def delete_payment(tx_id):
    """Deletes a mistakenly logged payment and restores the tenant's balance."""
    tx = Transaction.query.get_or_404(tx_id)

    if tx.type != 'PAYMENT':
        flash("Only payment transactions can be deleted from this section.", "error")
        return redirect(url_for('payment'))

    lease = tx.lease
    amount = abs(tx.amount)
    operator_name = request.form.get('operator_name', '').strip() or 'Unknown'

    # Log Audit Trail before deleting the transaction
    audit = AuditLog(
        lease_id=lease.id,
        action='DELETE_PAYMENT',
        description=f"Deleted payment of PKR {amount:,.2f} from tenant '{lease.tenant_name}' for shop(s) {lease.formatted_shops}. Date: {tx.date}. Balance restored.",
        performed_by=operator_name
    )
    db.session.add(audit)
    db.session.delete(tx)
    db.session.commit()

    flash(f"Payment of PKR {amount:,.2f} deleted for {lease.tenant_name}. Balance restored.", "success")
    return redirect(url_for('payment'))

# ----------------- SHOPS & BLOCKS SETUP -----------------

@app.route('/shops')
def shops_setup():
    """Standardized setup for blocks and shops."""
    blocks = Block.query.order_by(Block.name).all()
    # Join Block to order by block name, then shop number
    shops = Shop.query.join(Block).order_by(Block.name, Shop.shop_number).all()
    return render_template('shops.html', blocks=blocks, shops=shops)

@app.route('/blocks/add', methods=['POST'])
def add_block():
    """Adds a new Block (Standard Source of Truth)."""
    block_name = request.form.get('block_name', '').strip()
    if not block_name:
        flash("Block name cannot be empty.", "error")
        return redirect(url_for('shops_setup'))

    # Check if duplicate
    existing = Block.query.filter_by(name=block_name).first()
    if existing:
        flash(f"Block '{block_name}' already exists.", "error")
        return redirect(url_for('shops_setup'))

    new_block = Block(name=block_name)
    db.session.add(new_block)
    db.session.commit()
    flash(f"Standard Block '{block_name}' created.", "success")
    return redirect(url_for('shops_setup'))

@app.route('/blocks/delete/<int:block_id>', methods=['POST'])
def delete_block(block_id):
    """Deletes a block if it does not contain standard shops."""
    block = Block.query.get_or_404(block_id)
    if block.shops:
        flash(f"Cannot delete block '{block.name}' because it contains shops. Delete or move the shops first.", "error")
        return redirect(url_for('shops_setup'))

    db.session.delete(block)
    db.session.commit()
    flash(f"Block '{block.name}' deleted.", "success")
    return redirect(url_for('shops_setup'))

@app.route('/shops/add', methods=['POST'])
def add_shop():
    """Adds a standardized shop in a block."""
    shop_number = request.form.get('shop_number', '').strip()
    block_id_str = request.form.get('block_id')

    if not shop_number or not block_id_str:
        flash("Shop number and Block selection are required.", "error")
        return redirect(url_for('shops_setup'))

    block_id = int(block_id_str)
    
    # Check if shop already exists in block
    existing = Shop.query.filter_by(shop_number=shop_number, block_id=block_id).first()
    if existing:
        block = Block.query.get(block_id)
        flash(f"Shop '{shop_number}' already exists in {block.name}.", "error")
        return redirect(url_for('shops_setup'))

    new_shop = Shop(shop_number=shop_number, block_id=block_id)
    db.session.add(new_shop)
    db.session.commit()
    
    block = Block.query.get(block_id)
    flash(f"Shop '{shop_number}' added successfully to {block.name}.", "success")
    return redirect(url_for('shops_setup'))

@app.route('/shops/delete/<int:shop_id>', methods=['POST'])
def delete_shop(shop_id):
    """Deletes a shop if it is not occupied by an active lease."""
    shop = Shop.query.get_or_404(shop_id)
    
    # Check if shop belongs to any lease
    if shop.lease:
        flash(f"Cannot delete Shop '{shop.shop_number}' in {shop.block.name} because it is currently assigned to tenant '{shop.lease.tenant_name}'.", "error")
        return redirect(url_for('shops_setup'))

    db.session.delete(shop)
    db.session.commit()
    flash(f"Shop '{shop.shop_number}' deleted from {shop.block.name}.", "success")
    return redirect(url_for('shops_setup'))

# ----------------- BILLING & INVOICING -----------------

@app.route('/billing')
def billing():
    """Billing cycles list and generator."""
    cycles = BillingCycle.query.order_by(BillingCycle.billing_month.desc()).all()
    # Pre-select next logical billing month YYYY-MM
    current_month, _ = get_current_dates()
    return render_template('billing.html', cycles=cycles, current_month=current_month)

@app.route('/billing/generate', methods=['POST'])
def generate_billing():
    """Generates monthly rent transactions for all active tenants in a billing cycle.
    Also auto-applies Late Payment (LP) surcharge for tenants who didn't pay the previous month by its due date."""
    billing_month = request.form.get('billing_month', '').strip()
    operator_name = request.form.get('operator_name', '').strip()
    due_day_str = request.form.get('due_day', '15').strip()
    lp_surcharge_str = request.form.get('lp_surcharge', '1000').strip()

    if not billing_month or not operator_name:
        flash("Billing Month and Operator Name are required.", "error")
        return redirect(url_for('billing'))

    # Validate due_day and lp_surcharge
    try:
        due_day = int(due_day_str)
        if due_day < 1 or due_day > 28:
            raise ValueError()
    except ValueError:
        flash("Due day must be a number between 1 and 28.", "error")
        return redirect(url_for('billing'))

    try:
        lp_surcharge = float(lp_surcharge_str)
        if lp_surcharge < 0:
            raise ValueError()
    except ValueError:
        flash("LP surcharge must be a valid non-negative number.", "error")
        return redirect(url_for('billing'))

    from sqlalchemy.orm import subqueryload
    active_leases = Lease.query.filter_by(status='Active').options(
        subqueryload(Lease.transactions)
    ).all()

    # Inactive leases that still owe dues also get a bill (existing balance only, no new rent)
    inactive_with_dues = Lease.query.filter(Lease.status == 'Inactive').options(
        subqueryload(Lease.transactions)
    ).all()
    inactive_with_dues = [l for l in inactive_with_dues if l.current_balance > 0]

    bill_leases = active_leases + inactive_with_dues
    if not bill_leases:
        flash("No tenant leases found to bill (no active leases and no inactive leases with outstanding dues).", "error")
        return redirect(url_for('billing'))

    # Prevent double generation unless unlocked (allows regeneration with updated tenant values)
    existing_cycle = BillingCycle.query.get(billing_month)
    is_regeneration = False
    if existing_cycle:
        if existing_cycle.is_locked:
            flash(f"🔒 Billing cycle for month {billing_month} is LOCKED. Unlock or rollback the cycle first to regenerate.", "error")
            return redirect(url_for('billing_details', month=billing_month))
        
        # Regenerate unlocked cycle: delete existing rent & late fee charges for this month
        Transaction.query.filter_by(type='RENT', billing_month=billing_month).delete()
        Transaction.query.filter_by(type='LATE_FEE', billing_month=billing_month).delete()
        
        existing_cycle.due_day = due_day
        existing_cycle.lp_surcharge = lp_surcharge
        existing_cycle.generated_by = operator_name
        existing_cycle.generated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        cycle = existing_cycle
        is_regeneration = True
    else:
        # Create cycle record with per-month settings
        cycle = BillingCycle(
            billing_month=billing_month,
            generated_by=operator_name,
            due_day=due_day,
            lp_surcharge=lp_surcharge
        )
        db.session.add(cycle)

    # --- Auto-apply LP surcharge for unpaid previous month ---
    from dateutil.relativedelta import relativedelta
    billing_dt = datetime.strptime(billing_month, "%Y-%m")
    prev_month_dt = billing_dt - relativedelta(months=1)
    prev_month_str = prev_month_dt.strftime("%Y-%m")
    
    prev_cycle = BillingCycle.query.get(prev_month_str)
    lp_count = 0
    
    if prev_cycle:
        # Lock the previous cycle now that a new one is being generated
        prev_cycle.is_locked = True
        
        for lease in active_leases:
            # Check if tenant paid ANYTHING in/for the previous month
            payments_in_prev = sum(
                abs(tx.amount) for tx in Transaction.query.filter(
                    Transaction.lease_id == lease.id,
                    Transaction.type == 'PAYMENT',
                    or_(
                        Transaction.billing_month == prev_month_str,
                        Transaction.date.like(f"{prev_month_str}%")
                    )
                ).all()
            )
            
            existing_lp = Transaction.query.filter_by(
                lease_id=lease.id, type='LATE_FEE', billing_month=prev_month_str
            ).first()
            
            if payments_in_prev > 0:
                # Rule: Remove LP surcharge if tenant paid ANYTHING in previous month
                if existing_lp:
                    db.session.delete(existing_lp)
            else:
                # Rule: Only charge LP surcharge for tenants who paid NOTHING in previous month
                prev_rent = Transaction.query.filter_by(
                    lease_id=lease.id, type='RENT', billing_month=prev_month_str
                ).first()
                
                if prev_rent and prev_rent.amount > 0 and not lease.is_waived:
                    if not existing_lp and prev_cycle.lp_surcharge > 0:
                        lp_tx = Transaction(
                            lease_id=lease.id,
                            type='LATE_FEE',
                            amount=prev_cycle.lp_surcharge,
                            date=f"{billing_month}-01",
                            billing_month=prev_month_str,
                            description=f"Late Payment Surcharge for {prev_month_str} (No payment received in {prev_month_str})"
                        )
                        db.session.add(lp_tx)
                        lp_count += 1
                        
                        lp_audit = AuditLog(
                            lease_id=lease.id,
                            action='LATE_FEE_CHARGED',
                            description=f"Late payment surcharge of PKR {prev_cycle.lp_surcharge:,.2f} applied for {prev_month_str}. Tenant paid nothing in {prev_month_str}.",
                            performed_by=operator_name
                        )
                        db.session.add(lp_audit)

    # --- Generate rent charges for the current month ---
    rent_date = f"{billing_month}-01"
    charges_count = 0
    waived_count = 0
    skipped_inactive = 0
    inactive_dues_count = 0

    for lease in bill_leases:
        # Active leases that became inactive before this month's start: no new bill generated
        if lease.status == 'Active' and lease.inactive_date and lease.inactive_date < f"{billing_month}-01":
            skipped_inactive += 1
            continue

        is_inactive_with_dues = (lease.status == 'Inactive' and lease.current_balance > 0)

        # Inactive leases still carrying dues: generate a bill showing existing dues only (no new rent charge)
        if is_inactive_with_dues:
            rent_amount = 0.0
        else:
            rent_amount = lease.get_rent_for_bill()

        # Insert RENT transaction in the ledger (0 for inactive-with-dues so they still appear on the bill)
        rent_tx = Transaction(
            lease_id=lease.id,
            type='RENT',
            amount=rent_amount,
            date=rent_date,
            billing_month=billing_month,
            description=f"Monthly Rent Charge for {billing_month}" + (" (Waived)" if lease.is_waived else "") + (" (Inactive - Dues Only)" if is_inactive_with_dues else "")
        )
        db.session.add(rent_tx)

        if is_inactive_with_dues:
            inactive_dues_count += 1
        elif lease.is_waived:
            waived_count += 1
        else:
            charges_count += 1

        # Audit Entry
        audit = AuditLog(
            lease_id=lease.id,
            action='REGENERATE_RENT' if is_regeneration else ('CHARGE_RENT' if not is_inactive_with_dues else 'BILL_INACTIVE_DUES'),
            description=(
                f"{'Regenerated' if is_regeneration else 'Charged'} monthly rent of PKR {rent_amount:,.2f} for billing cycle {billing_month}."
                + (" [WAIVED]" if lease.is_waived else "")
                + (" [INACTIVE - dues only, no new rent]" if is_inactive_with_dues else "")
            ),
            performed_by=operator_name
        )
        db.session.add(audit)

    db.session.commit()
    lp_msg = f" LP surcharge applied to {lp_count} tenant(s)." if lp_count > 0 else ""
    status_str = "regenerated with updated tenant values" if is_regeneration else "complete"
    extra = ""
    if inactive_dues_count:
        extra += f" {inactive_dues_count} inactive shop(s) billed for outstanding dues (no new rent)."
    if skipped_inactive:
        extra += f" {skipped_inactive} shop(s) skipped (inactive before {billing_month}-01)."
    flash(f"Billing cycle for {billing_month} {status_str}. Rent charged for {charges_count} shops, {waived_count} waived-rent shops skipped/billed at 0.{lp_msg}{extra}", "success")
    return redirect(url_for('billing_details', month=billing_month))

@app.route('/billing/view/<month>')
def billing_details(month):
    """View details of a specific billing cycle."""
    from sqlalchemy.orm import subqueryload, joinedload
    
    cycle = BillingCycle.query.get_or_404(month)
    
    # Eager-load rent transactions with their lease -> transactions & shops in one go
    rent_txs = Transaction.query.filter_by(type='RENT', billing_month=month).options(
        joinedload(Transaction.lease).subqueryload(Lease.transactions),
        joinedload(Transaction.lease).subqueryload(Lease.shops).joinedload(Shop.block)
    ).all()
    
    # Compute everything in-memory from the preloaded relationships
    start_date = f"{month}-01"
    end_date = f"{month}-31"

    bill_records = []
    for tx in rent_txs:
        lease = tx.lease
        txs = lease.transactions  # Already loaded, no extra queries
        
        prev_bal = sum(t.amount for t in txs if (t.date < start_date and t.billing_month != month))
        rent = tx.amount
        payment_total = abs(sum(t.amount for t in txs if t.type == 'PAYMENT' and start_date <= t.date <= end_date))
        adjustment_total = sum(t.amount for t in txs if t.type == 'ADJUSTMENT' and (start_date <= t.date <= end_date or t.billing_month == month))
        lp_total = sum(t.amount for t in txs if t.type == 'LATE_FEE' and (start_date <= t.date <= end_date or t.billing_month == month))
        payable = prev_bal + rent - payment_total + adjustment_total + lp_total
        
        bill_records.append({
            'lease': lease,
            'prev_bal': prev_bal,
            'rent': rent,
            'is_waived': lease.is_waived,
            'payments': payment_total,
            'adjustments': adjustment_total,
            'lp_fee': lp_total,
            'payable': payable
        })

    # Get unlock passphrase for the template
    unlock_setting = AppSettings.query.get('unlock_passphrase')
    unlock_passphrase = unlock_setting.value if unlock_setting else 'UNLOCK-BILLING'

    return render_template(
        'billing_details.html',
        cycle=cycle,
        bill_records=bill_records,
        unlock_passphrase=unlock_passphrase
    )

def get_bill_record(lease, month, cycle=None, valid_cycle_months=None, tracker_start_month=None):
    """Build a bill record for a lease using its pre-loaded transactions (no SQL queries).
    Uses cycle object for dynamic due_day and lp_surcharge values."""
    from dateutil.relativedelta import relativedelta
    
    # Get per-cycle settings (fallback to defaults)
    if cycle is None:
        cycle = BillingCycle.query.get(month)
    due_day = cycle.due_day if cycle else 15
    surcharge = cycle.lp_surcharge if cycle else 1000.0
    
    start_date = f"{month}-01"
    end_date = f"{month}-31"
    txs = lease.transactions  # Must be pre-loaded by caller
    
    prev_bal = sum(t.amount for t in txs if (t.date < start_date and t.billing_month != month))
    
    rent_tx = next((t for t in txs if t.type == 'RENT' and t.billing_month == month), None)
    rent = rent_tx.amount if rent_tx else lease.get_rent_for_bill()
    
    payment_total    = abs(sum(t.amount for t in txs if t.type == 'PAYMENT'    and start_date <= t.date <= end_date))
    adjustment_total = sum(t.amount for t in txs if t.type == 'ADJUSTMENT'     and (start_date <= t.date <= end_date or t.billing_month == month))
    lp_this_month    = sum(t.amount for t in txs if t.type == 'LATE_FEE'       and (start_date <= t.date <= end_date or t.billing_month == month))
    
    payable_within = prev_bal + rent - payment_total + adjustment_total + lp_this_month
    payable_after  = payable_within + surcharge
    
    dt = datetime.strptime(month, "%Y-%m")
    billing_month_formatted = dt.strftime("%b-%y")
    issue_date_formatted    = f"1-{dt.strftime('%b-%y')}"
    due_date_formatted      = f"{due_day}-{dt.strftime('%b-%y')}"

    # --- Rent history lookup (single pass) — shared by tracker & prev month bill ---
    # Only include months that have an actual BillingCycle record (filters out legacy/old data).
    if valid_cycle_months is None:
        valid_cycle_months = {c.billing_month for c in BillingCycle.query.all()}
    rent_by_month = {
        t.billing_month: t.amount
        for t in txs
        if t.type == 'RENT' and t.billing_month and t.billing_month in valid_cycle_months
    }

    # --- Previous Month Bill (shown as "PREV MONTH BILL" on the printed slip) ---
    # Displays the GROSS amount billed on the previous month's slip — BEFORE payments.
    # Example: Sep bill = 10,000 | tenant paid 5,000 → Oct slip shows Sep = 10,000 (not 5,000).
    # The outstanding 5,000 is already reflected in "PREVIOUS BALANCE" on the current bill.
    # Formula: (balance carried INTO prev month) + prev month rent + prev month adjustments/late fees
    prev_month_dt  = dt - relativedelta(months=1)
    prev_month_str = prev_month_dt.strftime("%Y-%m")
    pm_start = f"{prev_month_str}-01"
    pm_end   = f"{prev_month_str}-31"

    # Only show PREV MONTH BILL if:
    #   1. A billing cycle was actually generated for that month, AND
    #   2. That month is >= tracker_start_month (not older than the system start date)
    prev_before_start = tracker_start_month and prev_month_str < tracker_start_month
    prev_cycle_exists = prev_month_str in (valid_cycle_months or set())
    if prev_cycle_exists and not prev_before_start:
        pm_carried_bal = sum(t.amount for t in txs if t.date < pm_start and t.billing_month != prev_month_str)
        pm_rent        = rent_by_month.get(prev_month_str, 0.0)
        pm_extras      = sum(                                   # adjustments + late fees for prev month
            t.amount for t in txs
            if t.type in ('ADJUSTMENT', 'LATE_FEE')
            and (pm_start <= t.date <= pm_end or t.billing_month == prev_month_str)
        )
        prev_month_billed = pm_carried_bal + pm_rent + pm_extras
    else:
        prev_month_billed = None  # Before system start or no cycle → show blank

    # --- Monthly Tracker: 2 months before billing month → 6 months ahead ---
    # Shows PAYABLE WITHIN DUE DATE for each month (not just rent).
    # Only shows data for months >= tracker_start_month and with an actual billing cycle.
    def _month_payable(m_str):
        """Compute payable_within for any month using pre-loaded transactions (no SQL)."""
        if m_str not in (valid_cycle_months or set()):
            return None
        if tracker_start_month and m_str < tracker_start_month:
            return None
        m_start = f"{m_str}-01"
        m_end   = f"{m_str}-31"
        m_prev = sum(t.amount for t in txs if t.date < m_start and t.billing_month != m_str)
        m_rent = rent_by_month.get(m_str, 0.0)
        m_pay  = abs(sum(t.amount for t in txs if t.type == 'PAYMENT'    and m_start <= t.date <= m_end))
        m_adj  = sum(t.amount for t in txs if t.type == 'ADJUSTMENT'     and (m_start <= t.date <= m_end or t.billing_month == m_str))
        m_lp   = sum(t.amount for t in txs if t.type == 'LATE_FEE'       and (m_start <= t.date <= m_end or t.billing_month == m_str))
        return m_prev + m_rent - m_pay + m_adj + m_lp

    tracker = [
        {
            'label':  (dt + relativedelta(months=i)).strftime("%b-%y"),
            'amount': _month_payable((dt + relativedelta(months=i)).strftime("%Y-%m"))
        }
        for i in range(-2, 7)
    ]
        
    return {
        'lease': lease,
        'billing_month_formatted': billing_month_formatted,
        'issue_date_formatted':    issue_date_formatted,
        'due_date_formatted':      due_date_formatted,
        'prev_bal':         prev_bal,
        'rent':             rent,
        'is_waived':        lease.is_waived,
        'payable_within':   payable_within,
        'surcharge':        surcharge,
        'payable_after':    payable_after,
        'tracker':          tracker,
        'prev_month_billed': prev_month_billed,
        'prev_month_label':  prev_month_dt.strftime("%b-%y"),
    }

@app.route('/billing/print/<month>')
def print_bills(month):
    """Generates the print slip view for all bills in a cycle (3 copies each)."""
    from sqlalchemy.orm import subqueryload, joinedload
    
    cycle = BillingCycle.query.get_or_404(month)
    
    # Eager-load rent transactions with their lease -> all transactions & shops
    rent_txs = Transaction.query.filter_by(type='RENT', billing_month=month).options(
        joinedload(Transaction.lease).subqueryload(Lease.transactions),
        joinedload(Transaction.lease).subqueryload(Lease.shops).joinedload(Shop.block)
    ).all()
    
    # Query tracker start month and valid cycle months once — shared across all bill records
    tracker_start_setting = AppSettings.query.get('tracker_start_month')
    tracker_start_month   = tracker_start_setting.value if tracker_start_setting else None
    valid_cycle_months    = {c.billing_month for c in BillingCycle.query.all()}
    bill_records = [get_bill_record(tx.lease, month, cycle, valid_cycle_months, tracker_start_month) for tx in rent_txs]
        
    return render_template('print_bills.html', cycle=cycle, bills=bill_records)

@app.route('/billing/rollback/<month>', methods=['POST'])
def rollback_billing(month):
    """Undoes/deletes a billing cycle, removing the rent charges and audit records.
    Locked cycles require the unlock passphrase + operator name to rollback."""
    operator_name = request.form.get('operator_name', '').strip()
    unlock_input = request.form.get('unlock_phrase', '').strip()
    
    if not operator_name:
        flash("Operator Name is required to rollback billing.", "error")
        return redirect(url_for('billing_details', month=month))

    cycle = BillingCycle.query.get_or_404(month)
    
    # If the cycle is locked, verify unlock passphrase
    if cycle.is_locked:
        unlock_setting = AppSettings.query.get('unlock_passphrase')
        required_passphrase = unlock_setting.value if unlock_setting else 'UNLOCK-BILLING'
        
        # Expected format: "PASSPHRASE OperatorName" e.g. "UNLOCK-BILLING Amaan"
        expected = f"{required_passphrase} {operator_name}"
        if unlock_input != expected:
            flash(f"🔒 This billing cycle is LOCKED. To rollback, you must enter the unlock phrase followed by your name (e.g. '{required_passphrase} YourName'). Rollback denied.", "error")
            return redirect(url_for('billing_details', month=month))
    
    # Safety check: Verify this is the latest cycle to avoid ledger timeline breaks
    newer_cycles = BillingCycle.query.filter(BillingCycle.billing_month > month).count()
    if newer_cycles > 0:
        flash("Cannot rollback this month because there are newer billing cycles. Rollback the newer cycles first.", "error")
        return redirect(url_for('billing_details', month=month))

    # Delete all RENT transactions for this month
    rent_txs = Transaction.query.filter_by(type='RENT', billing_month=month).all()
    for tx in rent_txs:
        db.session.delete(tx)
    
    # Delete all LATE_FEE transactions charged for this month
    lp_txs = Transaction.query.filter_by(type='LATE_FEE', billing_month=month).all()
    for tx in lp_txs:
        db.session.delete(tx)
        
    # Write a rollback audit entry for the general logs
    general_audit = AuditLog(
        action='ROLLBACK_BILLING_CYCLE',
        description=f"Rolled back billing cycle for month {month}. Deleted all corresponding rent charge and late fee transactions." + (" [LOCKED CYCLE - UNLOCKED WITH PASSPHRASE]" if cycle.is_locked else ""),
        performed_by=operator_name
    )
    db.session.add(general_audit)
    
    # Delete the cycle record itself
    db.session.delete(cycle)
    db.session.commit()

    flash(f"Billing cycle for {month} has been rolled back successfully.", "success")
    return redirect(url_for('billing'))

@app.route('/billing/update-settings/<month>', methods=['POST'])
def update_billing_settings(month):
    """Updates due_day and lp_surcharge for an unlocked billing cycle."""
    cycle = BillingCycle.query.get_or_404(month)
    
    if cycle.is_locked:
        flash("🔒 This billing cycle is locked and its settings cannot be changed.", "error")
        return redirect(url_for('billing_details', month=month))
    
    due_day_str = request.form.get('due_day', '').strip()
    lp_surcharge_str = request.form.get('lp_surcharge', '').strip()
    operator_name = request.form.get('operator_name', '').strip()
    
    if not operator_name:
        flash("Operator name is required.", "error")
        return redirect(url_for('billing_details', month=month))
    
    changes = []
    
    if due_day_str:
        try:
            due_day = int(due_day_str)
            if due_day < 1 or due_day > 28:
                raise ValueError()
            if cycle.due_day != due_day:
                changes.append(f"Due day changed from {cycle.due_day} to {due_day}")
                cycle.due_day = due_day
        except ValueError:
            flash("Due day must be between 1 and 28.", "error")
            return redirect(url_for('billing_details', month=month))
    
    if lp_surcharge_str:
        try:
            lp_surcharge = float(lp_surcharge_str)
            if lp_surcharge < 0:
                raise ValueError()
            if cycle.lp_surcharge != lp_surcharge:
                changes.append(f"LP surcharge changed from PKR {cycle.lp_surcharge:,.2f} to PKR {lp_surcharge:,.2f}")
                cycle.lp_surcharge = lp_surcharge
        except ValueError:
            flash("LP surcharge must be a valid non-negative number.", "error")
            return redirect(url_for('billing_details', month=month))
    
    if changes:
        audit = AuditLog(
            action='UPDATE_BILLING_SETTINGS',
            description=f"Updated billing settings for {month}: " + ", ".join(changes),
            performed_by=operator_name
        )
        db.session.add(audit)
        db.session.commit()
        flash(f"Billing settings for {month} updated successfully.", "success")
    else:
        flash("No changes were made.", "info")
    
    return redirect(url_for('billing_details', month=month))

# ----------------- INSTALLMENT ROUTES -----------------

@app.route('/installments')
def installments_list():
    """Lists all installment plans, active first."""
    plans = InstallmentPlan.query.order_by(
        db.case(
            (InstallmentPlan.status == 'Active', 0),
            (InstallmentPlan.status == 'Completed', 1),
            else_=2
        ),
        InstallmentPlan.created_at.desc()
    ).all()

    # Update overdue statuses
    today = datetime.now().strftime("%Y-%m-%d")
    for plan in plans:
        if plan.status == 'Active':
            for inst in plan.installments:
                if inst.status == 'Pending' and inst.due_date < today:
                    inst.status = 'Overdue'
    db.session.commit()

    return render_template('installments.html', plans=plans)

@app.route('/installments/create', methods=['GET', 'POST'])
def installment_create():
    """Create a new installment plan for a tenant."""
    active_leases = Lease.query.filter_by(status='Active').order_by(Lease.tenant_name).all()
    _, today_str = get_current_dates()

    if request.method == 'POST':
        lease_id = request.form.get('lease_id')
        total_str = request.form.get('total_amount', '').strip()
        num_str = request.form.get('num_installments', '').strip()
        start_date = request.form.get('start_date', '').strip()
        notes = request.form.get('notes', '').strip()
        operator = request.form.get('operator_name', '').strip()

        if not lease_id or not total_str or not num_str or not start_date or not operator:
            flash("All fields except notes are required.", "error")
            return redirect(url_for('installment_create'))

        try:
            total_amount = float(total_str)
            num_installments = int(num_str)
            if total_amount <= 0 or num_installments < 2:
                raise ValueError()
        except ValueError:
            flash("Please enter a valid positive amount and at least 2 installments.", "error")
            return redirect(url_for('installment_create'))

        lease = Lease.query.get(lease_id)
        if not lease:
            flash("Tenant not found.", "error")
            return redirect(url_for('installment_create'))

        # Check for existing active plan
        existing = InstallmentPlan.query.filter_by(lease_id=lease.id, status='Active').first()
        if existing:
            flash(f"{lease.tenant_name} already has an active installment plan (Plan #{existing.id}). Cancel it first to create a new one.", "error")
            return redirect(url_for('installment_create'))

        installment_amount = round(total_amount / num_installments, 2)

        # Create the plan
        plan = InstallmentPlan(
            lease_id=lease.id,
            total_amount=total_amount,
            num_installments=num_installments,
            installment_amount=installment_amount,
            start_date=start_date,
            notes=notes or None,
            created_by=operator
        )
        db.session.add(plan)
        db.session.flush()  # Get plan.id

        # Generate individual installments with monthly spacing
        from dateutil.relativedelta import relativedelta
        base_date = datetime.strptime(start_date, "%Y-%m-%d")
        for i in range(num_installments):
            due = base_date + relativedelta(months=i)
            # Last installment absorbs rounding difference
            amt = installment_amount
            if i == num_installments - 1:
                amt = round(total_amount - (installment_amount * (num_installments - 1)), 2)

            inst = Installment(
                plan_id=plan.id,
                installment_number=i + 1,
                due_date=due.strftime("%Y-%m-%d"),
                amount_due=amt
            )
            db.session.add(inst)

        # Audit log
        audit = AuditLog(
            lease_id=lease.id,
            action='CREATE_INSTALLMENT_PLAN',
            description=f"Created installment plan #{plan.id} for {lease.tenant_name}: PKR {total_amount:,.2f} in {num_installments} installments of PKR {installment_amount:,.2f} starting {start_date}.",
            performed_by=operator
        )
        db.session.add(audit)
        db.session.commit()

        flash(f"Installment plan created for {lease.tenant_name}: {num_installments} installments of PKR {installment_amount:,.2f}.", "success")
        return redirect(url_for('installment_detail', plan_id=plan.id))

    return render_template('installment_create.html', leases=active_leases, today_str=today_str)

@app.route('/installments/<int:plan_id>')
def installment_detail(plan_id):
    """View details of a single installment plan."""
    plan = InstallmentPlan.query.get_or_404(plan_id)

    # Update overdue statuses
    today = datetime.now().strftime("%Y-%m-%d")
    if plan.status == 'Active':
        for inst in plan.installments:
            if inst.status == 'Pending' and inst.due_date < today:
                inst.status = 'Overdue'
        db.session.commit()

    return render_template('installment_detail.html', plan=plan)

@app.route('/installments/<int:plan_id>/pay', methods=['POST'])
def installment_pay(plan_id):
    """Pay the next unpaid installment in a plan (supports full or partial payments)."""
    plan = InstallmentPlan.query.get_or_404(plan_id)

    if plan.status != 'Active':
        flash("This plan is no longer active.", "error")
        return redirect(url_for('installment_detail', plan_id=plan.id))

    inst_id = request.form.get('installment_id')
    operator = request.form.get('operator_name', '').strip() or plan.created_by
    inst = Installment.query.get(inst_id)

    if not inst or inst.plan_id != plan.id:
        flash("Invalid installment.", "error")
        return redirect(url_for('installment_detail', plan_id=plan.id))

    pay_amount_input = request.form.get('amount', '').strip()
    
    try:
        if not pay_amount_input:
            pay_amount = inst.remaining
        else:
            pay_amount = float(pay_amount_input)
        
        pay_amount = round(pay_amount, 2)
        if pay_amount <= 0:
            raise ValueError("Payment amount must be greater than zero.")
        if pay_amount > round(inst.remaining, 2):
            raise ValueError(f"Payment amount cannot exceed the remaining balance of PKR {inst.remaining:,.2f}.")
    except ValueError as e:
        flash(str(e) if str(e) else "Please enter a valid numeric payment amount.", "error")
        return redirect(url_for('installment_detail', plan_id=plan.id))

    _, today_str = get_current_dates()

    # Update installment amount paid and status
    inst.amount_paid = round(inst.amount_paid + pay_amount, 2)
    
    is_fully_paid = round(inst.remaining, 2) <= 0
    if is_fully_paid:
        inst.amount_paid = inst.amount_due  # Clean rounding
        inst.status = 'Paid'
        inst.paid_date = today_str
        status_desc = "fully paid"
    else:
        inst.status = 'Partially Paid'
        inst.paid_date = today_str
        status_desc = "partially paid"

    # Create a PAYMENT transaction so it integrates with the balance system
    tx = Transaction(
        lease_id=plan.lease_id,
        type='PAYMENT',
        amount=-pay_amount,
        date=today_str,
        description=f"Installment #{inst.installment_number} payment ({status_desc}) (Plan #{plan.id})"
    )
    db.session.add(tx)

    # Audit log
    audit = AuditLog(
        lease_id=plan.lease_id,
        action='INSTALLMENT_PAYMENT',
        description=f"Paid PKR {pay_amount:,.2f} towards installment #{inst.installment_number} of plan #{plan.id} for {plan.lease.tenant_name}. Installment is now {status_desc}.",
        performed_by=operator
    )
    db.session.add(audit)

    # Check if plan is now complete
    if plan.next_unpaid is None:
        plan.status = 'Completed'
        flash(f"🎉 All installments paid! Plan #{plan.id} is now complete.", "success")
    else:
        flash(f"Installment #{inst.installment_number} payment of PKR {pay_amount:,.2f} logged successfully.", "success")

    db.session.commit()
    return redirect(url_for('installment_detail', plan_id=plan.id))

@app.route('/installments/<int:plan_id>/cancel', methods=['POST'])
def installment_cancel(plan_id):
    """Cancel an active installment plan."""
    plan = InstallmentPlan.query.get_or_404(plan_id)

    if plan.status != 'Active':
        flash("This plan is not active.", "error")
        return redirect(url_for('installment_detail', plan_id=plan.id))

    plan.status = 'Cancelled'

    # Mark remaining unpaid installments as cancelled
    for inst in plan.installments:
        if inst.status in ('Pending', 'Overdue', 'Partially Paid'):
            inst.status = 'Pending'  # Reset to pending (plan cancelled, no longer tracked)

    audit = AuditLog(
        lease_id=plan.lease_id,
        action='CANCEL_INSTALLMENT_PLAN',
        description=f"Cancelled installment plan #{plan.id} for {plan.lease.tenant_name}. {plan.paid_count}/{plan.num_installments} installments were paid.",
        performed_by=request.form.get('operator_name', 'System')
    )
    db.session.add(audit)
    db.session.commit()

    flash(f"Installment plan #{plan.id} has been cancelled.", "warning")
    return redirect(url_for('installments_list'))

# ----------------- AUDIT LOGS -----------------

@app.route('/audit-logs')
def audit_logs():
    """Audit logs screen displaying the financial paper trail."""
    logs = AuditLog.query.order_by(AuditLog.created_at.desc()).all()
    return render_template('audit_logs.html', logs=logs)

# ----------------- EXCEL EXPORT -----------------

@app.route('/export-excel')
def export_excel():
    """Generates and serves a styled Excel workbook backup of all app data."""
    leases = Lease.query.order_by(Lease.status, Lease.tenant_name).all()
    transactions = Transaction.query.order_by(Transaction.created_at.desc()).all()
    audit_logs_list = AuditLog.query.order_by(AuditLog.created_at.desc()).all()
    
    excel_file = generate_excel_export(leases, transactions, audit_logs_list)
    
    filename = f"rental_tracking_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return send_file(
        excel_file,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route('/tenants/statement/<int:lease_id>')
def tenant_statement(lease_id):
    """Shows full ledger statement for a single tenant."""
    lease = Lease.query.get_or_404(lease_id)
    txs = sorted(lease.transactions, key=lambda x: (x.date, x.id))
    
    # Calculate running balances
    ledger = []
    running_bal = 0.0
    for tx in txs:
        running_bal += tx.amount
        ledger.append({
            'tx': tx,
            'running_balance': running_bal
        })
        
    return render_template(
        'tenant_statement.html',
        lease=lease,
        ledger=ledger,
        current_time=datetime.now()
    )

@app.route('/tenants/statement/<int:lease_id>/export')
def export_tenant_statement(lease_id):
    """Generates and serves a styled Excel statement of account for a single tenant."""
    lease = Lease.query.get_or_404(lease_id)
    txs = sorted(lease.transactions, key=lambda x: (x.date, x.id))
    
    excel_file = generate_tenant_statement_excel(lease, txs)
    filename = f"statement_{lease.tenant_name.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return send_file(
        excel_file,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route('/billing/print-single/<int:lease_id>/<month>')
def print_single_bill(lease_id, month):
    """Generates the print slip view for a single tenant's bill in a cycle (3 copies)."""
    from sqlalchemy.orm import subqueryload, joinedload
    
    cycle = BillingCycle.query.get_or_404(month)
    lease = Lease.query.options(
        subqueryload(Lease.transactions),
        subqueryload(Lease.shops).joinedload(Shop.block)
    ).get_or_404(lease_id)
    
    # Verify rent transaction exists for this cycle (check in-memory)
    has_rent = any(t.type == 'RENT' and t.billing_month == month for t in lease.transactions)
    if not has_rent:
        flash("No rent charge found for this tenant in the selected billing cycle.", "error")
        return redirect(url_for('billing_details', month=month))
        
    bill_record = get_bill_record(lease, month, cycle)
    return render_template('print_bills.html', cycle=cycle, bills=[bill_record])

@app.route('/billing/view/<month>/export-payments')
def export_monthly_summary(month):
    """Generates and serves an Excel summary of a specific billing month's payments and ledger."""
    cycle = BillingCycle.query.get_or_404(month)
    
    # 1. Get all transactions of type 'PAYMENT' in this month
    start_date = f"{month}-01"
    end_date = f"{month}-31"
    
    payments = Transaction.query.filter(
        Transaction.type == 'PAYMENT',
        Transaction.date >= start_date,
        Transaction.date <= end_date
    ).order_by(Transaction.date).all()
    
    # 2. Get the billing ledger records for this month
    rent_txs = Transaction.query.filter_by(type='RENT', billing_month=month).all()
    
    bill_records = []
    for tx in rent_txs:
        lease = tx.lease
        txs = lease.transactions
        
        prev_bal = sum(t.amount for t in txs if (t.date < start_date and t.billing_month != month))
        rent = tx.amount
        payment_total = abs(sum(t.amount for t in txs if t.type == 'PAYMENT' and start_date <= t.date <= end_date))
        adjustment_total = sum(t.amount for t in txs if t.type == 'ADJUSTMENT' and (start_date <= t.date <= end_date or t.billing_month == month))
        lp_total = sum(t.amount for t in txs if t.type == 'LATE_FEE' and (start_date <= t.date <= end_date or t.billing_month == month))
        payable = prev_bal + rent - payment_total + adjustment_total + lp_total
        
        bill_records.append({
            'lease': lease,
            'prev_bal': prev_bal,
            'rent': rent,
            'is_waived': lease.is_waived,
            'payments': payment_total,
            'adjustments': adjustment_total,
            'lp_fee': lp_total,
            'payable': payable
        })
        
    from utils import generate_monthly_summary_excel
    excel_file = generate_monthly_summary_excel(cycle, payments, bill_records)
    
    filename = f"billing_summary_{month}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return send_file(
        excel_file,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route('/export-shops-matrix')
def export_shops_matrix():
    """Generates and serves an Excel sheet of all shops with monthly payments."""
    from utils import get_lease_shops_mapping, generate_shops_matrix_excel
    
    # 1. Fetch all blocks and shops
    shops = Shop.query.all()
    shops = sorted(shops, key=lambda s: (s.block.name, len(s.shop_number), s.shop_number))
    
    # 2. Get all generated billing months
    cycles = BillingCycle.query.order_by(BillingCycle.billing_month).all()
    months = [c.billing_month for c in cycles]
    
    # 3. Compile lease-to-shops mapping
    lease_shops = get_lease_shops_mapping()
    
    # 4. Fetch all transactions of type 'PAYMENT'
    payments = Transaction.query.filter_by(type='PAYMENT').all()
    
    lease_payments_by_month = {}
    for p in payments:
        p_month = p.date[:7]
        l_id = p.lease_id
        if l_id not in lease_payments_by_month:
            lease_payments_by_month[l_id] = {}
        lease_payments_by_month[l_id][p_month] = lease_payments_by_month[l_id].get(p_month, 0.0) + abs(p.amount)
        
    excel_file = generate_shops_matrix_excel(shops, months, lease_shops, lease_payments_by_month)
    
    filename = f"shops_monthly_payments_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return send_file(
        excel_file,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

if __name__ == '__main__':
    # Start waitress server on all interfaces when executed directly
    from waitress import serve
    print(f"Rental Tracking App initializing...")
    print(f"Server starting on Local Address: http://localhost:5000")
    print(f"Server starting on LAN Address:    http://{get_local_ip()}:5000")
    serve(app, host='0.0.0.0', port=5000)
