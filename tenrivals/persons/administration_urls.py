from django.urls import path

from . import views
from . import staff_promo_views
from shop import staff_sales_views

app_name = "administration"

urlpatterns = [
    path("users/", views.staff_users, name="staff_users"),
    path(
        "users/<int:user_id>/send-email-verification/",
        views.superuser_send_email_verification,
        name="superuser_send_email_verification",
    ),
    path(
        "users/<int:user_id>/edit/",
        views.superuser_user_edit,
        name="superuser_user_edit",
    ),
    path("stock/", views.staff_stock_list, name="staff_stock"),
    path("stock/stats/", views.staff_stock_stats, name="staff_stock_stats"),
    path("preorder/", views.staff_preorder_list, name="staff_preorder"),
    path("blog/", views.staff_blog_posts, name="staff_blog_posts"),
    path("blog/new/", views.staff_blog_edit, name="staff_blog_new"),
    path("blog/<int:post_id>/", views.staff_blog_edit, name="staff_blog_edit"),
    path("shop-home/", views.staff_home_banners, name="staff_home_banners"),
    path("collections/", views.staff_collections, name="staff_collections"),
    path("collections/new/", views.staff_collection_edit, name="staff_collection_new"),
    path(
        "collections/<int:collection_id>/",
        views.staff_collection_edit,
        name="staff_collection_edit",
    ),
    path("promo-codes/", staff_promo_views.staff_promo_codes, name="staff_promo_codes"),
    path(
        "promo-codes/new/",
        staff_promo_views.staff_promo_edit,
        {"promo_id": None},
        name="staff_promo_new",
    ),
    path(
        "promo-codes/<int:promo_id>/",
        staff_promo_views.staff_promo_edit,
        name="staff_promo_edit",
    ),
    path("order-for-me/", views.staff_order_for_me_list, name="staff_order_for_me_list"),
    path("order-for-me/new/", views.staff_order_for_me_edit, name="staff_order_for_me_new"),
    path(
        "order-for-me/<int:order_id>/",
        views.staff_order_for_me_edit,
        name="staff_order_for_me_edit",
    ),
    path("customers/new/", staff_sales_views.staff_customer_edit, name="staff_customer_new"),
    path(
        "customers/<int:pk>/edit/",
        staff_sales_views.staff_customer_edit,
        name="staff_customer_edit",
    ),
    path("customers/", staff_sales_views.staff_customers, name="staff_customers"),
    path(
        "customers/<int:pk>/delete/",
        staff_sales_views.staff_customer_delete,
        name="staff_customer_delete",
    ),
    path("orders/new/", staff_sales_views.staff_sales_order_edit, name="staff_sales_order_new"),
    path(
        "orders/report/",
        staff_sales_views.staff_sales_orders_month_report,
        name="staff_sales_orders_month_report",
    ),
    path(
        "orders/sequence/",
        staff_sales_views.staff_sales_invoice_sequence_set,
        name="staff_sales_invoice_sequence_set",
    ),
    path(
        "orders/<int:pk>/edit/",
        staff_sales_views.staff_sales_order_edit,
        name="staff_sales_order_edit",
    ),
    path("orders/", staff_sales_views.staff_sales_orders, name="staff_sales_orders"),
    path(
        "orders/<int:pk>/status/",
        staff_sales_views.staff_sales_order_set_status,
        name="staff_sales_order_set_status",
    ),
    path(
        "orders/<int:pk>/delete/",
        staff_sales_views.staff_sales_order_delete,
        name="staff_sales_order_delete",
    ),
    path(
        "orders/<int:pk>/invoice/",
        staff_sales_views.staff_sales_order_invoice,
        name="staff_sales_order_invoice",
    ),
]
