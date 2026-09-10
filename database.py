import os
from models import db, Block, Shop, AppSettings

def init_db(app):
    """Initializes the database with the Flask app context and seeds standard data if empty."""
    db.init_app(app)
    with app.app_context():
        # Configure SQLite optimization event listener inside application context
        from sqlalchemy import event
        
        @event.listens_for(db.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        db.create_all()
        
        # --- Migrate existing BillingCycle table (add new columns if missing) ---
        _migrate_billing_cycle_columns()

        # --- Migrate existing Lease table (add new columns if missing) ---
        _migrate_lease_columns()
        
        # --- Create missing indexes ---
        _create_missing_indexes()
        
        # --- Seed default app settings ---
        _seed_app_settings()
        
        # Seed standard blocks if database is empty
        if Block.query.count() == 0:
            standard_blocks = ["Block A", "Block B", "Block C", "Block D"]
            for name in standard_blocks:
                block = Block(name=name)
                db.session.add(block)
            db.session.commit()
            
            # Seed standard shops 01 to 15 for each block to make the app ready-to-use
            blocks = Block.query.all()
            for block in blocks:
                for i in range(1, 16):
                    shop_num = f"{i:02d}"  # Standardizes as '01', '02', ..., '15'
                    shop = Shop(shop_number=shop_num, block_id=block.id)
                    db.session.add(shop)
            db.session.commit()
            print("Database initialized and seeded with standard Blocks (A-D) and Shops (01-15).")


def _migrate_billing_cycle_columns():
    """Adds new columns to the billing_cycle table for existing databases (SQLite ALTER TABLE)."""
    from sqlalchemy import text, inspect
    
    inspector = inspect(db.engine)
    existing_columns = [col['name'] for col in inspector.get_columns('billing_cycle')]
    
    migrations = [
        ('due_day', 'INTEGER DEFAULT 15 NOT NULL'),
        ('lp_surcharge', 'REAL DEFAULT 1000.0 NOT NULL'),
        ('is_locked', 'BOOLEAN DEFAULT 0 NOT NULL'),
    ]
    
    for col_name, col_def in migrations:
        if col_name not in existing_columns:
            db.session.execute(text(f"ALTER TABLE billing_cycle ADD COLUMN {col_name} {col_def}"))
            print(f"  [Migration] Added column '{col_name}' to billing_cycle table.")
    
    db.session.commit()


def _migrate_lease_columns():
    """Adds new columns to the lease table for existing databases (SQLite ALTER TABLE)."""
    from sqlalchemy import text, inspect

    inspector = inspect(db.engine)
    existing_columns = [col['name'] for col in inspector.get_columns('lease')]

    migrations = [
        ('inactive_date', 'VARCHAR(10)'),
        ('waived_amount', 'FLOAT'),
    ]

    for col_name, col_def in migrations:
        if col_name not in existing_columns:
            db.session.execute(text(f"ALTER TABLE lease ADD COLUMN {col_name} {col_def}"))
            print(f"  [Migration] Added column '{col_name}' to lease table.")

    db.session.commit()


def _create_missing_indexes():
    """Creates indexes if they are missing in an existing SQLite database."""
    from sqlalchemy import text
    
    statements = [
        'CREATE INDEX IF NOT EXISTS ix_transaction_billing_month ON "transaction" (billing_month)',
        'CREATE INDEX IF NOT EXISTS ix_transaction_created_at ON "transaction" (created_at)',
        'CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON "audit_log" (created_at)'
    ]
    
    for stmt in statements:
        try:
            db.session.execute(text(stmt))
        except Exception as e:
            print(f"  [Migration Warning] Could not run index creation: {e}")
            
    db.session.commit()


def _seed_app_settings():
    """Seeds default application settings if they don't exist."""
    defaults = {
        'unlock_passphrase': 'UNLOCK-BILLING',
    }
    for key, value in defaults.items():
        existing = AppSettings.query.get(key)
        if not existing:
            setting = AppSettings(key=key, value=value)
            db.session.add(setting)
    db.session.commit()
