def market_context(request):
    """Return the current visitor market label for templates."""
    country_code = (
        request.META.get('HTTP_CF_IPCOUNTRY')
        or request.META.get('CF_IPCOUNTRY')
        or request.COOKIES.get('country_code')
        or 'NG'
    ).upper()

    country_map = {
        'NG': ('Nigeria', 'Nigerian'),
        'US': ('United States', 'US'),
        'GB': ('United Kingdom', 'UK'),
        'KE': ('Kenya', 'Kenyan'),
        'GH': ('Ghana', 'Ghanaian'),
        'ZA': ('South Africa', 'South African'),
    }

    country_name, market_name = country_map.get(country_code, ('Nigeria', 'Nigerian'))
    return {
        'country_name': country_name,
        'market_name': market_name,
    }
