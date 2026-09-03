from django.conf import settings
from django.core.exceptions import PermissionDenied


class AdminSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        admin_path = "/" + str(getattr(settings, 'ADMIN_URL', 'admin')).strip("/")
        request_path = request.path.rstrip("/")

        if request_path == admin_path or request_path.startswith(admin_path + "/"):
            allowed_hosts = getattr(settings, 'ADMIN_ALLOWED_HOSTS', [])
            allowed_ips = getattr(settings, 'ADMIN_ALLOWED_IPS', [])

            if allowed_hosts:
                host = request.get_host().split(':')[0]
                if host not in allowed_hosts and not any(host.endswith(f'.{allowed_host}') for allowed_host in allowed_hosts if allowed_host and '.' in allowed_host):
                    raise PermissionDenied('Admin access is restricted to configured hosts.')

            if allowed_ips:
                client_ip = request.META.get('REMOTE_ADDR', '')
                if client_ip not in allowed_ips:
                    raise PermissionDenied('This IP is not allowed to access the admin panel.')

        return self.get_response(request)
