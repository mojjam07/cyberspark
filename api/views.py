import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from django_ratelimit.decorators import ratelimit

from .forms import (
    AccountUpdateForm, BankTransferProofForm, ContactForm, CouponForm, LoginForm, ReviewForm, SignupForm,
    StyledPasswordChangeForm,
)
from .models import Category, Coupon, Course, Enrollment, Instructor, Order, Review, Wishlist
from . import emails, paystack

logger = logging.getLogger('api')


def home(request):
    stats = Course.objects.filter(is_published=True).aggregate(
        total_courses=Count('id'),
        avg_rating=Avg('rating'),
    )
    featured_courses = Course.objects.filter(is_published=True, is_featured=True).select_related('category')[:6]
    context = {
        'view_name': 'Home',
        'hero_stats': [
            {'val': stats['total_courses'] or 0, 'lbl': 'Courses live'},
            {'val': f"{stats['avg_rating']:.1f}" if stats['avg_rating'] else '0', 'lbl': 'Avg rating'},
            {'val': '24/7', 'lbl': 'Support'},
        ],
        'featured_courses': featured_courses,
    }
    return render(request, 'home.html', context)


def courses(request):
    course_list = Course.objects.filter(is_published=True).select_related('category').order_by('-created_at')

    category_slug = request.GET.get('category')
    if category_slug:
        course_list = course_list.filter(category__slug=category_slug)

    query = request.GET.get('q', '').strip()
    if query:
        course_list = course_list.filter(title__icontains=query)

    context = {
        'view_name': 'Courses',
        'course_list': course_list,
        'categories': Category.objects.all(),
        'active_category': category_slug or '',
        'query': query,
    }
    return render(request, 'courses.html', context)


def course_detail(request, slug):
    course = get_object_or_404(Course.objects.select_related('category', 'instructor_profile'), slug=slug, is_published=True)
    is_enrolled = False
    is_wishlisted = False
    user_review = None
    if request.user.is_authenticated:
        is_enrolled = Enrollment.objects.filter(user=request.user, course=course).exists()
        is_wishlisted = Wishlist.objects.filter(user=request.user, course=course).exists()
        user_review = Review.objects.filter(user=request.user, course=course).first()

    reviews = course.reviews.select_related('user').exclude(user=request.user if request.user.is_authenticated else None)[:20]

    context = {
        'view_name': course.title,
        'course': course,
        'is_enrolled': is_enrolled,
        'is_wishlisted': is_wishlisted,
        'reviews': reviews,
        'user_review': user_review,
        'review_form': ReviewForm(instance=user_review),
    }
    return render(request, 'course_detail.html', context)


@login_required
@require_POST
def toggle_wishlist(request, slug):
    course = get_object_or_404(Course, slug=slug, is_published=True)
    item, created = Wishlist.objects.get_or_create(user=request.user, course=course)
    if not created:
        item.delete()
        messages.info(request, "Removed from your wishlist.")
    else:
        messages.success(request, "Saved to your wishlist.")
    next_url = request.POST.get('next') or reverse('course_detail', args=[slug])
    return redirect(next_url)


@login_required
def my_wishlist(request):
    items = Wishlist.objects.filter(user=request.user).select_related('course', 'course__category')
    return render(request, 'wishlist.html', {'view_name': 'My Wishlist', 'items': items})


