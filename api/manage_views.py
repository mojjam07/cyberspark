"""
Staff admin dashboard (/manage/): a friendlier, branded way for staff to
populate and manage data than the raw Django admin (which stays at /admin/).
Every view here requires an authenticated user with is_staff=True.
"""

import csv
import io
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from . import emails
from .manage_forms import (
    CategoryAdminForm, CouponAdminForm, CourseAdminForm, InstructorAdminForm, LessonAdminForm, ManualEnrollForm,
)
from .models import Category, Coupon, Course, Enrollment, Instructor, Lesson, Order

PER_PAGE = 20
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 500


def staff_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(request, "You don't have access to the admin area.")
            return redirect('dashboard')
        return view(request, *args, **kwargs)
    return wrapper


def _ctx(active, **extra):
    return {
        'active_section': active,
        'pending_count': Order.objects.filter(status=Order.Status.AWAITING_REVIEW).count(),
        **extra,
    }


def _paginate(request, qs, extra_params=None):
    page = Paginator(qs, PER_PAGE).get_page(request.GET.get('page'))
    params = {k: v for k, v in (extra_params or {}).items() if v}
    return page, ('&' + urlencode(params)) if params else ''


def _safe_next(request, default):
    nxt = request.POST.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return default


# ---------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------

@staff_required
def manage_home(request):
    now = timezone.now()
    paid = Order.objects.filter(status=Order.Status.PAID)
    revenue_all = paid.aggregate(t=Sum('amount'))['t'] or 0
    revenue_30 = paid.filter(paid_at__gte=now - timedelta(days=30)).aggregate(t=Sum('amount'))['t'] or 0
    courses_total = Course.objects.count()
    pending = Order.objects.filter(status=Order.Status.AWAITING_REVIEW).count()

    stats = [
        {'label': 'Revenue (all time)', 'value': f"NGN {revenue_all:,.0f}", 'desc': f"{paid.count()} paid orders"},
        {'label': 'Revenue (30 days)', 'value': f"NGN {revenue_30:,.0f}", 'desc': 'Paid orders only'},
        {'label': 'Learners', 'value': User.objects.filter(is_staff=False).count(), 'desc': 'Registered accounts'},
        {'label': 'Enrollments', 'value': Enrollment.objects.count(), 'desc': 'All courses'},
        {'label': 'Courses', 'value': Course.objects.filter(is_published=True).count(), 'desc': f"{courses_total} total incl. drafts"},
        {'label': 'Awaiting review', 'value': pending, 'desc': 'Bank transfers to verify'},
    ]
    context = _ctx(
        'home',
        view_name='Admin Dashboard',
        stats=stats,
        awaiting=Order.objects.filter(status=Order.Status.AWAITING_REVIEW).select_related('user', 'course')[:5],
        recent_orders=Order.objects.select_related('user', 'course')[:6],
        recent_users=User.objects.order_by('-date_joined')[:6],
        top_courses=Course.objects.annotate(n=Count('enrollments')).order_by('-n', 'title')[:5],
        needs_setup=(courses_total == 0),
    )
    return render(request, 'manage_home.html', context)


# ---------------------------------------------------------------------
# Generic CRUD for courses / categories / instructors / lessons / coupons
# ---------------------------------------------------------------------

def _fmt_dt(o):
    return o.valid_until.strftime('%b %d, %Y %H:%M') if o.valid_until else 'No expiry'


