"""URLs du suivi commercial (namespace « sales »).

Ce module est inclus dans scolaloop.urls AVANT django.contrib.admin : les
routes partagent le préfixe « admin/ » et seraient autrement avalées par
l'admin Django (404).
"""

from django.urls import path

from . import sales_exports, sales_views

app_name = "sales"

urlpatterns = [
    # Onglet 1 — Tableau de bord & synthèse
    path(
        "admin/sales-tracking/",
        sales_views.sales_tracking_view,
        name="dashboard",
    ),
    path(
        "admin/sales-tracking/export.csv",
        sales_exports.reports_export_csv,
        name="export_csv",
    ),
    # Onglet 2 — Formulaire de saisie (création / édition)
    path(
        "admin/sales-tracking/rapport/nouveau/",
        sales_views.report_form_view,
        name="report_new",
    ),
    path(
        "admin/sales-tracking/rapport/<int:pk>/",
        sales_views.report_form_view,
        name="report_edit",
    ),
    path(
        "admin/sales-tracking/rapport/<int:pk>/soumettre/",
        sales_views.report_submit_view,
        name="report_submit",
    ),
    path(
        "admin/sales-tracking/rapport/<int:pk>/supprimer/",
        sales_views.report_delete_view,
        name="report_delete",
    ),
    # Onglet 3 — Validation & paie des commissions (admin only)
    path(
        "admin/sales-tracking/valider/",
        sales_views.commission_list_view,
        name="commission_list",
    ),
    path(
        "admin/sales-tracking/rapport/<int:pk>/valider/",
        sales_views.commission_validate_view,
        name="commission_validate",
    ),
    # Exports
    path(
        "admin/sales-tracking/rapport/<int:pk>/export.xlsx",
        sales_exports.report_export_xlsx,
        name="export_xlsx",
    ),
    path(
        "admin/sales-tracking/rapport/<int:pk>/export.pdf",
        sales_exports.report_export_pdf,
        name="export_pdf",
    ),
    # API JSON (CRUD + calcul des commissions)
    path("api/sales/kpis/", sales_views.api_kpis_api, name="api_kpis"),
    path("api/sales/reports/", sales_views.api_reports_api, name="api_reports"),
    path(
        "api/sales/reports/<int:pk>/",
        sales_views.api_report_detail_api,
        name="api_report",
    ),
    path(
        "api/sales/reports/<int:pk>/validate/",
        sales_views.api_report_validate_api,
        name="api_validate",
    ),
]
