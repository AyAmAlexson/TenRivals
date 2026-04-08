from django.urls import path

from . import views

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
    path("preorder/", views.staff_preorder_list, name="staff_preorder"),
    path("blog/", views.staff_blog_posts, name="staff_blog_posts"),
    path("blog/new/", views.staff_blog_edit, name="staff_blog_new"),
    path("blog/<int:post_id>/", views.staff_blog_edit, name="staff_blog_edit"),
    path("shop-home/", views.staff_home_banners, name="staff_home_banners"),
]
