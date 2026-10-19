"""Suivi commercial — dashboard, rapports hebdomadaires, validation des commissions.

Rôles : SUPER_ADMIN (superuser) et ADMIN (groupe Django « ADMIN ») accèdent au
dashboard et à la validation ; un commercial (SalesRep lié à son compte) remplit
ses propres rapports.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    CommissionValidationForm,
    PhysicalDemoFormSet,
    PilotContractFormSet,
    ProspectionDayForm,
    WeeklyReportForm,
)
from .models import (
    CommissionValidation,
    PhysicalDemo,
    PilotContract,
    ProspectionDay,
    SalesRep,
    SalesSettings,
    WeeklyReport,
)

SALES_ROLES = ("ADMIN", "SUPER_ADMIN")
WEEKDAY_LABELS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]


# --------------------------------------------------------------------------- Accès

def user_has_sales_access(user):
    """True si l'utilisateur a le rôle SUPER_ADMIN (superuser) ou ADMIN (groupe)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=SALES_ROLES).exists()


def _require_sales_access(request):
    """Garde des vues réservées ADMIN/SUPER_ADMIN. Retourne la redirection ou None."""
    if user_has_sales_access(request.user):
        return None
    messages.error(request, "Accès réservé aux rôles ADMIN et SUPER_ADMIN.")
    return redirect("routing:dashboard")


def _sales_rep_of(user):
    return getattr(user, "sales_profile", None)


def _can_edit_report(user, report):
    if user_has_sales_access(user):
        return True
    rep = _sales_rep_of(user)
    return rep is not None and report.sales_rep_id == rep.id


# ------------------------------------------------------------------- Filtres / KPI

def _parse_filters(params):
    """Filtres dashboard : commercial, semaine (lundi) ou mois. Retourne un dict."""
    filters = {"rep": None, "mode": "week", "start": None, "end": None, "label": ""}
    rep_raw = params.get("rep") or ""
    if rep_raw.isdigit():
        filters["rep"] = int(rep_raw)

    today = timezone.localdate()
    month_raw = params.get("month") or ""
    week_raw = params.get("week") or ""

    if month_raw:
        try:
            first = datetime.strptime(month_raw, "%Y-%m").date().replace(day=1)
            if first.month == 12:
                nxt = first.replace(year=first.year + 1, month=1)
            else:
                nxt = first.replace(month=first.month + 1)
            filters.update(mode="month", start=first, end=nxt - timedelta(days=1))
            filters["label"] = first.strftime("%B %Y")
        except ValueError:
            pass

    if filters["start"] is None:
        ref = today
        if week_raw:
            try:
                ref = datetime.strptime(week_raw, "%Y-%m-%d").date()
            except ValueError:
                ref = today
        monday = ref - timedelta(days=ref.weekday())
        filters.update(mode="week", start=monday, end=monday + timedelta(days=6))
        filters["label"] = f"Semaine du {monday:%d/%m/%Y}"
    return filters


def _week_count(filters):
    """Nombre de semaines (lundis) couvertes par la période, pour les cibles."""
    if filters["mode"] == "week":
        return 1
    count, day = 0, filters["start"]
    while day <= filters["end"]:
        if day.weekday() == 0:
            count += 1
        day += timedelta(days=1)
    return count or 1


def _focus_status(report, filters):
    """(label, ton) du statut d'affichage pour le rapport (ou son absence)."""
    if report is None:
        deadline = filters["end"] - timedelta(days=1)  # dimanche
        if timezone.localdate() > deadline:
            return "En retard", "late"
        return "Non soumis", "none"
    if report.status == WeeklyReport.Status.VALIDATED:
        return "Validé", "ok"
    if report.status == WeeklyReport.Status.SUBMITTED:
        return "Soumis", "pending"
    if report.is_late:
        return "En retard", "late"
    return "Brouillon", "draft"


