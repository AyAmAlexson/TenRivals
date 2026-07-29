from celery import shared_task


@shared_task
def snapshot_stock_value():
    """Nightly: persist today's measured stock shelf / landed value."""
    from .stock_value_history import upsert_stock_snapshot

    row = upsert_stock_snapshot()
    return {
        'date': str(row.snapshot_date),
        'units': row.units,
        'shelf': str(row.shelf_value_gel),
        'landed': str(row.landed_value_gel),
    }
