"""
Thin helpers around django.core.mail. All calls are wrapped so a failed
send (e.g. misconfigured SMTP) never breaks the user-facing flow that
triggered it — we log and move on.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

logger = logging.getLogger('api')


def _send(subject, message, to_email):
    if not to_email:
        return
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Failed to send email '%s' to %s", subject, to_email)


def send_welcome_email(user):
    _send(
        subject="Welcome to CyberSpark Enroll",
        message=(
            f"Hi {user.first_name or user.username},\n\n"
            "Your account has been created. You can now browse courses and start learning.\n\n"
            "— CyberSpark IT Solutions"
        ),
        to_email=user.email,
    )


def send_enrollment_email(enrollment):
    _send(
        subject=f"You're enrolled in {enrollment.course.title}",
        message=(
            f"Hi {enrollment.user.first_name or enrollment.user.username},\n\n"
            f"You're now enrolled in \"{enrollment.course.title}\". You can start learning "
            "from your dashboard at any time.\n\n"
            "— CyberSpark IT Solutions"
        ),
        to_email=enrollment.user.email,
    )


def send_payment_confirmation_email(order):
    _send(
        subject=f"Payment confirmed for {order.course.title}",
        message=(
            f"Hi {order.user.first_name or order.user.username},\n\n"
            f"We've confirmed your payment of NGN {order.amount:,.0f} for \"{order.course.title}\" "
            f"({order.get_method_display()}). You're now enrolled and can start learning right away.\n\n"
            f"Reference: {order.reference}\n\n"
            "— CyberSpark IT Solutions"
        ),
        to_email=order.user.email,
    )


def send_bank_transfer_received_email(order):
    _send(
        subject=f"We received your payment proof for {order.course.title}",
        message=(
            f"Hi {order.user.first_name or order.user.username},\n\n"
            f"Thanks — we've received your bank transfer proof for \"{order.course.title}\". "
            "Our team will verify it and confirm your enrollment within 24 hours.\n\n"
            f"Reference: {order.reference}\n\n"
            "— CyberSpark IT Solutions"
        ),
        to_email=order.user.email,
    )


def send_contact_form_submission(name, email, subject, message):
    if not settings.CONTACT_RECIPIENT_EMAIL:
        return
    try:
        from django.core.mail import EmailMessage

        msg = EmailMessage(
            subject=f"[Contact form] {subject}",
            body=f"From: {name} <{email}>\n\n{message}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[settings.CONTACT_RECIPIENT_EMAIL],
            reply_to=[email],
        )
        msg.send(fail_silently=False)
    except Exception:
        logger.exception("Failed to send contact form email from %s", email)