def _dashboard_data(params, user):
    """Données du dashboard (page HTML et API JSON partagent cette fonction)."""
    filters = _parse_filters(params)
    settings_obj = SalesSettings.get_solo()

    reps = SalesRep.objects.filter(is_active=True)
    if filters["rep"]:
        reps = reps.filter(pk=filters["rep"])
    reps = list(reps.order_by("full_name"))

    reports = list(
        WeeklyReport.objects.filter(
            week_start__gte=filters["start"], week_start__lte=filters["end"]
        )
        .select_related("sales_rep", "validated_by")
        .prefetch_related("prospection_days", "demos", "contracts", "commission")
    )
    by_rep = {}
    for r in reports:
        by_rep.setdefault(r.sales_rep_id, []).append(r)

    # KPI agrégés (requêtes séparées : évite le croisement des jointures).
    base = WeeklyReport.objects.filter(
        week_start__gte=filters["start"], week_start__lte=filters["end"]
    )
    total_calls = (
        ProspectionDay.objects.filter(report__in=base).aggregate(s=Sum("ecoles_contactees"))["s"] or 0
    )
    total_rdv = ProspectionDay.objects.filter(report__in=base).aggregate(s=Sum("rdv_fixes"))["s"] or 0
    total_demos = PhysicalDemo.objects.filter(report__in=base).count()
    signed_qs = PilotContract.objects.filter(report__in=base, signed=True)
    total_signatures = signed_qs.count()
    total_buses = signed_qs.aggregate(s=Sum("bus_count"))["s"] or 0
    total_commission = (
        CommissionValidation.objects.filter(report__in=base).aggregate(
            s=Sum("total_amount")
        )["s"]
        or Decimal("0")
    )

    weeks = _week_count(filters)
    rows = []
    for rep in reps:
        rep_reports = sorted(by_rep.get(rep.pk, []), key=lambda r: r.week_start)
        focus = rep_reports[-1] if rep_reports else None
        realized = {
            "calls": sum(r.total_contacted for r in rep_reports),
            "demos": sum(r.demos_count for r in rep_reports),
            "signatures": sum(r.signatures_count for r in rep_reports),
            "buses": sum(r.signed_bus_count for r in rep_reports),
        }
        if rep_reports:
            attainment = round(
                sum(r.attainment(settings_obj) for r in rep_reports) / len(rep_reports), 1
            )
        else:
            attainment = 0.0
        status_label, status_tone = _focus_status(focus, filters)
        rows.append(
            {
                "rep": rep,
                "report": focus,
                "realized": realized,
                "targets": {
                    "calls": settings_obj.calls_target * weeks,
                    "demos": settings_obj.demos_target * weeks,
                    "signatures": settings_obj.signatures_target * weeks,
                },
                "attainment": attainment,
                "status_label": status_label,
                "status_tone": status_tone,
            }
        )

    return {
        "filters": filters,
        "weeks": weeks,
        "settings": settings_obj,
        "reps": reps,
        "reports": reports,
        "rows": rows,
        "kpis": {
            "calls": total_calls,
            "appointments": total_rdv,
            "demos": total_demos,
            "signatures": total_signatures,
            "buses": total_buses,
            "commission": total_commission,
        },
    }


# --------------------------------------------------------------------------- Pages

@login_required
@require_GET
def sales_tracking_view(request):
    """Onglet 1 — Tableau de bord & synthèse (ADMIN / SUPER_ADMIN)."""
    blocked = _require_sales_access(request)
    if blocked:
        return blocked
    data = _dashboard_data(request.GET, request.user)
    return render(
        request,
        "routing/sales_dashboard.html",
        {
            **data,
            "tab": "dashboard",
            "rep_filter": request.GET.get("rep", ""),
            "week_filter": request.GET.get("week", ""),
            "month_filter": request.GET.get("month", ""),
        },
    )


class _ProspectionFormSet(BaseInlineFormSet):
    """Préfixe explicite « prospection » (attendu par le template et l'API)."""

    @classmethod
    def get_default_prefix(cls):
        return "prospection"


def _prospection_formset(extra):
    """Formset de la grille prospection : 6 lignes Lundi→Samedi au total."""
    return inlineformset_factory(
        WeeklyReport,
        ProspectionDay,
        form=ProspectionDayForm,
        formset=_ProspectionFormSet,
        extra=extra,
        max_num=6,
        can_delete=False,
    )