RESOURCES = {
    'courses': {
        'model': Course, 'form': CourseAdminForm, 'label': 'Course', 'plural': 'Courses',
        'columns': [
            ('Title', 'title'), ('Category', 'category'), ('Instructor', 'instructor'),
            ('Price', 'display_price'), ('Published', 'is_published'), ('Featured', 'is_featured'),
        ],
        'search': ['title', 'instructor', 'category__name'],
        'ordering': ['-created_at'], 'select': ['category'],
        'row_links': lambda o: [
            ('Lessons', f"{reverse('manage_list', args=['lessons'])}?course={o.pk}"),
            ('View', reverse('course_detail', args=[o.slug])),
        ],
        'empty': "No courses yet. Add one, or bulk-import many from a CSV.",
    },
    'categories': {
        'model': Category, 'form': CategoryAdminForm, 'label': 'Category', 'plural': 'Categories',
        'columns': [('Name', 'name'), ('Slug', 'slug'), ('Courses', lambda o: o.courses.count())],
        'search': ['name', 'slug'], 'ordering': ['name'],
        'empty': "No categories yet. Courses need a category — add one first.",
    },
    'instructors': {
        'model': Instructor, 'form': InstructorAdminForm, 'label': 'Instructor', 'plural': 'Instructors',
        'columns': [('Name', 'name'), ('Title', 'title'), ('Courses', lambda o: o.courses_taught.count())],
        'search': ['name', 'title'], 'ordering': ['name'],
        'row_links': lambda o: [('View', reverse('instructor_detail', args=[o.slug]))],
        'empty': "No instructor profiles yet (optional — courses work without them).",
    },
    'lessons': {
        'model': Lesson, 'form': LessonAdminForm, 'label': 'Lesson', 'plural': 'Lessons',
        'columns': [
            ('Course', 'course'), ('#', 'order'), ('Title', 'title'),
            ('Minutes', 'duration_minutes'), ('Video', lambda o: '✓' if o.video_url else '—'),
        ],
        'search': ['title', 'course__title'],
        'ordering': ['course__title', 'order', 'id'], 'select': ['course'],
        'filters': {'course': 'course_id'},
        'empty': "No lessons yet. Lessons are what learners tick off to track progress.",
    },
    'coupons': {
        'model': Coupon, 'form': CouponAdminForm, 'label': 'Coupon', 'plural': 'Coupons',
        'columns': [
            ('Code', 'code'), ('Discount', lambda o: f"{o.discount_percent}%"), ('Active', 'active'),
            ('Used', lambda o: f"{o.times_used}/{o.max_uses}" if o.max_uses else str(o.times_used)),
            ('Expires', _fmt_dt),
        ],
        'search': ['code'], 'ordering': ['-created_at'],
        'empty': "No coupons yet.",
    },
}


def _resource(name):
    cfg = RESOURCES.get(name)
    if not cfg:
        raise Http404("Unknown resource")
    return cfg


def _cell(obj, spec):
    value = spec(obj) if callable(spec) else getattr(obj, spec)
    if callable(value):
        value = value()
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return '✓' if value else '—'
    if hasattr(value, 'strftime'):
        return value.strftime('%b %d, %Y %H:%M')
    return str(value)


@staff_required
def manage_list(request, resource):
    cfg = _resource(resource)
    qs = cfg['model'].objects.all()
    if cfg.get('select'):
        qs = qs.select_related(*cfg['select'])

    q = request.GET.get('q', '').strip()
    if q:
        cond = Q()
        for field in cfg['search']:
            cond |= Q(**{f'{field}__icontains': q})
        qs = qs.filter(cond)

    active_filter = None
    for param, orm_field in cfg.get('filters', {}).items():
        raw = request.GET.get(param, '')
        if raw.isdigit():
            qs = qs.filter(**{orm_field: int(raw)})
            if param == 'course':
                active_filter = Course.objects.filter(pk=int(raw)).first()

    qs = qs.order_by(*cfg['ordering'])
    extra = {'q': q, **{p: request.GET.get(p, '') for p in cfg.get('filters', {})}}
    page, extra_qs = _paginate(request, qs, extra)

    link_fn = cfg.get('row_links')
    rows = [{
        'pk': o.pk,
        'cells': [_cell(o, spec) for _, spec in cfg['columns']],
        'links': link_fn(o) if link_fn else [],
    } for o in page]

    add_url = reverse('manage_create', args=[resource])
    if active_filter:
        add_url += f"?course={active_filter.pk}"

    return render(request, 'manage_list.html', _ctx(
        resource, view_name=f"Manage {cfg['plural']}", cfg=cfg, resource=resource,
        labels=[label for label, _ in cfg['columns']], rows=rows, page=page, extra_qs=extra_qs,
        query=q, active_filter=active_filter, add_url=add_url,
    ))


