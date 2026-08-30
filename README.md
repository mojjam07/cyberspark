# CyberSpark Enroll

A Django course-enrollment platform for CyberSpark IT Solutions, with
Paystack and direct bank-transfer payments for the Nigerian market.

## Stack
- Django 5.2, server-rendered templates
- PostgreSQL via Supabase in production, SQLite locally
- Whitenoise for static files
- Paystack for card/bank payments, plus a manual bank-transfer-with-proof
  flow for users who prefer it
- Gunicorn + Render for hosting

## Local setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # then edit .env with your own values

python manage.py migrate
python manage.py seed_demo_data   # optional: sample categories/courses
python manage.py createsuperuser
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`. Admin is at `/admin/`.

## Environment variables
See `.env.example` for the full list. At minimum for local dev you only
need `SECRET_KEY`, `DEBUG=True`. Postgres, Paystack, Cloudinary, and SMTP
are all optional locally — without `DATABASE_URL` it falls back to SQLite,
without Paystack keys the checkout page hides that option and shows bank
transfer only, without `CLOUDINARY_URL` uploads go to local disk, and
without `EMAIL_HOST` emails print to the console instead of sending.

## Pages
Home, course catalog, course detail, checkout, dashboard, account settings,
order history, and the usual site pages: About, Contact (working form),
FAQ, Terms of Service (incl. refund policy), Privacy Policy.

## Payments
- **Paystack**: `checkout` view calls `POST /transaction/initialize`, the
  user pays on Paystack's hosted page, and we confirm via both a browser
  callback and a server-to-server webhook (`api/paystack.py`,
  `views.paystack_callback`, `views.paystack_webhook`). The webhook is the
  authoritative source of truth.
- **Bank transfer**: user uploads proof of payment (`api/forms.py:
  BankTransferProofForm`), creating an `Order` in `awaiting_review` status.
  An admin approves or rejects it from Django admin
  (`Order` list → select rows → "Approve selected bank-transfer orders").
  Approval creates the `Enrollment` automatically. Pending reviews sort to
  the top of the admin list automatically.

## Course content & progress
Each `Course` has `Lesson`s (add them inline from the Course admin page).
Enrolled users see them at `/courses/<slug>/learn/` and can tick lessons
off; `Enrollment.progress` recomputes automatically and marks the course
complete at 100%.

## Reviews, wishlist, instructors, coupons
- **Reviews**: enrolled users can rate/review a course from its detail
  page; `Course.rating`/`rating_count` recompute automatically whenever a
  review is added, updated, or deleted.
- **Wishlist**: any logged-in user can save a course for later from the
  detail page; view saved courses at `/wishlist/`.
- **Instructor pages**: `Course.instructor` stays a plain text field
  (unchanged), but you can optionally link a course to a richer
  `Instructor` profile (bio, photo, title) from the admin — when linked, a
  page at `/instructors/<slug>/` lists everything they teach.
- **Coupons**: create a `Coupon` in admin (code + % off, optional expiry
  and usage cap). Users apply it at checkout before paying by either
  method; usage count increments only once a payment actually succeeds.

## SEO & security
- `sitemap.xml` and `robots.txt` are served automatically
  (`api/sitemaps.py`) — courses and static pages are included, dashboard/
  checkout/account pages are excluded from crawling.
- Course and catalog pages carry Open Graph / Twitter Card meta tags
  (`{% block og_image %}` etc. in `base.html`) so shared links render
  properly on WhatsApp, Twitter, etc.
- Login, signup, contact, and password-reset are rate-limited
  (`django-ratelimit`) to blunt credential-stuffing and form spam beyond
  the contact form's honeypot. Automatically disabled during
  `manage.py test`. See `RATELIMIT_ENABLE` in `.env.example`.

## Email notifications
Sent on: signup (welcome), free-course enrollment, bank-transfer proof
received, and payment confirmation (both Paystack and approved bank
transfers). Also powers Django's built-in password reset flow
(`/password-reset/`). Locally, with no `EMAIL_HOST` set, emails print to
the console instead of sending — nothing to configure to test the flows.

## Tests

```bash
python manage.py test api
```

Covers signup/login validation, free enrollment, both payment paths
(mocking the Paystack API), the admin approval workflow, lesson-progress
tracking, email notifications, and the password reset flow.

## Deployment
See [`DEPLOYMENT.md`](./DEPLOYMENT.md) for the full Render + Supabase +
Paystack production deployment guide, including which Supabase connection
string to use, required environment variables, and the webhook URL to
configure.

## Project layout
```
api/
  models.py       Category, Course, Lesson, LessonProgress, Enrollment, Order,
                   Instructor, Wishlist, Review, Coupon
  views.py        all views (home, courses, checkout, payments, learn, dashboard,
                   auth, about/contact/terms/privacy/faq, account settings,
                   wishlist, reviews, instructor pages)
  forms.py        Signup/Login/BankTransferProof/Contact/AccountUpdate/Review/
                   Coupon forms, plus a styled PasswordChangeForm
  paystack.py     thin wrapper around the Paystack Transactions API
  emails.py       transactional email helpers (welcome, enrollment, payment,
                   contact form)
  sitemaps.py     sitemap.xml definitions (courses + static pages)
  admin.py        Django admin, incl. bank-transfer approval actions
  templates/      server-rendered HTML, each page with its own CSS in
                   static/css/
  management/commands/seed_demo_data.py
setup/
  settings.py     single environment-driven settings module (Supabase-aware —
                   see DB_CONN_MAX_AGE; rate-limit config — see RATELIMIT_ENABLE)
  urls.py         includes sitemap.xml and robots.txt
```
# cyberspark