@login_required
@require_http_methods(["GET", "POST"])
def report_form_view(request, pk=None):
    """Onglet 2 — Formulaire de saisie (création / édition).

    Un commercial remplit ses propres rapports (commercial auto-détecté) ;
    un admin choisit le commercial et saisit en mode admin.
    """
    settings_obj = SalesSettings.get_solo()
    is_admin = user_has_sales_access(request.user)
    own_rep = _sales_rep_of(request.user)

    if pk is not None:
        report = get_object_or_404(
            WeeklyReport.objects.select_related("sales_rep"), pk=pk
        )
        # Capturé AVANT la validation : ModelForm.is_valid() mute l'instance
        # (construct_instance), on ne pourrait plus détecter un changement de semaine.
        original_week = report.week_start
        if not _can_edit_report(request.user, report):
            messages.error(request, "Vous ne pouvez modifier que vos propres rapports.")
            return redirect("routing:dashboard")
        if not is_admin and report.sales_rep_id != (own_rep.pk if own_rep else None):
            messages.error(request, "Ce rapport appartient à un autre commercial.")
            return redirect("routing:dashboard")
    else:
        original_week = None
        if not is_admin and own_rep is None:
            messages.error(request, "Aucun rattachement commercial pour votre compte.")
            return redirect("routing:dashboard")
        report = WeeklyReport(sales_rep=own_rep) if not is_admin else WeeklyReport()

    restrict_rep = own_rep if not is_admin else None
    if not is_admin and pk is None and own_rep is not None:
        report.sales_rep = own_rep

    initial = {}
    if pk is None and request.method == "GET":
        today = timezone.localdate()
        initial["week_start"] = today - timedelta(days=today.weekday())
        if not is_admin:
            initial["sales_rep"] = own_rep

    report_form = WeeklyReportForm(
        request.POST or None,
        instance=report,
        rep_queryset=SalesRep.objects.filter(is_active=True),
        restrict_rep=restrict_rep,
        initial=initial or None,
    )

    existing_days = (
        report.prospection_days.count() if report.pk and pk is not None else 0
    )
    extra_days = max(6 - existing_days, 0)
    if request.method == "POST":
        # Toutes les semaines ont 6 lignes affichées : extra = 6 - lignes existantes.
        total_forms = request.POST.get("prospection-TOTAL_FORMS")
        if total_forms is not None:
            extra_days = 0  # le formset POST prendra les 6 formes soumises
    ProspectionFS = _prospection_formset(extra_days)
    prospection_fs = demos_fs = contracts_fs = None

    if request.method == "POST":
        if report_form.is_valid():
            # original_week capturé AVANT validation (ModelForm.is_valid() mute
            # l'instance via construct_instance → comparaison impossible après).
            week_changed = (
                original_week is not None
                and report_form.cleaned_data.get("week_start") != original_week
            )

            new_report = report_form.save(commit=False)  # écriture mémoire seule
            dup = WeeklyReport.objects.filter(
                sales_rep=new_report.sales_rep, week_start=new_report.week_start
            )
            if new_report.pk:
                dup = dup.exclude(pk=new_report.pk)

            if dup.exists():
                report_form.add_error(
                    "week_start",
                    "Un rapport existe déjà pour ce commercial sur cette semaine.",
                )
            else:
                # Validation de TOUTES les sections avant toute écriture DB.
                prospection_fs = ProspectionFS(request.POST, instance=new_report)
                demos_fs = PhysicalDemoFormSet(request.POST, instance=new_report)
                contracts_fs = PilotContractFormSet(request.POST, instance=new_report)

                if (
                    prospection_fs.is_valid()
                    and demos_fs.is_valid()
                    and contracts_fs.is_valid()
                ):
                    with transaction.atomic():
                        if week_changed:
                            # Semaine décalée : les lignes de jours sont créées
                            # à zéro pour la nouvelle semaine (pas de collision
                            # d'unicité (report, day) sur les anciennes lignes).
                            report.prospection_days.all().delete()
                            post = request.POST.copy()
                            post["prospection-INITIAL_FORMS"] = "0"
                            # Les ids des lignes supprimées ne doivent pas
                            # survivre : le formset les validerait contre le
                            # queryset (« Sélectionnez un choix valide »).
                            for key in [
                                k for k in post
                                if k.startswith("prospection-") and k.endswith("-id")
                            ]:
                                del post[key]
                            prospection_fs = ProspectionFS(post, instance=new_report)
                            if not prospection_fs.is_valid():  # pragma: no cover
                                raise RuntimeError("Reconstruction de la grille invalide")
                        new_report.save()
                        for i, form in enumerate(prospection_fs.forms):
                            form.instance.day = new_report.week_start + timedelta(days=i)
                        prospection_fs.save()
                        demos_fs.save()
                        contracts_fs.save()

                        if request.POST.get("action") == "submit":
                            new_report.status = WeeklyReport.Status.SUBMITTED
                            new_report.submitted_at = timezone.now()
                            new_report.save(update_fields=["status", "submitted_at"])
                            messages.success(
                                request,
                                f"Rapport du {new_report.week_start:%d/%m/%Y} soumis "
                                "— il est en attente de validation.",
                            )
                        else:
                            messages.success(
                                request,
                                f"Rapport du {new_report.week_start:%d/%m/%Y} enregistré (brouillon).",
                            )
                    return redirect("sales:report_edit", pk=new_report.pk)

        # Erreur (formulaire ou section) : formsets liés pour conserver la saisie.
        if prospection_fs is None:
            prospection_fs = ProspectionFS(request.POST, instance=report)
            demos_fs = PhysicalDemoFormSet(request.POST, instance=report)
            contracts_fs = PilotContractFormSet(request.POST, instance=report)
        messages.error(
            request, "Formulaire non enregistré : corrigez les erreurs signalées."
        )
    else:
        prospection_fs = ProspectionFS(instance=report)
        demos_fs = PhysicalDemoFormSet(instance=report)
        contracts_fs = PilotContractFormSet(instance=report)

    # Libellés Lundi→Samedi pour la grille (la semaine connue après validation).
    week_ref = None
    if report_form.is_bound and "week_start" in report_form.data:
        try:
            week_ref = datetime.strptime(
                report_form.data.get("week_start") or "", "%Y-%m-%d"
            ).date()
        except ValueError:
            week_ref = None
    if week_ref is None and report.pk:
        week_ref = report.week_start
    if week_ref is None:
        today = timezone.localdate()
        week_ref = today - timedelta(days=today.weekday())
    day_labels = [
        f"{WEEKDAY_LABELS[i]} {(week_ref + timedelta(days=i)):%d/%m}"
        for i in range(6)
    ]
    # Paires (formulaire, libellé de jour) — les templates Django ne savent pas zipper.
    prosp_rows = list(zip(prospection_fs.forms, day_labels))

    breakdown = (
        report.commission_breakdown(settings_obj)
        if report.pk and report.status != WeeklyReport.Status.DRAFT
        else None
    )
    return render(
        request,
        "routing/sales_report_form.html",
        {
            "tab": "report",
            "report": report,
            "report_form": report_form,
            "prospection_fs": prospection_fs,
            "prosp_rows": prosp_rows,
            "demos_fs": demos_fs,
            "contracts_fs": contracts_fs,
            "day_labels": day_labels,
            "settings": settings_obj,
            "is_admin": is_admin,
            "is_new": pk is None,
            "breakdown": breakdown,
        },
    )


