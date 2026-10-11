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


import shutil
import tempfile

from django.test import override_settings


class ManageDashboardTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='staff@example.com', email='staff@example.com', password='pass12345', is_staff=True)
        self.learner = User.objects.create_user(username='learner@example.com', email='learner@example.com', password='pass12345')
        self.category = Category.objects.create(name='Python', slug='python')
        self.course = Course.objects.create(
            title='Paid Course', slug='paid-course', description='...', instructor='A',
            price=Decimal('20000.00'), duration_hours=2, level='beginner', category=self.category,
        )
        self.client.login(username='staff@example.com', password='pass12345')

    def _csv(self, text, name='data.csv'):
        return SimpleUploadedFile(name, text.encode('utf-8'), content_type='text/csv')

    # --- access control ---
    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        resp = self.client.get(reverse('manage_home'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_non_staff_cannot_access_any_manage_page(self):
        self.client.login(username='learner@example.com', password='pass12345')
        for url in [reverse('manage_home'), reverse('manage_list', args=['courses']), reverse('manage_orders'),
                    reverse('manage_users'), reverse('manage_import'), reverse('manage_create', args=['courses'])]:
            resp = self.client.get(url)
            self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False, msg_prefix=url)

    def test_non_staff_cannot_post_actions(self):
        order = Order.objects.create(user=self.learner, course=self.course, amount=1, method=Order.Method.BANK_TRANSFER,
                                     status=Order.Status.AWAITING_REVIEW)
        self.client.login(username='learner@example.com', password='pass12345')
        self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'approve'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_REVIEW)
        self.assertFalse(Enrollment.objects.filter(user=self.learner).exists())

    def test_unknown_resource_404(self):
        self.assertEqual(self.client.get('/manage/nonsense/').status_code, 404)

    def test_staff_sees_admin_link_learner_does_not(self):
        self.assertContains(self.client.get(reverse('dashboard')), reverse('manage_home'))
        self.client.login(username='learner@example.com', password='pass12345')
        self.assertNotContains(self.client.get(reverse('dashboard')), '/manage/')

    # --- pages render, no dead links ---
    def test_every_manage_page_loads_without_dead_links(self):
        urls = [reverse('manage_home'), reverse('manage_orders'), reverse('manage_enrollments'),
                reverse('manage_users'), reverse('manage_import')]
        for r in ['courses', 'categories', 'instructors', 'lessons', 'coupons']:
            urls += [reverse('manage_list', args=[r]), reverse('manage_create', args=[r])]
        urls += [reverse('manage_edit', args=['courses', self.course.pk]), reverse('manage_delete', args=['courses', self.course.pk])]
        for url in urls:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            self.assertNotContains(resp, 'href="#"', msg_prefix=url)
            self.assertContains(resp, 'sidebar-item', msg_prefix=url)

    def test_home_shows_stats_and_pending_badge(self):
        Order.objects.create(user=self.learner, course=self.course, amount=1, method=Order.Method.BANK_TRANSFER,
                             status=Order.Status.AWAITING_REVIEW)
        resp = self.client.get(reverse('manage_home'))
        self.assertContains(resp, 'Awaiting review')
        self.assertContains(resp, 'mg-badge')

    def test_robots_blocks_manage(self):
        self.assertContains(self.client.get('/robots.txt'), 'Disallow: /manage/')

    # --- CRUD ---
    def test_create_course_autogenerates_unique_slug(self):
        data = {'title': 'Brand New', 'category': self.category.pk, 'instructor': 'Z', 'description': 'd',
                'price': '15000', 'duration_hours': 3, 'level': 'beginner', 'is_published': 'on'}
        resp = self.client.post(reverse('manage_create', args=['courses']), data)
        self.assertRedirects(resp, reverse('manage_list', args=['courses']))
        self.assertTrue(Course.objects.filter(slug='brand-new').exists())
        self.client.post(reverse('manage_create', args=['courses']), data)
        self.assertTrue(Course.objects.filter(slug='brand-new-2').exists())

    def test_paid_course_requires_price_free_course_does_not(self):
        base = {'title': 'T', 'category': self.category.pk, 'instructor': 'Z', 'description': 'd',
                'duration_hours': 3, 'level': 'beginner'}
        resp = self.client.post(reverse('manage_create', args=['courses']), {**base, 'price': '0'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'greater than 0')
        self.assertFalse(Course.objects.filter(title='T').exists())
        resp = self.client.post(reverse('manage_create', args=['courses']), {**base, 'is_free': 'on'})
        self.assertRedirects(resp, reverse('manage_list', args=['courses']))
        self.assertEqual(Course.objects.get(title='T').price, 0)

    def test_edit_course(self):
        data = {'title': 'Renamed', 'slug': 'paid-course', 'category': self.category.pk, 'instructor': 'A',
                'description': 'd', 'price': '25000', 'duration_hours': 2, 'level': 'advanced', 'is_published': 'on'}
        self.client.post(reverse('manage_edit', args=['courses', self.course.pk]), data)
        self.course.refresh_from_db()
        self.assertEqual((self.course.title, self.course.price, self.course.level), ('Renamed', Decimal('25000.00'), 'advanced'))

    def test_manual_slug_conflict_rejected(self):
        resp = self.client.post(reverse('manage_create', args=['categories']), {'name': 'Other', 'slug': 'python'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already in use')

    def test_category_slug_autogenerated(self):
        self.client.post(reverse('manage_create', args=['categories']), {'name': 'Data Science'})
        self.assertTrue(Category.objects.filter(slug='data-science').exists())

    def test_instructor_and_lesson_creation(self):
        self.client.post(reverse('manage_create', args=['instructors']), {'name': 'Ada Obi', 'title': 'Engineer'})
        self.assertTrue(Instructor.objects.filter(slug='ada-obi').exists())
        resp = self.client.post(reverse('manage_create', args=['lessons']), {
            'course': self.course.pk, 'order': 1, 'title': 'Intro', 'duration_minutes': 10})
        self.assertRedirects(resp, f"{reverse('manage_list', args=['lessons'])}?course={self.course.pk}")
        self.assertEqual(self.course.lessons.count(), 1)

    def test_lessons_list_filters_by_course(self):
        other = Course.objects.create(title='Other', slug='other', description='.', instructor='B', price=1,
                                      duration_hours=1, level='beginner', category=self.category)
        Lesson.objects.create(course=self.course, title='Only Mine', order=1)
        Lesson.objects.create(course=other, title='Not Mine', order=1)
        resp = self.client.get(f"{reverse('manage_list', args=['lessons'])}?course={self.course.pk}")
        self.assertContains(resp, 'Only Mine')
        self.assertNotContains(resp, 'Not Mine')

    def test_coupon_code_normalised_and_validated(self):
        url = reverse('manage_create', args=['coupons'])
        self.client.post(url, {'code': ' save10 ', 'discount_percent': 10, 'active': 'on'})
        self.assertTrue(Coupon.objects.filter(code='SAVE10').exists())
        self.assertContains(self.client.post(url, {'code': 'SAVE10', 'discount_percent': 5}), 'already exists')
        self.assertContains(self.client.post(url, {'code': 'BIG', 'discount_percent': 150}), 'between 1 and 100')
        self.assertContains(self.client.post(url, {'code': 'bad code!', 'discount_percent': 5}), 'letters, numbers')

    def test_search_filters_list(self):
        Course.objects.create(title='Zebra Skills', slug='zebra', description='.', instructor='B', price=1,
                              duration_hours=1, level='beginner', category=self.category)
        resp = self.client.get(reverse('manage_list', args=['courses']) + '?q=zebra')
        self.assertContains(resp, 'Zebra Skills')
        self.assertNotContains(resp, 'Paid Course')

    # --- delete protection ---
    def test_course_with_enrollments_cannot_be_deleted(self):
        Enrollment.objects.create(user=self.learner, course=self.course)
        resp = self.client.get(reverse('manage_delete', args=['courses', self.course.pk]))
        self.assertContains(resp, 'enrollment')
        self.client.post(reverse('manage_delete', args=['courses', self.course.pk]))
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())

    def test_unused_course_can_be_deleted(self):
        self.client.post(reverse('manage_delete', args=['courses', self.course.pk]))
        self.assertFalse(Course.objects.filter(pk=self.course.pk).exists())

    def test_category_with_courses_cannot_be_deleted(self):
        self.client.post(reverse('manage_delete', args=['categories', self.category.pk]))
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_delete_requires_post(self):
        self.client.get(reverse('manage_delete', args=['courses', self.course.pk]))
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())

    # --- orders ---
    def _bank_order(self, **kw):
        return Order.objects.create(user=self.learner, course=self.course, amount=Decimal('20000'),
                                    method=Order.Method.BANK_TRANSFER, status=Order.Status.AWAITING_REVIEW, **kw)

    def test_approve_bank_transfer_enrolls_learner(self):
        order = self._bank_order()
        resp = self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'approve', 'next': reverse('manage_home')})
        self.assertRedirects(resp, reverse('manage_home'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.reviewed_by), (Order.Status.PAID, self.staff))
        self.assertTrue(Enrollment.objects.filter(user=self.learner, course=self.course).exists())

    def test_reject_then_reapprove(self):
        order = self._bank_order()
        self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'reject'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.FAILED)
        self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'approve'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_paid_order_cannot_be_rejected(self):
        order = self._bank_order()
        order.mark_paid()
        self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'reject'})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PAID)

    def test_paystack_order_cannot_be_hand_approved(self):
        order = Order.objects.create(user=self.learner, course=self.course, amount=1, method=Order.Method.PAYSTACK)
        self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'approve'})
        order.refresh_from_db()
        self.assertNotEqual(order.status, Order.Status.PAID)
        self.assertFalse(Enrollment.objects.filter(user=self.learner).exists())

    def test_order_action_ignores_offsite_next_url(self):
        order = self._bank_order()
        resp = self.client.post(reverse('manage_order_action', args=[order.pk]), {'action': 'reject', 'next': 'https://evil.example.com/'})
        self.assertRedirects(resp, reverse('manage_orders'), fetch_redirect_response=False)

    def test_orders_filter_by_status(self):
        self._bank_order()
        done = Order.objects.create(user=self.learner, course=self.course, amount=5, method=Order.Method.PAYSTACK, status=Order.Status.PAID)
        resp = self.client.get(reverse('manage_orders') + '?status=paid')
        self.assertContains(resp, str(done.reference)[:11])
        self.assertNotContains(resp, 'Approve')

    def test_proof_viewable_by_staff_only(self):
        tmp = tempfile.mkdtemp()
        try:
            with override_settings(MEDIA_ROOT=tmp):
                order = self._bank_order()
                order.proof_of_payment.save('proof.png', SimpleUploadedFile('proof.png', b'PNGDATA', content_type='image/png'))
                resp = self.client.get(reverse('manage_order_proof', args=[order.pk]))
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(b''.join(resp.streaming_content), b'PNGDATA')
                self.client.login(username='learner@example.com', password='pass12345')
                self.assertEqual(self.client.get(reverse('manage_order_proof', args=[order.pk])).status_code, 302)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_proof_404_when_none_uploaded(self):
        order = self._bank_order()
        self.assertEqual(self.client.get(reverse('manage_order_proof', args=[order.pk])).status_code, 404)

    # --- enrollments / users ---
    def test_manual_enroll_by_email(self):
        resp = self.client.post(reverse('manage_enrollments'), {'email': 'LEARNER@example.com', 'course': self.course.pk})
        self.assertRedirects(resp, reverse('manage_enrollments'))
        self.assertTrue(Enrollment.objects.filter(user=self.learner, course=self.course).exists())
        self.client.post(reverse('manage_enrollments'), {'email': 'learner@example.com', 'course': self.course.pk})
        self.assertEqual(Enrollment.objects.filter(user=self.learner, course=self.course).count(), 1)

    def test_manual_enroll_unknown_email_errors(self):
        resp = self.client.post(reverse('manage_enrollments'), {'email': 'ghost@example.com', 'course': self.course.pk})
        self.assertContains(resp, 'No account with that email')

    def test_remove_enrollment(self):
        enr = Enrollment.objects.create(user=self.learner, course=self.course)
        self.client.post(reverse('manage_enrollment_remove', args=[enr.pk]))
        self.assertFalse(Enrollment.objects.filter(pk=enr.pk).exists())

    def test_users_page_lists_and_searches(self):
        resp = self.client.get(reverse('manage_users') + '?q=learner@')
        self.assertContains(resp, 'learner@example.com')
        self.assertNotContains(resp, 'staff@example.com')

    # --- CSV import ---
    def test_import_courses_creates_category_and_course(self):
        csv_text = ("title,slug,category,instructor,description,price,duration_hours,level,is_free,is_featured,is_published,thumbnail\n"
                    "Intro to SQL,,Data Science,Ada Obi,Learn SQL.,35000,8,beginner,no,yes,yes,\n"
                    "Free Taster,,Data Science,Ada Obi,Try it.,,1,beginner,yes,no,,\n")
        resp = self.client.post(reverse('manage_import'), {'kind': 'courses', 'file': self._csv(csv_text)})
        self.assertContains(resp, '2 created')
        sql = Course.objects.get(slug='intro-to-sql')
        self.assertEqual((sql.category.slug, sql.price, sql.is_featured, sql.is_published), ('data-science', Decimal('35000'), True, True))
        self.assertTrue(Course.objects.get(slug='free-taster').is_free)

    def test_import_is_all_or_nothing(self):
        csv_text = ("title,category,instructor,description,duration_hours,price\n"
                    "Good One,Brand New Cat,Ada,desc,5,1000\n"
                    ",Brand New Cat,Ada,desc,5,1000\n"
                    "Bad Price,Brand New Cat,Ada,desc,5,0\n")
        resp = self.client.post(reverse('manage_import'), {'kind': 'courses', 'file': self._csv(csv_text)})
        self.assertContains(resp, 'Nothing was imported')
        self.assertContains(resp, 'Row 3')
        self.assertContains(resp, 'Row 4')
        self.assertFalse(Course.objects.filter(title='Good One').exists())
        self.assertFalse(Category.objects.filter(slug='brand-new-cat').exists())

    def test_import_updates_existing_by_slug(self):
        csv_text = ("title,slug,category,instructor,description,price,duration_hours\n"
                    "Paid Course v2,paid-course,Python,A,new desc,30000,4\n")
        resp = self.client.post(reverse('manage_import'), {'kind': 'courses', 'file': self._csv(csv_text)})
        self.assertContains(resp, '1 updated')
        self.course.refresh_from_db()
        self.assertEqual((self.course.title, self.course.price), ('Paid Course v2', Decimal('30000.00')))

    def test_import_lessons_and_upsert(self):
        csv_text = ("course_slug,order,title,duration_minutes\n"
                    "paid-course,1,First,5\npaid-course,2,Second,7\n")
        self.client.post(reverse('manage_import'), {'kind': 'lessons', 'file': self._csv(csv_text)})
        self.assertEqual(self.course.lessons.count(), 2)
        again = "course_slug,order,title,duration_minutes\npaid-course,1,First (edited),9\n"
        resp = self.client.post(reverse('manage_import'), {'kind': 'lessons', 'file': self._csv(again)})
        self.assertContains(resp, '1 updated')
        self.assertEqual(self.course.lessons.get(order=1).title, 'First (edited)')
        self.assertEqual(self.course.lessons.count(), 2)

    def test_import_lessons_unknown_course_rejected(self):
        resp = self.client.post(reverse('manage_import'), {'kind': 'lessons', 'file': self._csv("course_slug,order,title\nnope,1,X\n")})
        self.assertContains(resp, 'no course with slug')

    def test_import_rejects_bad_files(self):
        for upload, expect in [
            (self._csv("title,category\nX,Y\n"), 'Missing required column'),
            (self._csv("a,b\n"), 'Missing required column'),
            (self._csv("", 'empty.csv'), 'empty'),
            (SimpleUploadedFile('data.txt', b'title', content_type='text/plain'), 'csv'),
        ]:
            resp = self.client.post(reverse('manage_import'), {'kind': 'courses', 'file': upload})
            self.assertContains(resp, expect)
        self.assertEqual(Course.objects.count(), 1)

    def test_import_handles_excel_bom(self):
        text = "\ufefftitle,category,instructor,description,duration_hours,price\nBOM Course,Python,A,d,2,500\n"
        resp = self.client.post(reverse('manage_import'), {'kind': 'courses', 'file': self._csv(text)})
        self.assertContains(resp, '1 created')

    def test_template_downloads(self):
        for kind, first in [('courses', 'title,'), ('lessons', 'course_slug,')]:
            resp = self.client.get(reverse('manage_import_template', args=[kind]))
            self.assertEqual(resp['Content-Type'], 'text/csv')
            self.assertTrue(resp.content.decode().startswith(first))
        self.assertEqual(self.client.get('/manage/import/template/bogus/').status_code, 404)

    def test_downloaded_course_template_round_trips(self):
        text = self.client.get(reverse('manage_import_template', args=['courses'])).content.decode()
        resp = self.client.post(reverse('manage_import'), {'kind': 'courses', 'file': self._csv(text)})
        self.assertContains(resp, '1 created')
