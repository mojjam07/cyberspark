from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.core.validators import validate_email

from .models import Order, Review


class SignupForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput, min_length=8, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, min_length=8, label="Confirm password")

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        validate_email(email)
        if User.objects.filter(username__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "Passwords do not match.")
        return cleaned

    def save(self):
        email = self.cleaned_data['email']
        full_name = self.cleaned_data['full_name'].strip()
        first_name, _, last_name = full_name.partition(' ')
        user = User.objects.create_user(
            username=email,
            email=email,
            password=self.cleaned_data['password1'],
            first_name=first_name,
            last_name=last_name,
        )
        return user


class LoginForm(forms.Form):
    username = forms.CharField(label="Email address")
    password = forms.CharField(widget=forms.PasswordInput)


class BankTransferProofForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['proof_of_payment', 'payer_note']
        widgets = {
            'payer_note': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Name / bank the transfer was sent from (optional but helps us match it faster)',
            }),
            'proof_of_payment': forms.ClearableFileInput(attrs={'class': 'form-input'}),
        }

    def clean_proof_of_payment(self):
        f = self.cleaned_data.get('proof_of_payment')
        if not f:
            raise forms.ValidationError("Please attach your proof of payment.")
        max_size_mb = 5
        if f.size > max_size_mb * 1024 * 1024:
            raise forms.ValidationError(f"File too large. Max size is {max_size_mb}MB.")
        allowed_types = ('image/jpeg', 'image/png', 'image/webp', 'application/pdf')
        content_type = getattr(f, 'content_type', None)
        if content_type and content_type not in allowed_types:
            raise forms.ValidationError("Please upload a JPG, PNG, WEBP image or a PDF.")
        return f


class ContactForm(forms.Form):
    name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-input'}))
    email = forms.EmailField(widget=forms.EmailInput(attrs={'class': 'form-input'}))
    subject = forms.CharField(max_length=200, widget=forms.TextInput(attrs={'class': 'form-input'}))
    message = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 6}))
    # Honeypot: real users never see or fill this field (hidden via CSS).
    # Bots that auto-fill every field will trip it.
    website = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean_website(self):
        value = self.cleaned_data.get('website')
        if value:
            raise forms.ValidationError("Spam detected.")
        return value


class AccountUpdateForm(forms.Form):
    full_name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={'class': 'form-input'}))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user:
            self.fields['full_name'].initial = user.get_full_name()

    def save(self):
        full_name = self.cleaned_data['full_name'].strip()
        first_name, _, last_name = full_name.partition(' ')
        self.user.first_name = first_name
        self.user.last_name = last_name
        self.user.save(update_fields=['first_name', 'last_name'])
        return self.user


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-input'


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.Select(choices=[(i, f"{i} star{'s' if i != 1 else ''}") for i in range(5, 0, -1)], attrs={'class': 'form-input'}),
            'comment': forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': "What did you think of this course? (optional)"}),
        }


class CouponForm(forms.Form):
    code = forms.CharField(max_length=32, widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Enter coupon code'}))