@staff_required
def manage_form(request, resource, pk=None):
    cfg = _resource(resource)
    instance = get_object_or_404(cfg['model'], pk=pk) if pk else None
    initial = {} if instance else {k: v for k, v in request.GET.items() if k in cfg['form'].base_fields}
    form = cfg['form'](request.POST or None, instance=instance, initial=initial)
    if request.method == 'POST' and form.is_valid():
        obj = form.save()
        messages.success(request, f"{cfg['label']} \"{obj}\" saved.")
        if resource == 'lessons':
            return redirect(f"{reverse('manage_list', args=[resource])}?course={obj.course_id}")
        return redirect('manage_list', resource=resource)
    return render(request, 'manage_form.html', _ctx(
        resource, view_name=f"{'Edit' if instance else 'Add'} {cfg['label']}",
        cfg=cfg, resource=resource, form=form, instance=instance,
    ))


def _delete_blockers(resource, obj):
    if resource == 'courses':
        n_enr, n_ord = obj.enrollments.count(), obj.orders.count()
        if n_enr or n_ord:
            return [
                f"This course has {n_enr} enrollment(s) and {n_ord} order(s). Deleting it would erase "
                "learners' progress and payment records. Untick “Published” to hide it instead."
            ]
    if resource == 'categories' and obj.courses.exists():
        return [f"This category still contains {obj.courses.count()} course(s). Move or delete them first."]
    return []


@staff_required
def manage_delete(request, resource, pk):
    cfg = _resource(resource)
    obj = get_object_or_404(cfg['model'], pk=pk)
    blockers = _delete_blockers(resource, obj)
    if request.method == 'POST':
        if blockers:
            messages.error(request, blockers[0])
        else:
            name = str(obj)
            obj.delete()
            messages.success(request, f"{cfg['label']} \"{name}\" deleted.")
        return redirect('manage_list', resource=resource)
    return render(request, 'manage_confirm_delete.html', _ctx(
        resource, view_name=f"Delete {cfg['label']}", cfg=cfg, resource=resource, obj=obj, blockers=blockers,
    ))


# ---------------------------------------------------------------------
# Orders (review bank transfers, see everything that was paid)
# ---------------------------------------------------------------------

@staff_required
def manage_orders(request):
    status = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    qs = Order.objects.select_related('user', 'course')
    if status in {c[0] for c in Order.Status.choices}:
        qs = qs.filter(status=status)
    else:
        status = ''
    if q:
        qs = qs.filter(
            Q(reference__icontains=q) | Q(user__email__icontains=q) | Q(user__username__icontains=q)
            | Q(course__title__icontains=q) | Q(payer_note__icontains=q)
        )
    counts = dict(Order.objects.values_list('status').annotate(n=Count('id')))
    tabs = [{'value': '', 'label': 'All', 'n': sum(counts.values())}] + [
        {'value': v, 'label': lbl, 'n': counts.get(v, 0)} for v, lbl in Order.Status.choices
    ]
    page, extra_qs = _paginate(request, qs, {'status': status, 'q': q})
    return render(request, 'manage_orders.html', _ctx(
        'orders', view_name='Orders', page=page, extra_qs=extra_qs, tabs=tabs, status=status, query=q,
        awaiting_value=Order.Status.AWAITING_REVIEW.value, pending_value=Order.Status.PENDING.value,
    ))


@staff_required
@require_POST
def manage_order_action(request, pk):
    order = get_object_or_404(Order, pk=pk)
    action = request.POST.get('action')
    if action == 'approve':
        if order.status == Order.Status.PAID:
            messages.info(request, "That order is already marked paid.")
        elif order.method != Order.Method.BANK_TRANSFER:
            messages.error(request, "Paystack orders are confirmed automatically by Paystack — they can't be approved by hand.")
        else:
            order.reviewed_by = request.user
            order.save(update_fields=['reviewed_by', 'updated_at'])
            order.mark_paid()
            messages.success(request, f"Approved — {order.user.username} is now enrolled in {order.course.title}.")
    elif action == 'reject':
        if order.status == Order.Status.PAID:
            messages.error(request, "A paid order can't be rejected.")
        else:
            order.status = Order.Status.FAILED
            order.reviewed_by = request.user
            order.save(update_fields=['status', 'reviewed_by', 'updated_at'])
            messages.success(request, "Order rejected.")
    else:
        messages.error(request, "Unknown action.")
    return redirect(_safe_next(request, reverse('manage_orders')))