@login_required
@require_POST
def submit_review(request, slug):
    course = get_object_or_404(Course, slug=slug, is_published=True)
    if not Enrollment.objects.filter(user=request.user, course=course).exists():
        messages.error(request, "You can only review courses you're enrolled in.")
        return redirect('course_detail', slug=slug)

    existing = Review.objects.filter(user=request.user, course=course).first()
    form = ReviewForm(request.POST, instance=existing)
    if form.is_valid():
        review = form.save(commit=False)
        review.user = request.user
        review.course = course
        review.save()
        messages.success(request, "Thanks — your review has been posted.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
    return redirect('course_detail', slug=slug)


def instructor_detail(request, slug):
    instructor = get_object_or_404(Instructor, slug=slug)
    courses = instructor.courses_taught.filter(is_published=True).select_related('category')
    return render(request, 'instructor_detail.html', {
        'view_name': instructor.name,
        'instructor': instructor,
        'courses': courses,
    })


@login_required
def dashboard(request):
    current_date = timezone.localdate().strftime("%A · %B %Y")
    user_name = request.user.get_full_name() or request.user.username

    active_count = Enrollment.objects.filter(user=request.user, completed_at__isnull=True).count()
    avg_progress = Enrollment.objects.filter(user=request.user).aggregate(avg=Avg('progress'))['avg']
    total_hours = Enrollment.objects.filter(user=request.user).aggregate(total=Sum('total_hours_spent'))['total'] or 0

    enrollments = Enrollment.objects.filter(user=request.user).select_related('course')[:5]
    enrolled_course_ids = Enrollment.objects.filter(user=request.user).values_list('course_id', flat=True)
    recommendations = Course.objects.filter(is_published=True, is_featured=True).exclude(id__in=enrolled_course_ids)[:3]

    enrollments_list = [
        {'name': e.course.title, 'instructor': e.course.instructor, 'pct': f"{e.progress}%", 'slug': e.course.slug}
        for e in enrollments
    ]
    recommendations_list = []
    for c in recommendations:
        rating_str = f"★ {c.rating} ({c.rating_count})" if c.rating_count else ("FREE" if c.is_free else "New")
        recommendations_list.append({'name': c.title, 'instructor': c.instructor, 'rating': rating_str, 'slug': c.slug})

    pending_orders = Order.objects.filter(
        user=request.user, status__in=[Order.Status.PENDING, Order.Status.AWAITING_REVIEW]
    ).select_related('course')

    context = {
        'view_name': 'Dashboard',
        'user_name': user_name,
        'greeting_time': current_date,
        'metrics': [
            {'label': 'Active Courses', 'value': active_count, 'desc': f'{active_count} in progress'},
            {'label': 'Avg Progress', 'value': f"{avg_progress:.0f}%" if avg_progress else '0%', 'desc': 'This month'},
            {'label': 'Hours Learned', 'value': total_hours, 'desc': 'Total'},
        ],
        'enrollments': enrollments_list,
        'recommendations': recommendations_list,
        'pending_orders': pending_orders,
    }
    return render(request, 'dashboard.html', context)


@login_required
def my_courses(request):
    enrollments = Enrollment.objects.filter(user=request.user).select_related('course', 'course__category')
    in_progress = enrollments.filter(completed_at__isnull=True)
    completed = enrollments.filter(completed_at__isnull=False)
    context = {
        'view_name': 'My Courses',
        'in_progress': in_progress,
        'completed': completed,
    }
    return render(request, 'my_courses.html', context)


@login_required
def progress_overview(request):
    enrollments = Enrollment.objects.filter(user=request.user).select_related('course').order_by('-progress')

    stats = enrollments.aggregate(
        avg_progress=Avg('progress'),
        total_hours=Sum('total_hours_spent'),
    )
    total_count = enrollments.count()
    completed_count = enrollments.filter(completed_at__isnull=False).count()

    context = {
        'view_name': 'Progress',
        'enrollments': enrollments,
        'total_count': total_count,
        'completed_count': completed_count,
        'in_progress_count': total_count - completed_count,
        'avg_progress': stats['avg_progress'] or 0,
        'total_hours': stats['total_hours'] or 0,
    }
    return render(request, 'progress.html', context)


@login_required
def certificates(request):
    completed = Enrollment.objects.filter(
        user=request.user, completed_at__isnull=False
    ).select_related('course').order_by('-completed_at')
    return render(request, 'certificates.html', {'view_name': 'Certificates', 'completed': completed})


@login_required
def certificate_detail(request, slug):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related('course'),
        user=request.user, course__slug=slug, completed_at__isnull=False,
    )
    return render(request, 'certificate.html', {
        'view_name': f'Certificate — {enrollment.course.title}',
        'enrollment': enrollment,
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def login_view(request):
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data['username'].strip().lower(),
            password=form.cleaned_data['password'],
        )
        if user is not None:
            auth_login(request, user)
            next_url = request.GET.get('next') or reverse('dashboard')
            return redirect(next_url)
        messages.error(request, 'Invalid email or password.')
    elif request.method == 'POST' and not form.is_valid():
        messages.error(request, 'Please enter both your email and password.')
    return render(request, 'login.html', {'view_name': 'Sign In', 'form': form})


@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def signup(request):
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        emails.send_welcome_email(user)
        messages.success(request, 'Account created successfully! Please log in.')
        return redirect('login')
    elif request.method == 'POST':
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, error)
    return render(request, 'signup.html', {'view_name': 'Sign Up', 'form': form})


def logout_view(request):
    auth_logout(request)
    return redirect('home')


# ---------------------------------------------------------------------
# Enrollment / payments
# ---------------------------------------------------------------------

@login_required
@require_POST
def enroll_free(request, slug):
    course = get_object_or_404(Course, slug=slug, is_published=True, is_free=True)
    enrollment, created = Enrollment.objects.get_or_create(user=request.user, course=course)
    if created:
        emails.send_enrollment_email(enrollment)
    messages.success(request, f"You're enrolled in {course.title}!")
    return redirect('dashboard')


