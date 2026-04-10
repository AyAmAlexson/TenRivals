from django.urls import path

from . import views
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
        "orders/<int:pk>/edit/",
        staff_sales_views.staff_sales_order_edit,
        name="staff_sales_order_edit",
    ),
    path("orders/", staff_sales_views.staff_sales_orders, name="staff_sales_orders"),
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
    path(
        "orders/<int:pk>/invoice.pdf",
        staff_sales_views.staff_sales_order_invoice_pdf,
        name="staff_sales_order_invoice_pdf",
    ),
]