@login_required
@require_POST
def report_submit_view(request, pk):
    """Soumission rapide d'un rapport (bouton du dashboard)."""
    report = get_object_or_404(WeeklyReport, pk=pk)
    if not _can_edit_report(request.user, report):
        messages.error(request, "Action non autorisée.")
        return redirect("routing:dashboard")
    if report.status == WeeklyReport.Status.DRAFT:
        report.status = WeeklyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=["status", "submitted_at"])
        messages.success(request, "Rapport soumis — en attente de validation.")
    else:
        messages.info(request, "Ce rapport a déjà été soumis.")
    return redirect(request.META.get("HTTP_REFERER") or "sales:dashboard")


@login_required
@require_POST
def report_delete_view(request, pk):
    """Suppression d'un rapport : brouillon pour l'auteur, tout pour l'admin."""
    report = get_object_or_404(WeeklyReport, pk=pk)
    is_admin = user_has_sales_access(request.user)
    if not _can_edit_report(request.user, report):
        messages.error(request, "Action non autorisée.")
        return redirect("routing:dashboard")
    if not is_admin and report.status != WeeklyReport.Status.DRAFT:
        messages.error(request, "Seul un brouillon peut être supprimé par un commercial.")
        return redirect("sales:report_edit", pk=pk)
    report.delete()
    messages.success(request, "Rapport supprimé.")
    return redirect("sales:dashboard")


@login_required
@require_GET
def commission_list_view(request):
    """Onglet 3 (entrée) — liste des rapports soumis en attente de validation."""
    blocked = _require_sales_access(request)
    if blocked:
        return blocked
    pending = (
        WeeklyReport.objects.filter(status=WeeklyReport.Status.SUBMITTED)
        .select_related("sales_rep")
        .order_by("-week_start")
    )
    validated = (
        WeeklyReport.objects.filter(status=WeeklyReport.Status.VALIDATED)
        .select_related("sales_rep", "commission")
        .order_by("-week_start")[:20]
    )
    return render(
        request,
        "routing/sales_commission_list.html",
        {"tab": "commission", "pending": pending, "validated": validated},
    )