@login_required
def checkout(request, slug):
    course = get_object_or_404(Course, slug=slug, is_published=True)

    if course.is_free:
        return redirect('course_detail', slug=slug)

    if Enrollment.objects.filter(user=request.user, course=course).exists():
        messages.info(request, "You're already enrolled in this course.")
        return redirect('dashboard')

    session_key = f'coupon:{course.slug}'
    applied_coupon = None
    coupon_error = None
    session_code = request.session.get(session_key)
    if session_code:
        try:
            candidate = Coupon.objects.get(code__iexact=session_code)
            if candidate.is_valid():
                applied_coupon = candidate
            else:
                del request.session[session_key]
        except Coupon.DoesNotExist:
            del request.session[session_key]

    final_amount = applied_coupon.apply_to(course.price) if applied_coupon else course.price
    discount_amount = (course.price - final_amount) if applied_coupon else 0

    coupon_form = CouponForm()
    bank_form = BankTransferProofForm(
        request.POST,
        request.FILES,
    ) if request.method == 'POST' and request.POST.get('method') == 'bank_transfer' else BankTransferProofForm()

    if request.method == 'POST':
        method = request.POST.get('method')

        if method == 'apply_coupon':
            coupon_form = CouponForm(request.POST)
            if coupon_form.is_valid():
                code = coupon_form.cleaned_data['code'].strip()
                try:
                    coupon = Coupon.objects.get(code__iexact=code)
                    if coupon.is_valid():
                        request.session[session_key] = coupon.code
                        messages.success(request, f"Coupon \"{coupon.code}\" applied — {coupon.discount_percent}% off.")
                    else:
                        messages.error(request, "That coupon has expired or reached its usage limit.")
                except Coupon.DoesNotExist:
                    messages.error(request, "That coupon code isn't valid.")
            return redirect('checkout', slug=slug)

        elif method == 'remove_coupon':
            request.session.pop(session_key, None)
            messages.info(request, "Coupon removed.")
            return redirect('checkout', slug=slug)

        elif method == 'paystack':
            order = Order.objects.create(
                user=request.user, course=course, amount=final_amount,
                coupon=applied_coupon, discount_amount=discount_amount, method=Order.Method.PAYSTACK,
            )
            callback_url = request.build_absolute_uri(reverse('paystack_callback'))
            try:
                data = paystack.initialize_transaction(
                    email=request.user.email or request.user.username,
                    amount_naira=final_amount,
                    reference=order.reference,
                    callback_url=callback_url,
                    metadata={'order_id': order.id, 'course_slug': course.slug},
                )
            except paystack.PaystackError as exc:
                order.status = Order.Status.FAILED
                order.save(update_fields=['status', 'updated_at'])
                messages.error(request, str(exc))
                return redirect('checkout', slug=slug)

            order.paystack_authorization_url = data['authorization_url']
            order.save(update_fields=['paystack_authorization_url', 'updated_at'])
            request.session.pop(session_key, None)
            return redirect(data['authorization_url'])

        elif method == 'bank_transfer':
            if bank_form.is_valid():
                order = bank_form.save(commit=False)
                order.user = request.user
                order.course = course
                order.amount = final_amount
                order.coupon = applied_coupon
                order.discount_amount = discount_amount
                order.method = Order.Method.BANK_TRANSFER
                order.status = Order.Status.AWAITING_REVIEW
                order.save()
                request.session.pop(session_key, None)
                emails.send_bank_transfer_received_email(order)
                messages.success(
                    request,
                    "Thanks! We've received your payment proof and will confirm your enrollment within 24 hours."
                )
                return redirect('my_orders')
        else:
            messages.error(request, "Please choose a payment method.")

    context = {
        'view_name': 'Checkout',
        'course': course,
        'bank_details': settings.BANK_TRANSFER_DETAILS,
        'bank_form': bank_form,
        'coupon_form': coupon_form,
        'applied_coupon': applied_coupon,
        'final_amount': final_amount,
        'discount_amount': discount_amount,
        'paystack_configured': bool(settings.PAYSTACK_SECRET_KEY),
    }
    return render(request, 'checkout.html', context)


