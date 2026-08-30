from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Category, Coupon, Course, Enrollment, Instructor, Lesson, Order, Review, Wishlist


class ModelTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.free_course = Course.objects.create(
            title='Free Course', slug='free-course', description='...', instructor='A',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        self.paid_course = Course.objects.create(
            title='Paid Course', slug='paid-course', description='...', instructor='B',
            price=Decimal('50000.00'), duration_hours=5, level='beginner', category=self.category,
        )

    def test_display_price(self):
        self.assertEqual(self.free_course.display_price, 'FREE')
        self.assertIn('50,000', self.paid_course.display_price)

    def test_order_mark_paid_creates_enrollment(self):
        user = User.objects.create_user(username='u1@example.com', password='pass12345')
        order = Order.objects.create(
            user=user, course=self.paid_course, amount=self.paid_course.price, method=Order.Method.BANK_TRANSFER
        )
        self.assertFalse(Enrollment.objects.filter(user=user, course=self.paid_course).exists())
        order.mark_paid()
        self.assertTrue(Enrollment.objects.filter(user=user, course=self.paid_course).exists())
        self.assertEqual(order.status, Order.Status.PAID)


class AuthFlowTests(TestCase):
    def test_signup_then_login(self):
        resp = self.client.post(reverse('signup'), {
            'full_name': 'Jane Doe', 'email': 'jane@example.com',
            'password1': 'SuperSecret123', 'password2': 'SuperSecret123',
        })
        self.assertRedirects(resp, reverse('login'))
        self.assertTrue(User.objects.filter(username='jane@example.com').exists())

        resp = self.client.post(reverse('login'), {'username': 'jane@example.com', 'password': 'SuperSecret123'})
        self.assertRedirects(resp, reverse('dashboard'))

    def test_signup_rejects_mismatched_passwords(self):
        resp = self.client.post(reverse('signup'), {
            'full_name': 'Jane Doe', 'email': 'jane2@example.com',
            'password1': 'SuperSecret123', 'password2': 'DifferentPass1',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='jane2@example.com').exists())

    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(username='dupe@example.com', password='pass12345')
        resp = self.client.post(reverse('signup'), {
            'full_name': 'Dupe', 'email': 'dupe@example.com',
            'password1': 'SuperSecret123', 'password2': 'SuperSecret123',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(User.objects.filter(username='dupe@example.com').count(), 1)


class EnrollmentFlowTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.free_course = Course.objects.create(
            title='Free Course', slug='free-course', description='...', instructor='A',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        self.paid_course = Course.objects.create(
            title='Paid Course', slug='paid-course', description='...', instructor='B',
            price=Decimal('50000.00'), duration_hours=5, level='beginner', category=self.category,
        )
        self.user = User.objects.create_user(username='learner@example.com', password='pass12345')

    def test_enroll_free_requires_login(self):
        resp = self.client.post(reverse('enroll_free', args=[self.free_course.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_enroll_free_creates_enrollment(self):
        self.client.login(username='learner@example.com', password='pass12345')
        resp = self.client.post(reverse('enroll_free', args=[self.free_course.slug]))
        self.assertRedirects(resp, reverse('dashboard'))
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.free_course).exists())

    def test_checkout_requires_login(self):
        resp = self.client.get(reverse('checkout', args=[self.paid_course.slug]))
        self.assertEqual(resp.status_code, 302)

    def test_bank_transfer_submission_creates_order_awaiting_review(self):
        self.client.login(username='learner@example.com', password='pass12345')
        proof = SimpleUploadedFile('proof.png', b'fake-bytes', content_type='image/png')
        resp = self.client.post(reverse('checkout', args=[self.paid_course.slug]), {
            'method': 'bank_transfer', 'payer_note': 'GTBank', 'proof_of_payment': proof,
        })
        self.assertRedirects(resp, reverse('my_orders'))
        order = Order.objects.get(user=self.user, course=self.paid_course)
        self.assertEqual(order.status, Order.Status.AWAITING_REVIEW)
        self.assertFalse(Enrollment.objects.filter(user=self.user, course=self.paid_course).exists())

    def test_bank_transfer_without_file_shows_error(self):
        self.client.login(username='learner@example.com', password='pass12345')
        resp = self.client.post(reverse('checkout', args=[self.paid_course.slug]), {
            'method': 'bank_transfer', 'payer_note': 'GTBank',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Order.objects.filter(user=self.user, course=self.paid_course).exists())

    @patch('api.paystack.initialize_transaction')
    def test_paystack_checkout_redirects_to_authorization_url(self, mock_init):
        mock_init.return_value = {
            'authorization_url': 'https://checkout.paystack.com/abc123',
            'access_code': 'abc123', 'reference': 'whatever',
        }
        self.client.login(username='learner@example.com', password='pass12345')
        resp = self.client.post(reverse('checkout', args=[self.paid_course.slug]), {'method': 'paystack'})
        self.assertRedirects(resp, 'https://checkout.paystack.com/abc123', fetch_redirect_response=False)
        order = Order.objects.get(user=self.user, course=self.paid_course)
        self.assertEqual(order.method, Order.Method.PAYSTACK)

    @patch('api.paystack.verify_transaction')
    def test_paystack_callback_marks_order_paid(self, mock_verify):
        order = Order.objects.create(
            user=self.user, course=self.paid_course, amount=self.paid_course.price, method=Order.Method.PAYSTACK
        )
        mock_verify.return_value = {'status': 'success', 'amount': int(self.paid_course.price * 100)}
        self.client.login(username='learner@example.com', password='pass12345')
        resp = self.client.get(reverse('paystack_callback') + f'?reference={order.reference}')
        self.assertRedirects(resp, reverse('dashboard'))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.paid_course).exists())


class AdminApprovalTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Paid Course', slug='paid-course', description='...', instructor='B',
            price=Decimal('50000.00'), duration_hours=5, level='beginner', category=self.category,
        )
        self.user = User.objects.create_user(username='learner@example.com', password='pass12345')
        self.admin = User.objects.create_superuser(username='admin@example.com', password='pass12345', email='a@a.com')
        self.order = Order.objects.create(
            user=self.user, course=self.course, amount=self.course.price,
            method=Order.Method.BANK_TRANSFER, status=Order.Status.AWAITING_REVIEW,
        )

    def test_approve_bank_transfer_action_enrolls_user(self):
        self.client.login(username='admin@example.com', password='pass12345')
        resp = self.client.post(reverse('admin:api_order_changelist'), {
            'action': 'approve_bank_transfer', '_selected_action': [str(self.order.pk)], 'index': '0',
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.course).exists())


class PageStatusTests(TestCase):
    def test_home_and_courses_load(self):
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('courses')).status_code, 200)

    def test_unknown_url_returns_404(self):
        resp = self.client.get('/this-does-not-exist/')
        self.assertEqual(resp.status_code, 404)


class LessonProgressTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Free Course', slug='free-course', description='...', instructor='A',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        self.lesson1 = Lesson.objects.create(course=self.course, title='Intro', order=1)
        self.lesson2 = Lesson.objects.create(course=self.course, title='Next steps', order=2)
        self.user = User.objects.create_user(username='learner@example.com', password='pass12345')
        self.client.login(username='learner@example.com', password='pass12345')
        self.client.post(reverse('enroll_free', args=[self.course.slug]))
        self.enrollment = Enrollment.objects.get(user=self.user, course=self.course)

    def test_learn_page_requires_enrollment(self):
        other_course = Course.objects.create(
            title='Other', slug='other', description='...', instructor='B',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        resp = self.client.get(reverse('course_learn', args=[other_course.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_toggle_lesson_updates_progress(self):
        self.assertEqual(self.enrollment.progress, 0)
        self.client.post(reverse('toggle_lesson', args=[self.course.slug, self.lesson1.id]))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress, Decimal('50.00'))
        self.assertIsNone(self.enrollment.completed_at)

        self.client.post(reverse('toggle_lesson', args=[self.course.slug, self.lesson2.id]))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress, Decimal('100.00'))
        self.assertIsNotNone(self.enrollment.completed_at)

        # Untoggle brings it back down
        self.client.post(reverse('toggle_lesson', args=[self.course.slug, self.lesson1.id]))
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.progress, Decimal('50.00'))
        self.assertIsNone(self.enrollment.completed_at)


class EmailNotificationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Free Course', slug='free-course', description='...', instructor='A',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )

    def test_signup_sends_welcome_email(self):
        self.client.post(reverse('signup'), {
            'full_name': 'Jane Doe', 'email': 'jane3@example.com',
            'password1': 'SuperSecret123', 'password2': 'SuperSecret123',
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Welcome', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ['jane3@example.com'])

    def test_free_enrollment_sends_email_once(self):
        User.objects.create_user(username='learner2@example.com', password='pass12345', email='learner2@example.com')
        self.client.login(username='learner2@example.com', password='pass12345')
        self.client.post(reverse('enroll_free', args=[self.course.slug]))
        self.assertEqual(len(mail.outbox), 1)
        # Enrolling again should not send a second email
        self.client.post(reverse('enroll_free', args=[self.course.slug]))
        self.assertEqual(len(mail.outbox), 1)

    def test_bank_transfer_submission_sends_email(self):
        paid_course = Course.objects.create(
            title='Paid', slug='paid', description='...', instructor='B',
            price=Decimal('20000.00'), duration_hours=2, level='beginner', category=self.category,
        )
        User.objects.create_user(username='learner3@example.com', password='pass12345', email='learner3@example.com')
        self.client.login(username='learner3@example.com', password='pass12345')
        proof = SimpleUploadedFile('proof.png', b'fake-bytes', content_type='image/png')
        self.client.post(reverse('checkout', args=[paid_course.slug]), {
            'method': 'bank_transfer', 'payer_note': 'GTBank', 'proof_of_payment': proof,
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('received your bank transfer proof', mail.outbox[0].body)


class NewPagesTests(TestCase):
    def test_static_info_pages_load(self):
        for name in ['about', 'contact', 'terms', 'privacy', 'faq']:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, f"{name} page failed to load")

    def test_contact_form_sends_email(self):
        resp = self.client.post(reverse('contact'), {
            'name': 'Ada Lovelace', 'email': 'ada@example.com',
            'subject': 'Question about refunds', 'message': 'How do refunds work?',
        })
        self.assertRedirects(resp, reverse('contact'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Question about refunds', mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].reply_to, ['ada@example.com'])

    def test_contact_form_honeypot_blocks_submission(self):
        resp = self.client.post(reverse('contact'), {
            'name': 'Bot', 'email': 'bot@example.com', 'subject': 'x', 'message': 'x',
            'website': 'http://spam.example.com',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_account_settings_requires_login(self):
        resp = self.client.get(reverse('account_settings'))
        self.assertEqual(resp.status_code, 302)

    def test_account_settings_updates_profile(self):
        User.objects.create_user(username='acct@example.com', password='pass12345', email='acct@example.com')
        self.client.login(username='acct@example.com', password='pass12345')
        resp = self.client.post(reverse('account_settings'), {'form': 'profile', 'full_name': 'New Name'})
        self.assertRedirects(resp, reverse('account_settings'))
        user = User.objects.get(username='acct@example.com')
        self.assertEqual(user.first_name, 'New')
        self.assertEqual(user.last_name, 'Name')

    def test_account_settings_changes_password(self):
        User.objects.create_user(username='acct2@example.com', password='OldPass123', email='acct2@example.com')
        self.client.login(username='acct2@example.com', password='OldPass123')
        resp = self.client.post(reverse('account_settings'), {
            'form': 'password', 'old_password': 'OldPass123',
            'new_password1': 'BrandNewPass789', 'new_password2': 'BrandNewPass789',
        })
        self.assertRedirects(resp, reverse('account_settings'))
        self.client.logout()
        self.assertTrue(self.client.login(username='acct2@example.com', password='BrandNewPass789'))
    def test_password_reset_flow(self):
        User.objects.create_user(username='reset@example.com', password='OldPass123', email='reset@example.com')

        resp = self.client.post(reverse('password_reset'), {'email': 'reset@example.com'})
        self.assertRedirects(resp, reverse('password_reset_done'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('reset', mail.outbox[0].subject.lower())

        # Extract the reset link from the email body
        import re
        match = re.search(r'/password-reset-confirm/(?P<uidb64>[^/]+)/(?P<token>[^/\s]+)/', mail.outbox[0].body)
        self.assertIsNotNone(match)

        # Follow the link (Django's confirm view redirects once with a valid token, replacing it in session)
        confirm_url = f"/password-reset-confirm/{match.group('uidb64')}/{match.group('token')}/"
        resp = self.client.get(confirm_url, follow=True)
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(resp.request['PATH_INFO'], {
            'new_password1': 'BrandNewPass456', 'new_password2': 'BrandNewPass456',
        })
        self.assertRedirects(resp, reverse('password_reset_complete'))

        # Old password no longer works, new one does
        self.assertFalse(self.client.login(username='reset@example.com', password='OldPass123'))
        self.assertTrue(self.client.login(username='reset@example.com', password='BrandNewPass456'))


class WishlistTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Paid Course', slug='paid-course', description='...', instructor='A',
            price=Decimal('30000.00'), duration_hours=5, level='beginner', category=self.category,
        )
        User.objects.create_user(username='wisher@example.com', password='pass12345')
        self.client.login(username='wisher@example.com', password='pass12345')

    def test_toggle_wishlist_adds_and_removes(self):
        resp = self.client.post(reverse('toggle_wishlist', args=[self.course.slug]))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Wishlist.objects.filter(course=self.course).count(), 1)

        self.client.post(reverse('toggle_wishlist', args=[self.course.slug]))
        self.assertEqual(Wishlist.objects.filter(course=self.course).count(), 0)

    def test_wishlist_page_requires_login(self):
        self.client.logout()
        resp = self.client.get(reverse('my_wishlist'))
        self.assertEqual(resp.status_code, 302)

    def test_wishlist_page_lists_saved_courses(self):
        self.client.post(reverse('toggle_wishlist', args=[self.course.slug]))
        resp = self.client.get(reverse('my_wishlist'))
        self.assertContains(resp, 'Paid Course')


class ReviewTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Free Course', slug='free-course', description='...', instructor='A',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        self.user = User.objects.create_user(username='reviewer@example.com', password='pass12345')

    def test_review_requires_enrollment(self):
        self.client.login(username='reviewer@example.com', password='pass12345')
        resp = self.client.post(reverse('submit_review', args=[self.course.slug]), {'rating': 5, 'comment': 'Great!'})
        self.assertRedirects(resp, reverse('course_detail', args=[self.course.slug]))
        self.assertEqual(Review.objects.count(), 0)

    def test_review_updates_course_rating(self):
        self.client.login(username='reviewer@example.com', password='pass12345')
        self.client.post(reverse('enroll_free', args=[self.course.slug]))
        self.client.post(reverse('submit_review', args=[self.course.slug]), {'rating': 4, 'comment': 'Solid course'})

        self.course.refresh_from_db()
        self.assertEqual(self.course.rating_count, 1)
        self.assertEqual(self.course.rating, Decimal('4.00'))

        # A second user's review updates the average
        User.objects.create_user(username='reviewer2@example.com', password='pass12345')
        self.client.login(username='reviewer2@example.com', password='pass12345')
        self.client.post(reverse('enroll_free', args=[self.course.slug]))
        self.client.post(reverse('submit_review', args=[self.course.slug]), {'rating': 2, 'comment': ''})

        self.course.refresh_from_db()
        self.assertEqual(self.course.rating_count, 2)
        self.assertEqual(self.course.rating, Decimal('3.00'))

    def test_one_review_per_user_updates_in_place(self):
        self.client.login(username='reviewer@example.com', password='pass12345')
        self.client.post(reverse('enroll_free', args=[self.course.slug]))
        self.client.post(reverse('submit_review', args=[self.course.slug]), {'rating': 3, 'comment': 'ok'})
        self.client.post(reverse('submit_review', args=[self.course.slug]), {'rating': 5, 'comment': 'actually great'})

        self.assertEqual(Review.objects.filter(user=self.user, course=self.course).count(), 1)
        review = Review.objects.get(user=self.user, course=self.course)
        self.assertEqual(review.rating, 5)
        self.course.refresh_from_db()
        self.assertEqual(self.course.rating_count, 1)


class CouponTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Paid Course', slug='paid-course', description='...', instructor='A',
            price=Decimal('10000.00'), duration_hours=5, level='beginner', category=self.category,
        )
        User.objects.create_user(username='shopper@example.com', password='pass12345')
        self.client.login(username='shopper@example.com', password='pass12345')
        self.coupon = Coupon.objects.create(code='SAVE20', discount_percent=20, active=True)

    def test_apply_valid_coupon_updates_checkout_price(self):
        self.client.post(reverse('checkout', args=[self.course.slug]), {'method': 'apply_coupon', 'code': 'save20'})
        resp = self.client.get(reverse('checkout', args=[self.course.slug]))
        self.assertContains(resp, 'SAVE20')
        self.assertContains(resp, '8000')  # 10000 - 20% = 8000

    def test_invalid_coupon_shows_error_and_no_discount(self):
        self.client.post(reverse('checkout', args=[self.course.slug]), {'method': 'apply_coupon', 'code': 'NOTREAL'})
        resp = self.client.get(reverse('checkout', args=[self.course.slug]))
        self.assertNotContains(resp, 'NOTREAL applied')

    def test_expired_coupon_rejected(self):
        from django.utils import timezone
        import datetime
        self.coupon.valid_until = timezone.now() - datetime.timedelta(days=1)
        self.coupon.save()
        self.client.post(reverse('checkout', args=[self.course.slug]), {'method': 'apply_coupon', 'code': 'SAVE20'})
        resp = self.client.get(reverse('checkout', args=[self.course.slug]))
        self.assertNotContains(resp, 'coupon-applied')

    @patch('api.paystack.initialize_transaction')
    def test_paystack_checkout_uses_discounted_amount(self, mock_init):
        mock_init.return_value = {'authorization_url': 'https://checkout.paystack.com/xyz', 'access_code': 'x', 'reference': 'y'}
        self.client.post(reverse('checkout', args=[self.course.slug]), {'method': 'apply_coupon', 'code': 'SAVE20'})
        self.client.post(reverse('checkout', args=[self.course.slug]), {'method': 'paystack'})

        order = Order.objects.get(course=self.course)
        self.assertEqual(order.amount, Decimal('8000.00'))
        self.assertEqual(order.discount_amount, Decimal('2000.00'))
        self.assertEqual(order.coupon, self.coupon)

    def test_coupon_usage_count_increments_on_payment(self):
        order = Order.objects.create(
            user=User.objects.get(username='shopper@example.com'), course=self.course,
            amount=Decimal('8000.00'), coupon=self.coupon, discount_amount=Decimal('2000.00'),
            method=Order.Method.BANK_TRANSFER,
        )
        order.mark_paid()
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.times_used, 1)


class InstructorPageTests(TestCase):
    def test_instructor_page_lists_their_courses(self):
        category = Category.objects.create(name='Python', slug='python')
        instructor = Instructor.objects.create(name='Ada Lovelace', slug='ada-lovelace', bio='Pioneer.')
        Course.objects.create(
            title='Algorithms 101', slug='algorithms-101', description='...', instructor='Ada Lovelace',
            instructor_profile=instructor, price=0, duration_hours=1, level='beginner',
            category=category, is_free=True,
        )
        resp = self.client.get(reverse('instructor_detail', args=[instructor.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Algorithms 101')
        self.assertContains(resp, 'Pioneer.')


class DashboardSubPagesTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Python', slug='python')
        self.course1 = Course.objects.create(
            title='In Progress Course', slug='in-progress-course', description='...', instructor='A',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        self.course2 = Course.objects.create(
            title='Completed Course', slug='completed-course', description='...', instructor='B',
            price=0, duration_hours=1, level='beginner', category=self.category, is_free=True,
        )
        self.user = User.objects.create_user(username='dashuser@example.com', password='pass12345')
        self.client.login(username='dashuser@example.com', password='pass12345')
        self.client.post(reverse('enroll_free', args=[self.course1.slug]))
        self.client.post(reverse('enroll_free', args=[self.course2.slug]))

        from django.utils import timezone
        completed_enrollment = Enrollment.objects.get(user=self.user, course=self.course2)
        completed_enrollment.progress = 100
        completed_enrollment.completed_at = timezone.now()
        completed_enrollment.save()

    def test_all_dashboard_subpages_require_login(self):
        self.client.logout()
        for name in ['my_courses', 'progress_overview', 'certificates']:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, f"{name} should redirect when logged out")

    def test_my_courses_shows_in_progress_and_completed(self):
        resp = self.client.get(reverse('my_courses'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'In Progress Course')
        self.assertContains(resp, 'Completed Course')

    def test_progress_page_shows_correct_counts(self):
        resp = self.client.get(reverse('progress_overview'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'In Progress Course')

    def test_certificates_page_shows_only_completed(self):
        self.client.get(reverse('dashboard'))  # drain leftover enrollment toast messages first
        resp = self.client.get(reverse('certificates'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Completed Course')
        self.assertNotContains(resp, 'In Progress Course')

    def test_certificate_detail_accessible_for_completed_course(self):
        resp = self.client.get(reverse('certificate_detail', args=[self.course2.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Certificate of Completion')
        self.assertContains(resp, 'Completed Course')

    def test_certificate_detail_404s_for_incomplete_course(self):
        resp = self.client.get(reverse('certificate_detail', args=[self.course1.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_certificate_detail_404s_for_other_users_course(self):
        User.objects.create_user(username='other@example.com', password='pass12345')
        self.client.login(username='other@example.com', password='pass12345')
        resp = self.client.get(reverse('certificate_detail', args=[self.course2.slug]))
        self.assertEqual(resp.status_code, 404)

    def test_sidebar_present_on_all_dashboard_pages(self):
        for name in ['dashboard', 'my_courses', 'progress_overview', 'certificates', 'my_orders', 'my_wishlist', 'account_settings']:
            resp = self.client.get(reverse(name))
            self.assertContains(resp, 'sidebar-item', msg_prefix=f"{name} missing sidebar")
            self.assertNotContains(resp, 'href="#"', msg_prefix=f"{name} still has a dead link")