def _commission_preview(report, settings_obj):
    """Fiche calculée (non persistée) pour l'affichage du bloc de validation."""
    bd = report.commission_breakdown(settings_obj)
    return {
        "monthly_fixed": bd["monthly_fixed"],
        "fixed_week": bd["fixed_week"],
        "signatures": bd["signatures"],
        "signature_amount": bd["signature_amount"],
        "active_buses": bd["active_buses"],
        "bus_amount": bd["bus_amount"],
        "total": bd["total"],
    }


@login_required
@require_http_methods(["GET", "POST"])
def commission_validate_view(request, pk):
    """Onglet 3 — Bloc de validation & calculateur de commission (admin only)."""
    blocked = _require_sales_access(request)
    if blocked:
        return blocked
    report = get_object_or_404(
        WeeklyReport.objects.select_related("sales_rep").prefetch_related(
            "prospection_days", "demos", "contracts", "commission"
        ),
        pk=pk,
    )
    settings_obj = SalesSettings.get_solo()
    existing = getattr(report, "commission", None)

    if request.method == "POST":
        form = CommissionValidationForm(
            request.POST, instance=existing or CommissionValidation(report=report)
        )
        action = request.POST.get("action", "validate")
        if form.is_valid():
            if action == "validate" or existing is None:
                # Validation : calcul puis fige des snapshots de la fiche.
                bd = report.commission_breakdown(settings_obj)
                commission = form.save(commit=False)
                commission.report = report
                commission.signatures_count = bd["signatures"]
                commission.active_buses_count = bd["active_buses"]
                commission.fixed_amount = bd["fixed_week"]
                commission.signature_amount = bd["signature_amount"]
                commission.bus_amount = bd["bus_amount"]
                commission.total_amount = bd["total"]
                commission.validated_by = request.user
                commission.save()
                report.status = WeeklyReport.Status.VALIDATED
                report.validated_at = timezone.now()
                report.validated_by = request.user
                report.save(update_fields=["status", "validated_at", "validated_by"])
                messages.success(
                    request,
                    f"Fiche de commission générée : {commission.total_amount} $ "
                    f"({report.sales_rep}).",
                )
            else:
                form.save()
                messages.success(request, "Fiche de commission mise à jour.")
            return redirect("sales:commission_validate", pk=report.pk)
        messages.error(request, "Formulaire non enregistré : corrigez les erreurs.")
    else:
        initial = {
            "received_at": timezone.localdate(),
            "payment_status": CommissionValidation.PaymentStatus.PENDING,
        }
        if existing:
            initial = None
        form = CommissionValidationForm(instance=existing, initial=initial)

    return render(
        request,
        "routing/sales_commission.html",
        {
            "tab": "commission",
            "report": report,
            "form": form,
            "existing": existing,
            "preview": _commission_preview(report, settings_obj),
            "settings": settings_obj,
            "weekdays": day_labels_for(report),
        },
    )


def day_labels_for(report):
    return [
        f"{WEEKDAY_LABELS[i]} {(report.week_start + timedelta(days=i)):%d/%m}"
        for i in range(6)
    ]


# -------------------------------------------------------------------------- API JSON

def _error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _serialize_report(report, settings_obj=None):
    s = settings_obj or SalesSettings.get_solo()
    commission = getattr(report, "commission", None)
    return {
        "id": report.pk,
        "sales_rep": {"id": report.sales_rep_id, "name": str(report.sales_rep)},
        "week_start": report.week_start.isoformat(),
        "week_end": report.week_end.isoformat(),
        "status": report.status,
        "status_display": report.get_status_display(),
        "is_late": report.is_late,
        "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
        "totals": {
            "calls": report.total_contacted,
            "replies": report.total_replies,
            "appointments": report.total_appointments,
            "demos": report.demos_count,
            "signatures": report.signatures_count,
            "signed_buses": report.signed_bus_count,
            "estimated_amount": str(report.estimated_amount),
        },
        "attainment": report.attainment(s),
        "targets": {
            "calls": s.calls_target,
            "demos": s.demos_target,
            "signatures": s.signatures_target,
        },
        "prospection": [
            {
                "day": d.day.isoformat(),
                "ecoles_contactees": d.ecoles_contactees,
                "reponses_positives": d.reponses_positives,
                "rdv_fixes": d.rdv_fixes,
            }
            for d in report.prospection_days.all()
        ],
        "demos": [
            {
                "id": d.pk,
                "date": d.date.isoformat(),
                "school_name": d.school_name,
                "director_name": d.director_name,
                "duration_minutes": d.duration_minutes,
                "interest_level": d.interest_level,
                "next_action": d.next_action,
            }
            for d in report.demos.all()
        ],
        "contracts": [
            {
                "id": c.pk,
                "date": c.date.isoformat(),
                "school_name": c.school_name,
                "bus_count": c.bus_count,
                "signed": c.signed,
                "estimated_amount": str(c.estimated_amount),
            }
            for c in report.contracts.all()
        ],
        "comments": {
            "went_well": report.went_well,
            "difficulties": report.difficulties,
            "needs_next_week": report.needs_next_week,
        },
        "commission": (
            {
                "received_at": commission.received_at.isoformat(),
                "objectives_met": commission.objectives_met,
                "signatures_count": commission.signatures_count,
                "active_buses_count": commission.active_buses_count,
                "fixed_amount": str(commission.fixed_amount),
                "signature_amount": str(commission.signature_amount),
                "bus_amount": str(commission.bus_amount),
                "total_amount": str(commission.total_amount),
                "payment_status": commission.payment_status,
                "payment_status_display": commission.get_payment_status_display(),
                "paid_at": commission.paid_at.isoformat() if commission.paid_at else None,
            }
            if commission
            else None
        ),
    }


