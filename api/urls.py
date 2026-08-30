from django.contrib.auth import views as auth_views
from django.urls import path
from django_ratelimit.decorators import ratelimit

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('courses/', views.courses, name='courses'),
    path('courses/<slug:slug>/', views.course_detail, name='course_detail'),
    path('courses/<slug:slug>/enroll-free/', views.enroll_free, name='enroll_free'),
    path('courses/<slug:slug>/checkout/', views.checkout, name='checkout'),
    path('courses/<slug:slug>/learn/', views.course_learn, name='course_learn'),
    path('courses/<slug:slug>/learn/<int:lesson_id>/toggle/', views.toggle_lesson, name='toggle_lesson'),
    path('courses/<slug:slug>/wishlist/', views.toggle_wishlist, name='toggle_wishlist'),
    path('courses/<slug:slug>/review/', views.submit_review, name='submit_review'),

    path('instructors/<slug:slug>/', views.instructor_detail, name='instructor_detail'),

    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('terms/', views.terms, name='terms'),
    path('privacy/', views.privacy, name='privacy'),
    path('faq/', views.faq, name='faq'),

    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/courses/', views.my_courses, name='my_courses'),
    path('dashboard/progress/', views.progress_overview, name='progress_overview'),
    path('dashboard/certificates/', views.certificates, name='certificates'),
    path('dashboard/certificates/<slug:slug>/', views.certificate_detail, name='certificate_detail'),
    path('orders/', views.my_orders, name='my_orders'),
    path('wishlist/', views.my_wishlist, name='my_wishlist'),
    path('account/', views.account_settings, name='account_settings'),

    path('login/', views.login_view, name='login'),
    path('signup/', views.signup, name='signup'),
    path('logout/', views.logout_view, name='logout'),

    path('password-reset/', ratelimit(key='ip', rate='5/m', method='POST', block=True)(auth_views.PasswordResetView.as_view(
        template_name='password_reset.html',
        email_template_name='password_reset_email.txt',
        subject_template_name='password_reset_subject.txt',
        success_url='/password-reset/done/',
    )), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='password_reset_confirm.html',
        success_url='/password-reset-complete/',
    ), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='password_reset_complete.html',
    ), name='password_reset_complete'),

    path('payments/paystack/callback/', views.paystack_callback, name='paystack_callback'),
    path('payments/paystack/webhook/', views.paystack_webhook, name='paystack_webhook'),

    path('healthz/', views.health_check, name='health_check'),

    path('<path:path>', views.catch_all),
]
