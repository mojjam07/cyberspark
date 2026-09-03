from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.utils import timezone

from .models import (
    Category, Coupon, Course, Enrollment, Instructor, Lesson, LessonProgress, Order, Review, Wishlist,
)

User = get_user_model()


def get_dashboard_stats():
    paid_orders = Order.objects.filter(status=Order.Status.PAID)
    pending_reviews = Order.objects.filter(status=Order.Status.AWAITING_REVIEW)
    return {
        'total_users': User.objects.count(),
        'total_courses': Course.objects.count(),
        'total_enrollments': Enrollment.objects.count(),
        'pending_reviews': pending_reviews.count(),
        'gross_revenue': paid_orders.aggregate(total=Sum('amount'))['total'] or 0,
        'recent_orders': Order.objects.select_related('user', 'course').order_by('-created_at')[:5],
        'recent_pending_reviews': pending_reviews.select_related('user', 'course').order_by('-created_at')[:5],
    }


original_each_context = admin.site.each_context

def custom_each_context(request):
    context = original_each_context(request)
    context['dashboard_stats'] = get_dashboard_stats()
    return context


admin.site.each_context = custom_each_context
admin.site.index_template = 'admin/custom_index.html'

admin.site.site_title = 'CyberSpark Admin'
admin.site.site_header = 'CyberSpark Enroll Administration'
admin.site.index_title = 'Management'


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 1
    fields = ('order', 'title', 'video_url', 'duration_minutes', 'content')


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'instructor', 'display_price', 'rating', 'category', 'is_published', 'is_featured')
    list_filter = ('category', 'is_free', 'is_published', 'is_featured', 'level')
    search_fields = ('title', 'instructor')
    prepopulated_fields = {'slug': ('title',)}
    inlines = [LessonInline]


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'progress', 'total_hours_spent', 'enrolled_at')
    list_filter = ('course__category',)
    search_fields = ('user__username', 'course__title')


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'created_at')
    search_fields = ('user__username', 'course__title')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('course', 'user', 'rating', 'created_at')
    list_filter = ('rating',)
    search_fields = ('user__username', 'course__title', 'comment')


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'discount_percent', 'active', 'times_used', 'max_uses', 'valid_until')
    list_filter = ('active',)
    search_fields = ('code',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('reference', 'user', 'course', 'amount', 'method', 'status', 'created_at')
    list_filter = ('method', 'status')
    search_fields = ('reference', 'user__username', 'course__title', 'payer_note')
    readonly_fields = ('reference', 'created_at', 'updated_at', 'paid_at')
    actions = ['approve_bank_transfer', 'reject_order']

    def get_queryset(self, request):
        from django.db.models import Case, When, IntegerField

        qs = super().get_queryset(request)
        # Surface orders awaiting manual review at the very top so staff
        # see what needs action first, newest-first within each group.
        return qs.annotate(
            _priority=Case(
                When(status=Order.Status.AWAITING_REVIEW, then=0),
                default=1,
                output_field=IntegerField(),
            )
        ).order_by('_priority', '-created_at')

    @admin.action(description="Approve selected bank-transfer orders (enrolls the user)")
    def approve_bank_transfer(self, request, queryset):
        count = 0
        for order in queryset.filter(method=Order.Method.BANK_TRANSFER):
            if order.status != Order.Status.PAID:
                order.reviewed_by = request.user
                order.save(update_fields=['reviewed_by', 'updated_at'])
                order.mark_paid()
                count += 1
        self.message_user(request, f"Approved {count} order(s) and enrolled the corresponding users.")

    @admin.action(description="Reject selected orders")
    def reject_order(self, request, queryset):
        updated = queryset.exclude(status=Order.Status.PAID).update(
            status=Order.Status.FAILED, reviewed_by=request.user, updated_at=timezone.now()
        )
        self.message_user(request, f"Rejected {updated} order(s).")
