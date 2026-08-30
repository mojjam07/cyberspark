from django.core.management.base import BaseCommand

from api.models import Category, Course


CATEGORIES = [
    {'name': 'Python', 'slug': 'python'},
    {'name': 'Web Development', 'slug': 'web-development'},
    {'name': 'Data Science', 'slug': 'data-science'},
    {'name': 'DevOps', 'slug': 'devops'},
]

COURSES = [
    dict(title='Python for Beginners', slug='python-for-beginners', category='python',
         description='Learn Python from scratch with hands-on projects.', instructor='John Doe',
         price=45000, rating=4.8, rating_count=1250, duration_hours=12, level='beginner',
         is_free=False, is_featured=True,
         thumbnail='https://picsum.photos/seed/python-for-beginners/600/400'),
    dict(title='Advanced Python & Flask', slug='advanced-python-flask', category='python',
         description='Build production-ready web apps with Flask.', instructor='Jane Smith',
         price=79000, rating=4.7, rating_count=890, duration_hours=18, level='advanced',
         is_free=False, is_featured=False,
         thumbnail='https://picsum.photos/seed/advanced-python-flask/600/400'),
    dict(title='HTML, CSS, JavaScript Fundamentals', slug='html-css-js-fundamentals', category='web-development',
         description='Master frontend development basics.', instructor='Mike Johnson',
         price=0, rating=4.9, rating_count=2100, duration_hours=15, level='beginner',
         is_free=True, is_featured=True,
         thumbnail='https://picsum.photos/seed/html-css-js-fundamentals/600/400'),
    dict(title='React.js - The Complete Guide', slug='react-complete-guide', category='web-development',
         description='Build modern React applications with hooks and context.', instructor='Sarah Wilson',
         price=69000, rating=4.6, rating_count=1670, duration_hours=25, level='intermediate',
         is_free=False, is_featured=True,
         thumbnail='https://picsum.photos/seed/react-complete-guide/600/400'),
    dict(title='Data Analysis with Pandas & NumPy', slug='pandas-numpy-data-analysis', category='data-science',
         description='Learn data manipulation and analysis with Python libraries.', instructor='David Brown',
         price=55000, rating=4.8, rating_count=980, duration_hours=14, level='intermediate',
         is_free=False, is_featured=False,
         thumbnail='https://picsum.photos/seed/pandas-numpy-data-analysis/600/400'),
    dict(title='CI/CD with GitHub Actions', slug='cicd-github-actions', category='devops',
         description='Automate testing and deployment pipelines.', instructor='Amaka Obi',
         price=60000, rating=4.5, rating_count=430, duration_hours=10, level='intermediate',
         is_free=False, is_featured=False,
         thumbnail='https://picsum.photos/seed/cicd-github-actions/600/400'),
]


class Command(BaseCommand):
    help = "Seed the database with demo categories and courses (safe to run multiple times)."

    def handle(self, *args, **options):
        cat_map = {}
        for c in CATEGORIES:
            obj, _ = Category.objects.get_or_create(slug=c['slug'], defaults=c)
            cat_map[c['slug']] = obj

        created_count = 0
        for c in COURSES:
            category = cat_map[c.pop('category')]
            _, created = Course.objects.get_or_create(
                slug=c['slug'], defaults={**c, 'category': category}
            )
            created_count += int(created)

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete. {created_count} new course(s) created (existing ones left untouched)."
        ))