@staff_required
def manage_order_proof(request, pk):
    """Staff-only viewer for bank-transfer proofs (they are never served publicly)."""
    order = get_object_or_404(Order, pk=pk)
    if not order.proof_of_payment:
        raise Http404("No proof uploaded")
    try:
        return FileResponse(open(order.proof_of_payment.path, 'rb'))
    except NotImplementedError:
        # Remote storage (e.g. Cloudinary) — send staff to the stored URL
        return redirect(order.proof_of_payment.url)
    except FileNotFoundError:
        raise Http404("The uploaded file is no longer on disk")


# ---------------------------------------------------------------------
# Enrollments & users
# ---------------------------------------------------------------------

@staff_required
def manage_enrollments(request):
    form = ManualEnrollForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        enrollment, created = Enrollment.objects.get_or_create(user=form.user, course=form.cleaned_data['course'])
        if created:
            emails.send_enrollment_email(enrollment)
            messages.success(request, f"Enrolled {form.user.username} in {enrollment.course.title}.")
        else:
            messages.info(request, "That learner is already enrolled in this course.")
        return redirect('manage_enrollments')

    q = request.GET.get('q', '').strip()
    qs = Enrollment.objects.select_related('user', 'course')
    if q:
        qs = qs.filter(Q(user__email__icontains=q) | Q(user__username__icontains=q) | Q(course__title__icontains=q))
    page, extra_qs = _paginate(request, qs, {'q': q})
    return render(request, 'manage_enrollments.html', _ctx(
        'enrollments', view_name='Enrollments', form=form, page=page, extra_qs=extra_qs, query=q,
    ))


@staff_required
@require_POST
def manage_enrollment_remove(request, pk):
    enrollment = get_object_or_404(Enrollment.objects.select_related('user', 'course'), pk=pk)
    label = f"{enrollment.user.username} from {enrollment.course.title}"
    enrollment.delete()
    messages.success(request, f"Removed {label}.")
    return redirect(_safe_next(request, reverse('manage_enrollments')))


@staff_required
def manage_users(request):
    q = request.GET.get('q', '').strip()
    qs = User.objects.annotate(
        n_enrollments=Count('enrollments', distinct=True), n_orders=Count('orders', distinct=True),
    ).order_by('-date_joined')
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    page, extra_qs = _paginate(request, qs, {'q': q})
    return render(request, 'manage_users.html', _ctx('users', view_name='Users', page=page, extra_qs=extra_qs, query=q))


# ---------------------------------------------------------------------
# Bulk CSV import
# ---------------------------------------------------------------------

COURSE_COLUMNS = [
    'title', 'slug', 'category', 'instructor', 'description', 'price', 'duration_hours',
    'level', 'is_free', 'is_featured', 'is_published', 'thumbnail',
]
COURSE_REQUIRED = ['title', 'category', 'instructor', 'description', 'duration_hours']
COURSE_SAMPLE = [
    'Intro to SQL', '', 'Data Science', 'Ada Obi', 'Learn SQL from scratch with real datasets.',
    '35000', '8', 'beginner', 'no', 'no', 'yes', '',
]
LESSON_COLUMNS = ['course_slug', 'order', 'title', 'duration_minutes', 'video_url', 'content']
LESSON_REQUIRED = ['course_slug', 'order', 'title']
LESSON_SAMPLE = ['intro-to-sql', '1', 'What is a database?', '12', '', 'Tables, rows and columns.']
TRUE_VALUES = {'1', 'true', 'yes', 'y', 'on', 't'}


def _truthy(value):
    return str(value).strip().lower() in TRUE_VALUES


def _form_errors(form):
    return "; ".join(
        f"{('' if k == '__all__' else k + ': ')}{' '.join(v)}" for k, v in form.errors.items()
    )


def _read_csv(upload, required):
    """Returns (rows, error). rows = list of (line_number, dict)."""
    if not upload:
        return None, "Choose a CSV file first."
    if not upload.name.lower().endswith('.csv'):
        return None, "That doesn't look like a .csv file."
    if upload.size > MAX_IMPORT_BYTES:
        return None, "File is too large (2 MB max)."
    try:
        text = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return None, "Couldn't read the file — save it as CSV (UTF-8) and try again."
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return None, "The file is empty."
    reader.fieldnames = [(f or '').strip().lower() for f in reader.fieldnames]
    missing = [c for c in required if c not in reader.fieldnames]
    if missing:
        return None, f"Missing required column(s): {', '.join(missing)}."
    rows = []
    for line, raw in enumerate(reader, start=2):
        row = {k: (v or '').strip() for k, v in raw.items() if k}
        if any(row.values()):
            rows.append((line, row))
    if not rows:
        return None, "No data rows found under the header."
    if len(rows) > MAX_IMPORT_ROWS:
        return None, f"Too many rows ({len(rows)}). Import at most {MAX_IMPORT_ROWS} at a time."
    return rows, None


