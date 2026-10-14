from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_GET


@require_GET
def healthz_view(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        return JsonResponse({'status': 'ok'})
    except Exception as exc:
        return JsonResponse(
            {'status': 'error', 'reason': str(exc)},
            status=503,
        )
