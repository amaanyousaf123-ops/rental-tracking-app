import socket
import io
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

_cached_local_ip = None

def get_local_ip():
    """Returns the primary local IPv4 address of the computer on the LAN (cached)."""
    global _cached_local_ip
    if _cached_local_ip is not None:
        return _cached_local_ip
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable, just triggers local IP routing resolution
        s.connect(('10.255.255.255', 1))
        _cached_local_ip = s.getsockname()[0]
    except Exception:
        _cached_local_ip = '127.0.0.1'
    finally:
        s.close()
    return _cached_local_ip

def generate_excel_export(leases, transactions, audit_logs):
    """Generates an in-memory formatted Excel spreadsheet with three sheets: 
       Balances, Transactions, and Audit Trail."""
    wb = Workbook()
    
    # 1. Sheet: Active Tenant Balances
    ws1 = wb.active
    ws1.title = "Tenant Balances"
    headers1 = ["Tenant Name", "Shop Number(s)", "Block(s)", "Monthly Rent (PKR)", "Status", "Current Dues (PKR)", "Notes"]
    ws1.append(headers1)
    
    for l in leases:
        rent_val = "-" if l.is_waived else l.monthly_rent
        ws1.append([
            l.tenant_name,
            l.formatted_shops,
            l.formatted_blocks,
            rent_val,
            l.status,
            l.current_balance,
            l.notes or ""
        ])
    style_sheet(ws1, headers1)

    # 2. Sheet: Transaction History (Full Ledger Dump)
    ws2 = wb.create_sheet(title="Transaction History")
    headers2 = ["ID", "Tenant Name", "Shops", "Block", "Type", "Amount (PKR)", "Date", "Billing Month", "Description", "Logged At"]
    ws2.append(headers2)
    
    for tx in transactions:
        ws2.append([
            tx.id,
            tx.lease.tenant_name if tx.lease else "Unknown Tenant",
            tx.lease.formatted_shops if tx.lease else "N/A",
            tx.lease.formatted_blocks if tx.lease else "N/A",
            tx.type,
            tx.amount,
            tx.date,
            tx.billing_month or "-",
            tx.description or "",
            tx.created_at.strftime("%Y-%m-%d %H:%M:%S")
        ])
    style_sheet(ws2, headers2)
    
    # 3. Sheet: Audit Log Paper Trail
    ws3 = wb.create_sheet(title="Audit Log")
    headers3 = ["ID", "Tenant Name", "Action", "Description", "Performed By", "Timestamp"]
    ws3.append(headers3)
    
    for log in audit_logs:
        ws3.append([
            log.id,
            log.lease.tenant_name if log.lease else "System/General",
            log.action,
            log.description,
            log.performed_by,
            log.created_at.strftime("%Y-%m-%d %H:%M:%S")
        ])
    style_sheet(ws3, headers3)
    
    # Save to buffer
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def style_sheet(ws, headers):
    """Applies professional typography, formatting, row coloring, and sizes to worksheets."""
    # Color palette
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=11)
    
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )
    
    # Format headers
    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
    
    # Format data rows
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=len(headers)):
        for cell in row:
            cell.font = data_font
            cell.border = thin_border
            
            # Format numbers, Align text left, Align status/dates center
            if isinstance(cell.value, (int, float)):
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif cell.value == "-":
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
                
    # Configure row dimensions
    ws.row_dimensions[1].height = 28
    for r in range(2, ws.max_row + 1):
        ws.row_dimensions[r].height = 20
        
    # Auto-fit columns
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