def _report_queryset(user):
    """Périmètre API : l'admin voit tout, un commercial les siens."""
    qs = WeeklyReport.objects.select_related(
        "sales_rep", "validated_by"
    ).prefetch_related("prospection_days", "demos", "contracts", "commission")
    if user_has_sales_access(user):
        return qs
    rep = _sales_rep_of(user)
    return qs.filter(sales_rep=rep) if rep else qs.none()


@login_required
@require_GET
def api_kpis_api(request):
    """GET /api/sales/kpis/ — KPI + lignes du tableau comparatif."""
    blocked = _require_sales_access(request)
    if blocked:
        return _error("Accès réservé aux rôles ADMIN et SUPER_ADMIN.", 403)
    data = _dashboard_data(request.GET, request.user)
    return JsonResponse(
        {
            "ok": True,
            "period": {
                "mode": data["filters"]["mode"],
                "start": data["filters"]["start"].isoformat(),
                "end": data["filters"]["end"].isoformat(),
                "label": data["filters"]["label"],
                "weeks": data["weeks"],
            },
            "kpis": {
                **data["kpis"],
                "commission": str(data["kpis"]["commission"]),
            },
            "rows": [
                {
                    "sales_rep": {"id": row["rep"].pk, "name": str(row["rep"])},
                    "report_id": row["report"].pk if row["report"] else None,
                    "realized": row["realized"],
                    "targets": row["targets"],
                    "attainment": row["attainment"],
                    "status": row["status_label"],
                    "status_tone": row["status_tone"],
                }
                for row in data["rows"]
            ],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def api_reports_api(request):
    """GET : liste filtrée. POST : création (payload JSON, validation réutilisée)."""
    if request.method == "GET":
        qs = _report_queryset(request.user)
        filters = _parse_filters(request.GET)
        qs = qs.filter(week_start__gte=filters["start"], week_start__lte=filters["end"])
        if request.GET.get("rep", "").isdigit():
            qs = qs.filter(sales_rep_id=int(request.GET["rep"]))
        if request.GET.get("status"):
            qs = qs.filter(status=request.GET["status"])
        settings_obj = SalesSettings.get_solo()
        return JsonResponse(
            {
                "ok": True,
                "reports": [_serialize_report(r, settings_obj) for r in qs],
            }
        )

    # POST — création depuis l'API
    try:
        payload = json_loads(request)
    except ValueError:
        return _error("JSON invalide.")
    cleaned, errors = _payload_to_forms(payload, request.user)
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    report, save_errors = _save_from_forms(cleaned, None)
    if save_errors:
        return JsonResponse({"ok": False, "errors": save_errors}, status=400)
    return JsonResponse({"ok": True, "report": _serialize_report(report)}, status=201)


@login_required
@require_http_methods(["GET", "PUT", "PATCH", "DELETE"])
def api_report_detail_api(request, pk):
    report = get_object_or_404(_report_queryset(request.user), pk=pk)

    if request.method == "GET":
        return JsonResponse({"ok": True, "report": _serialize_report(report)})

    if request.method == "DELETE":
        is_admin = user_has_sales_access(request.user)
        if not is_admin and report.status != WeeklyReport.Status.DRAFT:
            return _error("Seul un brouillon peut être supprimé.", 403)
        report.delete()
        return JsonResponse({"ok": True})

    if not _can_edit_report(request.user, report):
        return _error("Action non autorisée.", 403)

    try:
        payload = json_loads(request)
    except ValueError:
        return _error("JSON invalide.")
    if not isinstance(payload, dict):
        return _error("Objet JSON attendu.")
    cleaned, errors = _payload_to_forms(payload, request.user, report=report)
    if errors:
        return JsonResponse({"ok": False, "errors": errors}, status=400)
    updated, save_errors = _save_from_forms(cleaned, report)
    if save_errors:
        return JsonResponse({"ok": False, "errors": save_errors}, status=400)
    return JsonResponse({"ok": True, "report": _serialize_report(updated)})


@login_required
@require_POST
def api_report_validate_api(request, pk):
    """POST /api/sales/reports/<id>/validate/ — calcule et fige la fiche."""
    blocked = _require_sales_access(request)
    if blocked:
        return _error("Accès réservé aux rôles ADMIN et SUPER_ADMIN.", 403)
    report = get_object_or_404(WeeklyReport, pk=pk)
    try:
        payload = json_loads(request)
    except ValueError:
        payload = {}
    data = payload if isinstance(payload, dict) else {}
    settings_obj = SalesSettings.get_solo()

    form = CommissionValidationForm(
        data or None,
        instance=getattr(report, "commission", None)
        or CommissionValidation(report=report),
    )
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "errors": form.errors.get_json_data()}, status=400
        )
    bd = report.commission_breakdown(settings_obj)
    commission = form.save(commit=False)
    commission.report = report
    commission.signatures_count = bd["signatures"]
    commission.active_buses_count = bd["active_buses"]
    commission.fixed_amount = bd["fixed_week"]
    commission.signature_amount = bd["signature_amount"]
    commission.bus_amount = bd["bus_amount"]
    commission.total_amount = bd["total"]
    commission.validated_by = request.user
    commission.save()
    report.status = WeeklyReport.Status.VALIDATED
    report.validated_at = timezone.now()
    report.validated_by = request.user
    report.save(update_fields=["status", "validated_at", "validated_by"])
    return JsonResponse(
        {"ok": True, "commission": _serialize_report(report)["commission"]}
    )