def _import_courses(rows):
    created = updated = 0
    errors = []
    with transaction.atomic():
        for line, row in rows:
            cat_slug = slugify(row.get('category', ''))
            if not cat_slug:
                errors.append(f"Row {line}: category is required.")
                continue
            category = Category.objects.filter(slug=cat_slug).first() or Category.objects.create(
                name=row['category'], slug=cat_slug
            )
            instance = Course.objects.filter(slug=row['slug']).first() if row.get('slug') else None
            data = {
                'title': row.get('title', ''), 'slug': row.get('slug', ''), 'category': category.pk,
                'instructor': row.get('instructor', ''), 'description': row.get('description', ''),
                'price': row.get('price', ''), 'duration_hours': row.get('duration_hours', ''),
                'level': (row.get('level') or 'beginner').lower(), 'thumbnail': row.get('thumbnail', ''),
            }
            for flag in ('is_free', 'is_featured'):
                if _truthy(row.get(flag, '')):
                    data[flag] = 'on'
            published = row.get('is_published', '')
            if published == '' or _truthy(published):
                data['is_published'] = 'on'
            form = CourseAdminForm(data, instance=instance)
            if form.is_valid():
                form.save()
                if instance:
                    updated += 1
                else:
                    created += 1
            else:
                errors.append(f"Row {line}: {_form_errors(form)}")
        if errors:
            transaction.set_rollback(True)
    return (0, 0, errors) if errors else (created, updated, [])


def _import_lessons(rows):
    created = updated = 0
    errors = []
    with transaction.atomic():
        for line, row in rows:
            course = Course.objects.filter(slug=row.get('course_slug', '')).first()
            if not course:
                errors.append(f"Row {line}: no course with slug \"{row.get('course_slug', '')}\".")
                continue
            try:
                order = int(row.get('order', ''))
            except ValueError:
                errors.append(f"Row {line}: order must be a whole number.")
                continue
            instance = Lesson.objects.filter(course=course, order=order).first()
            data = {
                'course': course.pk, 'order': order, 'title': row.get('title', ''),
                'duration_minutes': row.get('duration_minutes') or 10,
                'video_url': row.get('video_url', ''), 'content': row.get('content', ''),
            }
            form = LessonAdminForm(data, instance=instance)
            if form.is_valid():
                form.save()
                if instance:
                    updated += 1
                else:
                    created += 1
            else:
                errors.append(f"Row {line}: {_form_errors(form)}")
        if errors:
            transaction.set_rollback(True)
    return (0, 0, errors) if errors else (created, updated, [])


@staff_required
def manage_import(request):
    result = None
    if request.method == 'POST':
        kind = request.POST.get('kind')
        if kind not in ('courses', 'lessons'):
            messages.error(request, "Unknown import type.")
            return redirect('manage_import')
        required = COURSE_REQUIRED if kind == 'courses' else LESSON_REQUIRED
        rows, error = _read_csv(request.FILES.get('file'), required)
        if error:
            result = {'kind': kind, 'created': 0, 'updated': 0, 'errors': [error]}
        else:
            created, updated, errors = (_import_courses if kind == 'courses' else _import_lessons)(rows)
            result = {'kind': kind, 'created': created, 'updated': updated, 'errors': errors}
            if not errors:
                messages.success(request, f"Import finished: {created} created, {updated} updated.")
    return render(request, 'manage_import.html', _ctx(
        'import', view_name='Bulk Import', result=result,
        course_columns=COURSE_COLUMNS, course_required=COURSE_REQUIRED,
        lesson_columns=LESSON_COLUMNS, lesson_required=LESSON_REQUIRED,
    ))


@staff_required
def manage_import_template(request, kind):
    if kind == 'courses':
        header, sample = COURSE_COLUMNS, COURSE_SAMPLE
    elif kind == 'lessons':
        header, sample = LESSON_COLUMNS, LESSON_SAMPLE
    else:
        raise Http404
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="cyberspark-{kind}-template.csv"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerow(sample)
    return response
