from celery import shared_task


@shared_task(ignore_result=True)
def build_image_rendition(source_name: str):
    """Build WebP variants for one stored photo.

    Page views do not enqueue this. Staff saves build inline, and the backlog
    of existing photos is ``build_image_renditions``.
    """
    from .responsive_images import build_rendition

    return build_rendition(source_name)


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