# ------------------------------------------------- API : payload → formulaires

def json_loads(request):
    import json

    body = request.body.decode("utf-8") if request.body else "{}"
    return json.loads(body or "{}")


def _payload_to_forms(payload, user, report=None):
    """Valide un payload JSON de rapport via les formulaires existants.

    Retourne (cleaned, errors) ; cleaned contient les données prêtes à sauver.
    """
    errors = {}
    is_admin = user_has_sales_access(user)
    own_rep = _sales_rep_of(user)

    # Sections présentes dans le payload : seul elles seront remplacées (PATCH).
    present = {k: k in payload for k in (
        "prospection", "demos", "contracts",
        "went_well", "difficulties", "needs_next_week",
    )}
    # Semaine d'origine AVANT validation (is_valid() mute l'instance).
    original_week = report.week_start if report is not None and report.pk else None

    sales_rep = payload.get("sales_rep")
    if report is not None and sales_rep is None:
        sales_rep = report.sales_rep_id
    if not is_admin:
        sales_rep = own_rep.pk if own_rep else None
    if not sales_rep:
        errors["sales_rep"] = ["Commercial requis."]
        return None, errors

    try:
        rep_obj = SalesRep.objects.get(pk=sales_rep, is_active=True)
    except (SalesRep.DoesNotExist, TypeError, ValueError):
        errors["sales_rep"] = ["Commercial introuvable ou inactif."]
        return None, errors

    def _value(key, default=""):
        """Valeur du payload si présente, sinon valeur existante (PATCH safe)."""
        if key in payload:
            v = payload[key]
            return "" if v is None else str(v)
        if report is not None and hasattr(report, key):
            return getattr(report, key) or default
        return default

    form_data = {
        "sales_rep": str(rep_obj.pk),
        "week_start": payload.get("week_start")
        or (report.week_start.isoformat() if report is not None and report.pk else ""),
        "went_well": _value("went_well"),
        "difficulties": _value("difficulties"),
        "needs_next_week": _value("needs_next_week"),
    }
    report_form = WeeklyReportForm(
        form_data,
        instance=report or WeeklyReport(),
        rep_queryset=SalesRep.objects.filter(is_active=True),
        restrict_rep=None if is_admin else own_rep,
    )
    if not report_form.is_valid():
        errors.update(report_form.errors.get_json_data())
        return None, errors

    week_start = report_form.cleaned_data["week_start"]
    dup = WeeklyReport.objects.filter(sales_rep=rep_obj, week_start=week_start)
    if report is not None and report.pk:
        dup = dup.exclude(pk=report.pk)
    if dup.exists():
        errors["week_start"] = ["Un rapport existe déjà pour cette semaine."]
        return None, errors

    return {
        "report_form": report_form,
        "report": report,
        "sales_rep": rep_obj,
        "week_start": week_start,
        "original_week": original_week,
        "present": present,
        "prospection": payload.get("prospection") or [],
        "demos": payload.get("demos") or [],
        "contracts": payload.get("contracts") or [],
        "went_well": _value("went_well"),
        "difficulties": _value("difficulties"),
        "needs_next_week": _value("needs_next_week"),
    }, None