def generate_tenant_statement_excel(lease, transactions):
    """Generates an in-memory formatted Excel statement of account for a single tenant."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Statement of Account"
    
    # Title Block
    ws.append([f"STATEMENT OF ACCOUNT - {lease.tenant_name.upper()}"])
    ws.append([f"Shop(s): {lease.formatted_shops} | Block(s): {lease.formatted_blocks}"])
    ws.append([f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    ws.append([]) # empty separator row
    
    headers = ["ID", "Date", "Billing Month", "Type", "Description", "Amount (PKR)", "Running Balance (PKR)"]
    ws.append(headers)
    
    running_bal = 0.0
    for tx in transactions:
        running_bal += tx.amount
        ws.append([
            tx.id,
            tx.date,
            tx.billing_month or "-",
            tx.type,
            tx.description or "",
            tx.amount,
            running_bal
        ])
        
    # Apply styling
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=11)
    title_font = Font(name="Segoe UI", size=14, bold=True, color="1F4E78")
    meta_font = Font(name="Segoe UI", size=10, italic=True)
    
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )
    
    # Merge and style title rows
    ws.merge_cells("A1:G1")
    ws["A1"].font = title_font
    ws.merge_cells("A2:G2")
    ws["A2"].font = meta_font
    ws.merge_cells("A3:G3")
    ws["A3"].font = meta_font
    
    # Header styling (row 5)
    for col_num in range(1, 8):
        cell = ws.cell(row=5, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
    # Data rows styling (row 6 onwards)
    for r in range(6, ws.max_row + 1):
        for col_num in range(1, 8):
            cell = ws.cell(row=r, column=col_num)
            cell.font = data_font
            cell.border = thin_border
            
            # Formats
            if col_num in [6, 7]: # Amount and Running Balance
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in [1, 2, 3, 4]: # ID, Date, Month, Type
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
                
    ws.row_dimensions[5].height = 28
    for r in range(6, ws.max_row + 1):
        ws.row_dimensions[r].height = 20
        
    # Auto-fit columns
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or '')
            if cell.row < 5:
                continue
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)
        
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def generate_monthly_summary_excel(cycle, payments, bill_records):
    """Generates an Excel workbook with two sheets: Monthly Payments Logged and Billing Ledger Summary."""
    wb = Workbook()
    
    # Sheet 1: Payments Logged
    ws1 = wb.active
    ws1.title = "Payments Logged"
    
    # Title Block
    dt = datetime.strptime(cycle.billing_month, "%Y-%m")
    month_name = dt.strftime("%B %Y")
    
    ws1.append([f"PAYMENTS LOGGED IN {month_name.upper()}"])
    ws1.append([f"Billing Cycle Due Date: {cycle.due_day}th | LP Surcharge: PKR {cycle.lp_surcharge:,.2f}"])
    ws1.append([f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    ws1.append([]) # spacer
    
    headers1 = ["Transaction ID", "Payment Date", "Tenant Name", "Shop Number(s)", "Block(s)", "Amount Paid (PKR)", "Description", "Logged At"]
    ws1.append(headers1)
    
    total_paid = 0.0
    for p in payments:
        amount_abs = abs(p.amount)
        total_paid += amount_abs
        ws1.append([
            p.id,
            p.date,
            p.lease.tenant_name if p.lease else "Unknown",
            p.lease.formatted_shops if p.lease else "N/A",
            p.lease.formatted_blocks if p.lease else "N/A",
            amount_abs,
            p.description or "Rent Payment",
            p.created_at.strftime("%Y-%m-%d %H:%M:%S")
        ])
        
    # Append Total Row
    ws1.append([])
    ws1.append(["TOTAL PAYMENTS COLLECTED", "", "", "", "", total_paid])
    
    # Styling Sheet 1
    style_monthly_payments_sheet(ws1, headers1)
    
    # Sheet 2: Billing Ledger Summary
    ws2 = wb.create_sheet(title="Billing Ledger Summary")
    ws2.append([f"BILLING LEDGER SUMMARY - {month_name.upper()}"])
    ws2.append([f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    ws2.append([]) # spacer
    
    headers2 = ["Tenant Name", "Shop Number(s)", "Block(s)", "Previous Balance (PKR)", "Monthly Rent (PKR)", "Payments Received (PKR)", "Adjustments (PKR)", "Late Fee Surcharge (PKR)", "Net Payable (PKR)"]
    ws2.append(headers2)
    
    tot_prev = tot_rent = tot_paid_rec = tot_adj = tot_lp = tot_payable = 0.0
    for r in bill_records:
        rent_val = 0.0 if r['is_waived'] else r['rent']
        
        tot_prev += r['prev_bal']
        tot_rent += rent_val
        tot_paid_rec += r['payments']
        tot_adj += r['adjustments']
        tot_lp += r['lp_fee']
        tot_payable += r['payable']
        
        ws2.append([
            r['lease'].tenant_name,
            r['lease'].formatted_shops,
            r['lease'].formatted_blocks,
            r['prev_bal'],
            rent_val,
            r['payments'],
            r['adjustments'],
            r['lp_fee'],
            r['payable']
        ])
        
    # Append totals
    ws2.append([])
    ws2.append([
        "TOTALS", "", "",
        tot_prev,
        tot_rent,
        tot_paid_rec,
        tot_adj,
        tot_lp,
        tot_payable
    ])
    
    style_monthly_ledger_sheet(ws2, headers2)
    
    # Save to buffer
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def style_monthly_payments_sheet(ws, headers):
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=11)
    total_font = Font(name="Segoe UI", size=11, bold=True)
    title_font = Font(name="Segoe UI", size=14, bold=True, color="1F4E78")
    meta_font = Font(name="Segoe UI", size=10, italic=True)
    
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )
    
    # Merge and style title rows
    ws.merge_cells("A1:H1")
    ws["A1"].font = title_font
    ws.merge_cells("A2:H2")
    ws["A2"].font = meta_font
    ws.merge_cells("A3:H3")
    ws["A3"].font = meta_font
    
    # Headers at row 5
    for col_num in range(1, 9):
        cell = ws.cell(row=5, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
    # Data rows from row 6 onwards
    for r in range(6, ws.max_row + 1):
        is_total_row = (ws.cell(row=r, column=1).value == "TOTAL PAYMENTS COLLECTED")
        for col_num in range(1, 9):
            cell = ws.cell(row=r, column=col_num)
            cell.font = total_font if is_total_row else data_font
            cell.border = thin_border
            
            if is_total_row:
                if col_num == 1:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                elif col_num == 6:
                    cell.number_format = '#,##0.00'
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                continue
                
            # Formatting
            if col_num == 6: # Amount Paid
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in [1, 2, 8]: # ID, Date, Logged At
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
                
    ws.row_dimensions[5].height = 28
    for r in range(6, ws.max_row + 1):
        ws.row_dimensions[r].height = 20
        
    # Auto-fit columns
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row < 5:
                continue
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

def style_monthly_ledger_sheet(ws, headers):
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=11)
    total_font = Font(name="Segoe UI", size=11, bold=True)
    title_font = Font(name="Segoe UI", size=14, bold=True, color="1F4E78")
    meta_font = Font(name="Segoe UI", size=10, italic=True)
    
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )
    
    # Merge and style title rows
    ws.merge_cells("A1:I1")
    ws["A1"].font = title_font
    ws.merge_cells("A2:I2")
    ws["A2"].font = meta_font
    
    # Headers at row 4
    for col_num in range(1, 10):
        cell = ws.cell(row=4, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
    # Data rows from row 5 onwards
    for r in range(5, ws.max_row + 1):
        is_total_row = (ws.cell(row=r, column=1).value == "TOTALS")
        for col_num in range(1, 10):
            cell = ws.cell(row=r, column=col_num)
            cell.font = total_font if is_total_row else data_font
            cell.border = thin_border
            
            if is_total_row:
                if col_num == 1:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                elif col_num >= 4:
                    cell.number_format = '#,##0.00'
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                continue
                
            # Formatting
            if col_num >= 4: # Numeric columns
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in [2, 3]: # Shops, Blocks
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
                
    ws.row_dimensions[4].height = 28
    for r in range(5, ws.max_row + 1):
        ws.row_dimensions[r].height = 20
        
    # Auto-fit columns
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row < 4:
                continue
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

def get_lease_shops_mapping():
    """Builds a mapping of lease_id -> list of (block_name, shop_number) for both active and inactive leases."""
    from models import Lease, AuditLog, Shop, Block
    mapping = {}
    
    # 1. Active Leases
    active_leases = Lease.query.filter_by(status='Active').all()
    for lease in active_leases:
        mapping[lease.id] = [(s.block.name, s.shop_number) for s in lease.shops]
        
    # 2. Inactive Leases (parsed from AuditLogs)
    inactive_leases = Lease.query.filter_by(status='Inactive').all()
    inactive_lease_ids = {l.id for l in inactive_leases}
    
    import re
    # Fetch AuditLogs that contain shop details for deactivations
    audit_logs = AuditLog.query.filter(
        AuditLog.lease_id.in_(inactive_lease_ids),
        AuditLog.action.in_(['CREATE_LEASE', 'DEACTIVATE_LEASE', 'UPDATE_LEASE'])
    ).all()
    
    for log in audit_logs:
        if log.lease_id in mapping:
            continue
            
        desc = log.description
        shops_list = []
        blocks_list = []
        
        # Parse blocks
        block_match = re.search(r"in\s+([A-Za-z0-9\s&]+?)(?:\. Starting|\. Final|$)", desc)
        if block_match:
            blocks_str = block_match.group(1).strip()
            blocks_list = [b.strip() for b in re.split(r"&|,", blocks_str) if "Block" in b]
            
        # Parse shop numbers
        if "Freeing shops:" in desc:
            match = re.search(r"Freeing shops:\s*([^.]+)", desc)
            if match:
                shops_str = match.group(1).strip()
                shops_list = [s.strip() for s in re.split(r"&|,", shops_str) if s.strip()]
        elif "covering shop(s)" in desc:
            match = re.search(r"covering shop\(s\)\s+([^in]+)\s+in", desc)
            if match:
                shops_str = match.group(1).strip()
                shops_list = [s.strip() for s in re.split(r"&|,", shops_str) if s.strip()]
                
        if shops_list:
            block_names = [b for b in blocks_list]
            if not block_names:
                for b_name in ["Block A", "Block B", "Block C", "Block D", "Block E"]:
                    if b_name in desc:
                        block_names.append(b_name)
                        
            resolved_shops = []
            for shop_num in shops_list:
                matched_block = None
                for bn in block_names:
                    exists = Shop.query.join(Block).filter(Block.name == bn, Shop.shop_number == shop_num).first()
                    if exists:
                        matched_block = bn
                        break
                if not matched_block:
                    exists = Shop.query.filter(Shop.shop_number == shop_num).first()
                    if exists:
                        matched_block = exists.block.name
                
                if matched_block:
                    resolved_shops.append((matched_block, shop_num))
                else:
                    resolved_shops.append(("Unknown Block", shop_num))
                    
            mapping[log.lease_id] = resolved_shops
            
    return mapping

def generate_shops_matrix_excel(shops, months, lease_shops, lease_payments_by_month):
    """Generates a shop-wise matrix of monthly payments in an Excel sheet."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Shops Monthly Payments"
    
    # Title Block
    ws.append(["RENTAL TRACKING APP - SHOPS MONTHLY PAYMENTS LEDGER"])
    ws.append([f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"])
    ws.append([]) # spacer
    
    # Headers
    headers = ["Block", "Shop Number", "Current Status", "Current Tenant", "Monthly Rent (PKR)"]
    # Add month columns (formatted like 'Aug-26')
    for m in months:
        dt = datetime.strptime(m, "%Y-%m")
        headers.append(f"{dt.strftime('%b-%y')} Paid (PKR)")
    headers.append("Total Paid (PKR)")
    
    ws.append(headers)
    
    # Map shop to lease ID (active leases)
    shop_active_lease_map = {}
    for s in shops:
        if s.lease and s.lease.status == 'Active':
            shop_active_lease_map[s.id] = s.lease
            
    # Iterate shops and calculate payments per month
    month_totals = {m: 0.0 for m in months}
    grand_total_paid = 0.0
    
    for s in shops:
        block_name = s.block.name
        shop_num = s.shop_number
        
        # Current status & tenant
        active_lease = shop_active_lease_map.get(s.id)
        status = "Leased" if active_lease else "Vacant"
        tenant_name = active_lease.tenant_name if active_lease else "—"
        rent_val = active_lease.monthly_rent if (active_lease and not active_lease.is_waived) else 0.0
        
        row_data = [block_name, shop_num, status, tenant_name, rent_val]
        
        # Find which leases historically (or currently) occupied this shop
        shop_total_paid = 0.0
        for m in months:
            payment_for_shop_in_month = 0.0
            
            # Loop over all leases that match this shop
            for lease_id, occupied_shops in lease_shops.items():
                if (block_name, shop_num) in occupied_shops:
                    # Yes, this lease occupied this shop!
                    # Check if this lease has payment in month 'm'
                    total_lease_pay = lease_payments_by_month.get(lease_id, {}).get(m, 0.0)
                    if total_lease_pay > 0:
                        # Split equally among the shops occupied by the lease
                        payment_for_shop_in_month += total_lease_pay / len(occupied_shops)
                        
            row_data.append(payment_for_shop_in_month)
            month_totals[m] += payment_for_shop_in_month
            shop_total_paid += payment_for_shop_in_month
            
        row_data.append(shop_total_paid)
        grand_total_paid += shop_total_paid
        ws.append(row_data)
        
    # Append Total row
    ws.append([])
    total_row = ["TOTALS", "", "", "", ""]
    for m in months:
        total_row.append(month_totals[m])
    total_row.append(grand_total_paid)
    ws.append(total_row)
    
    # Styling Matrix Sheet
    style_shops_matrix_sheet(ws, headers, len(months))
    
    # Save to buffer
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

def style_shops_matrix_sheet(ws, headers, num_months):
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Segoe UI", size=11)
    total_font = Font(name="Segoe UI", size=11, bold=True)
    title_font = Font(name="Segoe UI", size=14, bold=True, color="1F4E78")
    meta_font = Font(name="Segoe UI", size=10, italic=True)
    
    thin_border = Border(
        left=Side(style='thin', color='E0E0E0'),
        right=Side(style='thin', color='E0E0E0'),
        top=Side(style='thin', color='E0E0E0'),
        bottom=Side(style='thin', color='E0E0E0')
    )
    
    # Merge and style title rows
    num_cols = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=num_cols)
    ws.cell(row=1, column=1).font = title_font
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=num_cols)
    ws.cell(row=2, column=1).font = meta_font
    
    # Headers at row 4
    for col_num in range(1, num_cols + 1):
        cell = ws.cell(row=4, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        
    # Data rows from row 5 onwards
    for r in range(5, ws.max_row + 1):
        is_total_row = (ws.cell(row=r, column=1).value == "TOTALS")
        for col_num in range(1, num_cols + 1):
            cell = ws.cell(row=r, column=col_num)
            cell.font = total_font if is_total_row else data_font
            cell.border = thin_border
            
            if is_total_row:
                if col_num == 1:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                elif col_num >= 5:
                    cell.number_format = '#,##0.00'
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                continue
                
            # Formatting
            if col_num >= 5: # Monthly Rent, Month Columns, Total Column
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col_num in [1, 2, 3]: # Block, Shop, Status
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
                
    ws.row_dimensions[4].height = 28
    for r in range(5, ws.max_row + 1):
        ws.row_dimensions[r].height = 20
        
    # Auto-fit columns
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row < 4:
                continue
            val_str = str(cell.value or '')
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)
