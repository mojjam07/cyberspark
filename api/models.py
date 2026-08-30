import uuid

from django.conf import settings
from django.db import models


class Instructor(models.Model):
    """
    Optional richer profile for an instructor. Course.instructor stays a
    plain text display name (unchanged, zero migration risk to existing
    data) — this is an opt-in link admins can set to give an instructor
    a bio page and a listing of everything they teach.
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=150, blank=True, help_text="e.g. 'Senior Backend Engineer at X'")
    bio = models.TextField(blank=True)
    photo_url = models.URLField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Course(models.Model):
    LEVEL_CHOICES = [
        ('beginner', 'Beginner'),
        ('intermediate', 'Intermediate'),
        ('advanced', 'Advanced'),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    instructor = models.CharField(max_length=100)
    instructor_profile = models.ForeignKey(
        'Instructor', on_delete=models.SET_NULL, null=True, blank=True, related_name='courses_taught',
        help_text="Optional — link to a richer instructor bio page. The 'instructor' text field above still controls what's displayed by default."
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price in Naira (NGN). Ignored if 'Is free' is checked.")
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    rating_count = models.IntegerField(default=0)
    duration_hours = models.IntegerField()
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='courses')
    thumbnail = models.URLField(blank=True)
    is_free = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def display_price(self):
        return "FREE" if self.is_free else f"NGN {self.price:,.0f}"

    def recompute_rating(self):
        from django.db.models import Avg, Count

        stats = self.reviews.aggregate(avg=Avg('rating'), count=Count('id'))
        self.rating = round(stats['avg'] or 0, 2)
        self.rating_count = stats['count'] or 0
        self.save(update_fields=['rating', 'rating_count'])


class Enrollment(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='enrollments')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='enrollments')
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_hours_spent = models.IntegerField(default=0)

    class Meta:
        unique_together = ('user', 'course')
        ordering = ["-enrolled_at"]

    def __str__(self):
        return f"{self.user.username} - {self.course.title}"

    def recompute_progress(self):
        from django.utils import timezone

        total = self.course.lessons.count()
        if total == 0:
            return
        completed = self.lesson_progress.filter(completed_at__isnull=False).count()
        self.progress = round((completed / total) * 100, 2)
        if completed == total and not self.completed_at:
            self.completed_at = timezone.now()
        elif completed < total:
            self.completed_at = None
        self.save(update_fields=['progress', 'completed_at'])


class Order(models.Model):
    """
    Tracks a purchase attempt for a paid course, regardless of payment
    method. An Enrollment is only created once the Order is marked PAID
    (automatically for Paystack, manually by an admin for bank transfer).
    """

    class Method(models.TextChoices):
        PAYSTACK = 'paystack', 'Paystack (Card/Bank)'
        BANK_TRANSFER = 'bank_transfer', 'Direct Bank Transfer'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending Payment'
        AWAITING_REVIEW = 'awaiting_review', 'Awaiting Manual Review'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    reference = models.CharField(max_length=64, unique=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='orders')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='orders')
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Final amount charged, after any discount")
    coupon = models.ForeignKey('Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    method = models.CharField(max_length=20, choices=Method.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # Bank transfer specific
    proof_of_payment = models.FileField(upload_to='payment_proofs/%Y/%m/', null=True, blank=True)
    payer_note = models.CharField(max_length=255, blank=True, help_text="e.g. sender name / bank used, to help matching")

    # Paystack specific
    paystack_authorization_url = models.URLField(blank=True)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_orders'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.reference} - {self.user.username} - {self.course.title} - {self.status}"

    def mark_paid(self):
        from django.utils import timezone

        self.status = self.Status.PAID
        self.paid_at = timezone.now()
        self.save(update_fields=['status', 'paid_at', 'updated_at'])
        Enrollment.objects.get_or_create(user=self.user, course=self.course)

        if self.coupon:
            Coupon.objects.filter(pk=self.coupon_id).update(times_used=models.F('times_used') + 1)

        from . import emails
        emails.send_payment_confirmation_email(self)


class Lesson(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='lessons')
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)
    video_url = models.URLField(blank=True, help_text="Optional embeddable video URL")
    content = models.TextField(blank=True, help_text="Lesson notes / written content")
    duration_minutes = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ["course", "order", "id"]

    def __str__(self):
        return f"{self.course.title} - {self.order}. {self.title}"


class LessonProgress(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name='lesson_progress')
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='progress_records')
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('enrollment', 'lesson')

    def __str__(self):
        return f"{self.enrollment} - {self.lesson.title}"


class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='wishlist_items')
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='wishlisted_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'course')
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} wishlisted {self.course.title}"


class Review(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(help_text="1-5")
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('course', 'user')
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} rated {self.course.title}: {self.rating}/5"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.course.recompute_rating()

    def delete(self, *args, **kwargs):
        course = self.course
        super().delete(*args, **kwargs)
        course.recompute_rating()


class Coupon(models.Model):
    code = models.CharField(max_length=32, unique=True)
    discount_percent = models.PositiveSmallIntegerField(help_text="e.g. 20 for 20% off")
    active = models.BooleanField(default=True)
    valid_until = models.DateTimeField(null=True, blank=True, help_text="Leave blank for no expiry")
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for unlimited")
    times_used = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} (-{self.discount_percent}%)"

    def is_valid(self):
        from django.utils import timezone

        if not self.active:
            return False
        if self.valid_until and timezone.now() > self.valid_until:
            return False
        if self.max_uses is not None and self.times_used >= self.max_uses:
            return False
        return True

    def apply_to(self, amount):
        from decimal import Decimal

        discount = (amount * Decimal(self.discount_percent) / Decimal(100)).quantize(Decimal('0.01'))
        return amount - discount