def _save_from_forms(cleaned, report):
    """Persiste un rapport + ses sections (API) — valide TOUT avant écriture.

    Sémantique de remplacement : les sections fournies dans le payload
    remplacent intégralement les lignes existantes (les absentes sont laissées
    telles quelles). Retourne (target, None) ou (None, errors).
    """
    present = cleaned["present"]
    rf = cleaned["report_form"]
    target = rf.save(commit=False)  # écriture mémoire seule

    # --- Validation de toutes les sections AVANT toute écriture DB ----------
    week_changed = (
        cleaned["original_week"] is not None
        and cleaned["original_week"] != cleaned["week_start"]
    )
    rebuild_prospection = present["prospection"] or week_changed

    formsets = {}
    if rebuild_prospection:
        rows = [
            _prospection_row(row)
            for row in list(cleaned["prospection"])[:6]
        ]
        data = _formset_data("prospection", rows,
                             ["ecoles_contactees", "reponses_positives", "rdv_fixes"])
        fs = _prospection_formset(0)(data, instance=target)
        if not fs.is_valid():
            return None, fs.errors.get_json_data()
        formsets["prospection"] = (fs, rows)

    for cls, key, fields in (
        (PhysicalDemoFormSet, "demos", ["date", "school_name", "director_name",
                                        "duration_minutes", "interest_level", "next_action"]),
        (PilotContractFormSet, "contracts", ["date", "school_name", "bus_count", "signed"]),
    ):
        if not present[key]:
            continue
        rows = [r for r in cleaned[key] if _row_has_content(r)][:100]
        data = _formset_data(cls().prefix, rows, fields)
        fs = cls(data, instance=target)
        if not fs.is_valid():
            return None, fs.errors.get_json_data()
        formsets[key] = (fs, rows)

    # --- Écriture atomique ---------------------------------------------------
    with transaction.atomic():
        target.save()

        if rebuild_prospection:
            # Remplacement intégral : pas de collision d'unicité (report, day).
            target.prospection_days.all().delete()
            fs, rows = formsets["prospection"]
            for i, form in enumerate(fs.forms):
                form.instance.day = target.week_start + timedelta(days=i)
            fs.save()

        for key in ("demos", "contracts"):
            if key in formsets:
                getattr(target, key).all().delete()
                formsets[key][0].save()

        target.save(update_fields=["went_well", "difficulties", "needs_next_week"])

    return (
        WeeklyReport.objects.select_related("sales_rep")
        .prefetch_related("prospection_days", "demos", "contracts", "commission")
        .get(pk=target.pk)
    ), None


def _prospection_row(row):
    """Normalise une ligne de prospection (entiers ≥ 0, vides → 0)."""
    out = {}
    for key in ("ecoles_contactees", "reponses_positives", "rdv_fixes"):
        value = row.get(key, 0)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        out[key] = max(value, 0)
    return out


def _row_has_content(row):
    """True si la ligne contient au moins une valeur non vide (ignore id/delete)."""
    skip = {"id", "DELETE"}
    return any(
        str(v).strip() not in ("", "None", "False")
        for k, v in (row or {}).items()
        if k not in skip
    )


def _formset_data(prefix, rows, fields=None):
    """Construit les données d'un formset (management form incluse)."""
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        if fields is None:
            fields = ["ecoles_contactees", "reponses_positives", "rdv_fixes"]
        for f in fields:
            value = row.get(f, "")
            if isinstance(value, bool):
                value = "on" if value else ""
            data[f"{prefix}-{i}-{f}"] = "" if value is None else str(value)
    return data