@login_required
def paystack_callback(request):
    reference = request.GET.get('reference') or request.GET.get('trxref')
    if not reference:
        messages.error(request, "Missing payment reference.")
        return redirect('dashboard')

    order = get_object_or_404(Order, reference=reference, user=request.user)

    if order.status == Order.Status.PAID:
        messages.success(request, f"You're enrolled in {order.course.title}!")
        return redirect('dashboard')

    try:
        data = paystack.verify_transaction(reference)
    except paystack.PaystackError as exc:
        messages.error(request, str(exc))
        return redirect('checkout', slug=order.course.slug)

    if data.get('status') == 'success' and int(data.get('amount', 0)) == int(order.amount * 100):
        order.mark_paid()
        messages.success(request, f"Payment confirmed — you're enrolled in {order.course.title}!")
        return redirect('dashboard')

    order.status = Order.Status.FAILED
    order.save(update_fields=['status', 'updated_at'])
    messages.error(request, "Payment was not successful. Please try again.")
    return redirect('checkout', slug=order.course.slug)


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    Server-to-server confirmation from Paystack. This is the source of
    truth for marking orders paid — the browser callback view above is
    just a nicer UX for the user and is not trusted on its own for
    anything security sensitive beyond re-verifying with Paystack.
    """
    signature = request.headers.get('x-paystack-signature', '')
    secret = settings.PAYSTACK_SECRET_KEY.encode('utf-8')
    expected = hmac.new(secret, request.body, hashlib.sha512).hexdigest()
    if not secret or not hmac.compare_digest(expected, signature):
        logger.warning("Paystack webhook signature mismatch")
        return HttpResponseForbidden()

    try:
        event = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    if event.get('event') == 'charge.success':
        data = event.get('data', {})
        reference = data.get('reference')
        try:
            order = Order.objects.get(reference=reference)
        except Order.DoesNotExist:
            logger.warning("Webhook for unknown order reference: %s", reference)
            return HttpResponse(status=200)

        if order.status != Order.Status.PAID and int(data.get('amount', 0)) == int(order.amount * 100):
            order.mark_paid()

    return HttpResponse(status=200)


@login_required
def my_orders(request):
    orders = Order.objects.filter(user=request.user).select_related('course')
    return render(request, 'orders.html', {'view_name': 'My Orders', 'orders': orders})


@login_required
def course_learn(request, slug):
    course = get_object_or_404(Course.objects.prefetch_related('lessons'), slug=slug, is_published=True)
    enrollment = get_object_or_404(Enrollment, user=request.user, course=course)

    lessons = list(course.lessons.all())
    completed_ids = set(
        enrollment.lesson_progress.filter(completed_at__isnull=False).values_list('lesson_id', flat=True)
    )
    for lesson in lessons:
        lesson.is_complete = lesson.id in completed_ids

    context = {
        'view_name': course.title,
        'course': course,
        'enrollment': enrollment,
        'lessons': lessons,
    }
    return render(request, 'course_learn.html', context)


@login_required
@require_POST
def toggle_lesson(request, slug, lesson_id):
    course = get_object_or_404(Course, slug=slug, is_published=True)
    enrollment = get_object_or_404(Enrollment, user=request.user, course=course)
    lesson = get_object_or_404(course.lessons, id=lesson_id)

    progress, _ = enrollment.lesson_progress.get_or_create(lesson=lesson)
    if progress.completed_at:
        progress.completed_at = None
    else:
        progress.completed_at = timezone.now()
    progress.save(update_fields=['completed_at'])
    enrollment.recompute_progress()

    return redirect('course_learn', slug=slug)


def about(request):
    stats = Course.objects.filter(is_published=True).aggregate(total_courses=Count('id'))
    context = {
        'view_name': 'About',
        'total_courses': stats['total_courses'] or 0,
        'total_categories': Category.objects.count(),
    }
    return render(request, 'about.html', context)


@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        emails.send_contact_form_submission(
            name=form.cleaned_data['name'],
            email=form.cleaned_data['email'],
            subject=form.cleaned_data['subject'],
            message=form.cleaned_data['message'],
        )
        messages.success(request, "Thanks for reaching out — we'll get back to you within 1-2 business days.")
        return redirect('contact')
    return render(request, 'contact.html', {'view_name': 'Contact', 'form': form})


def terms(request):
    return render(request, 'terms.html', {'view_name': 'Terms of Service'})


def privacy(request):
    return render(request, 'privacy.html', {'view_name': 'Privacy Policy'})


def faq(request):
    return render(request, 'faq.html', {'view_name': 'FAQ'})


@login_required
def account_settings(request):
    profile_form = AccountUpdateForm(user=request.user)
    password_form = StyledPasswordChangeForm(user=request.user)

    if request.method == 'POST':
        if request.POST.get('form') == 'profile':
            profile_form = AccountUpdateForm(request.POST, user=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profile updated.")
                return redirect('account_settings')
        elif request.POST.get('form') == 'password':
            password_form = StyledPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # keep the user logged in
                messages.success(request, "Password changed successfully.")
                return redirect('account_settings')

    return render(request, 'account_settings.html', {
        'view_name': 'Account Settings',
        'profile_form': profile_form,
        'password_form': password_form,
    })


def health_check(request):
    return JsonResponse({'status': 'ok'})


def catch_all(request, path):
    return render(request, '404.html', status=404)
