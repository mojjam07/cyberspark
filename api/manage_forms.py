"""Forms for the staff admin dashboard (/manage/)."""

import re
from decimal import Decimal

from django import forms
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils.text import slugify

from .models import Category, Coupon, Course, Instructor, Lesson


class StyledModelForm(forms.ModelForm):
    """Applies the site's form classes to every widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs['class'] = 'mg-checkbox'
            else:
                widget.attrs['class'] = (widget.attrs.get('class', '') + ' form-input').strip()
            if isinstance(widget, forms.Textarea):
                widget.attrs.setdefault('rows', 5)


class AutoSlugMixin:
    """Slug is optional: generated from `slug_source` and made unique."""

    slug_source = 'title'

    def clean_slug(self):
        typed = (self.cleaned_data.get('slug') or '').strip()
        model = self._meta.model

        def taken(value):
            qs = model.objects.filter(slug=value)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            return qs.exists()

        if typed:
            slug = slugify(typed)
            if not slug:
                raise forms.ValidationError("Use letters, numbers and hyphens only.")
            if taken(slug):
                raise forms.ValidationError("That slug is already in use.")
            return slug

        base = slugify(self.cleaned_data.get(self.slug_source) or '')
        if not base:
            raise forms.ValidationError("Enter a slug, or fill in the name/title so one can be generated.")
        slug, n = base, 2
        while taken(slug):
            slug = f"{base}-{n}"
            n += 1
        return slug


class CategoryAdminForm(AutoSlugMixin, StyledModelForm):
    slug_source = 'name'

    class Meta:
        model = Category
        fields = ['name', 'slug']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        self.fields['slug'].help_text = "Leave blank to generate from the name."


class InstructorAdminForm(AutoSlugMixin, StyledModelForm):
    slug_source = 'name'

    class Meta:
        model = Instructor
        fields = ['name', 'slug', 'title', 'photo_url', 'bio']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        self.fields['slug'].help_text = "Leave blank to generate from the name."


class CourseAdminForm(AutoSlugMixin, StyledModelForm):
    slug_source = 'title'

    class Meta:
        model = Course
        fields = [
            'title', 'slug', 'category', 'instructor', 'instructor_profile', 'description',
            'price', 'duration_hours', 'level', 'thumbnail', 'is_free', 'is_featured', 'is_published',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['slug'].required = False
        self.fields['slug'].help_text = "Leave blank to generate from the title."
        self.fields['price'].required = False
        self.fields['price'].help_text = "Naira. Not needed for free courses."
        self.fields['instructor_profile'].required = False
        self.fields['instructor_profile'].help_text = "Optional — links the instructor name to a bio page."
        self.fields['thumbnail'].help_text = "Paste an image URL (e.g. from Cloudinary)."
        self.fields['is_published'].help_text = "Unticked = hidden from learners (draft)."

    def clean(self):
        cleaned = super().clean()
        price = cleaned.get('price')
        if cleaned.get('is_free'):
            cleaned['price'] = price if price is not None else Decimal('0')
        elif price is None or price <= 0:
            self.add_error('price', 'Enter a price greater than 0, or tick "Is free".')
        return cleaned


class LessonAdminForm(StyledModelForm):
    class Meta:
        model = Lesson
        fields = ['course', 'order', 'title', 'duration_minutes', 'video_url', 'content']


class CouponAdminForm(StyledModelForm):
    class Meta:
        model = Coupon
        fields = ['code', 'discount_percent', 'active', 'valid_until', 'max_uses']
        widgets = {
            'valid_until': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valid_until'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d']

    def clean_code(self):
        code = (self.cleaned_data.get('code') or '').strip().upper()
        if not re.fullmatch(r'[A-Z0-9_-]+', code):
            raise forms.ValidationError("Use letters, numbers, hyphens or underscores only.")
        qs = Coupon.objects.filter(code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A coupon with this code already exists.")
        return code

    def clean_discount_percent(self):
        pct = self.cleaned_data.get('discount_percent')
        if pct is None or not (1 <= pct <= 100):
            raise forms.ValidationError("Enter a percentage between 1 and 100.")
        return pct


class ManualEnrollForm(forms.Form):
    email = forms.CharField(
        label="Learner's email",
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'learner@example.com'}),
    )
    course = forms.ModelChoiceField(queryset=Course.objects.none(), widget=forms.Select(attrs={'class': 'form-input'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['course'].queryset = Course.objects.filter(is_published=True).order_by('title')
        self.user = None

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        user = User.objects.filter(Q(email__iexact=email) | Q(username__iexact=email)).first()
        if not user:
            raise forms.ValidationError("No account with that email — ask them to sign up first.")
        self.user = user
        return email
