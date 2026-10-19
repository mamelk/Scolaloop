"""Exports du suivi commercial : Excel, PDF et CSV des rapports hebdomadaires."""

import io
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import SalesSettings, WeeklyReport
from .sales_views import (
    WEEKDAY_LABELS,
    _can_edit_report,
    _dashboard_data,
    _require_sales_access,
)


def _week_label(report):
    return f"du {report.week_start:%d/%m/%Y} au {report.week_end:%d/%m/%Y}"


def _report_filename(report, ext):
    name = f"rapport_{report.sales_rep.full_name}_{report.week_start.isoformat()}"
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) + f".{ext}"


# ------------------------------------------------------------------ Excel (XLSX)

def build_report_workbook(report, settings_obj=None):
    """Classeur Excel complet d'un rapport hebdomadaire (toutes sections)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    s = settings_obj or SalesSettings.get_solo()
    header_fill = PatternFill("solid", fgColor="2563EB")
    section_fill = PatternFill("solid", fgColor="E2E8F0")

    def _style_header(ws):
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

    wb = Workbook()

    # Feuille 1 — Synthèse
    ws = wb.active
    ws.title = "Synthèse"
    ws.append(["Commercial", str(report.sales_rep)])
    ws.append(["Semaine", _week_label(report)])
    ws.append(["Statut", report.get_status_display()])
    ws.append([])
    ws.append(["Indicateur", "Cible", "Réalisé", "Écart"])
    for cell in ws[5]:
        cell.font = Font(bold=True)
        cell.fill = section_fill
    rows = [
        ("Appels / écoles contactées", s.calls_target, report.total_contacted),
        ("Démos physiques", s.demos_target, report.demos_count),
        ("Signatures", s.signatures_target, report.signatures_count),
    ]
    for label, target, realized in rows:
        ws.append([label, target, realized, realized - target])
    ws.append(["Réponses positives", "", report.total_replies])
    ws.append(["Rendez-vous fixés", "", report.total_appointments])
    ws.append(["Bus actifs générés (cumul)", "", report.active_buses_cumulated()])
    ws.append(["Montant estimé contrats signés ($)", "", float(report.estimated_amount)])
    ws.append(["Taux d'atteinte global (%)", "", report.attainment(s)])
    widths = [36, 12, 12, 10]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    # Feuille 2 — Prospection (grille Lundi→Samedi)
    ws = wb.create_sheet("Prospection")
    ws.append(["Jour", "Date", "Écoles contactées", "Réponses positives", "RDV fixés"])
    _style_header(ws)
    days = {d.day: d for d in report.prospection_days.all()}
    for i in range(6):
        day = report.week_start + timedelta(days=i)
        obj = days.get(day)
        ws.append(
            [
                WEEKDAY_LABELS[i],
                day.strftime("%d/%m/%Y"),
                obj.ecoles_contactees if obj else 0,
                obj.reponses_positives if obj else 0,
                obj.rdv_fixes if obj else 0,
            ]
        )
    ws.append(
        ["TOTAL", "", report.total_contacted, report.total_replies, report.total_appointments]
    )
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for i, width in enumerate([14, 14, 20, 20, 14], start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    # Feuille 3 — Démos physiques
    ws = wb.create_sheet("Démos")
    ws.append(
        ["Date", "École", "Directeur rencontré", "Durée (min)", "Intérêt (1-10)", "Prochaine action"]
    )
    _style_header(ws)
    for d in report.demos.all():
        ws.append(
            [
                d.date.strftime("%d/%m/%Y"),
                d.school_name,
                d.director_name,
                d.duration_minutes,
                d.interest_level,
                d.next_action,
            ]
        )
    if not report.demos.exists():
        ws.append(["Aucune démonstration enregistrée pour cette semaine."])
    for i, width in enumerate([14, 28, 24, 14, 16, 32], start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    # Feuille 4 — Contrats pilotes
    ws = wb.create_sheet("Contrats")
    ws.append(["Date", "École", "Nb bus", "Signé", "Montant estimé ($)"])
    _style_header(ws)
    for c in report.contracts.all():
        ws.append(
            [
                c.date.strftime("%d/%m/%Y"),
                c.school_name,
                c.bus_count,
                "Oui" if c.signed else "Non",
                float(c.estimated_amount),
            ]
        )
    if not report.contracts.exists():
        ws.append(["Aucun contrat enregistré pour cette semaine."])
    else:
        ws.append(
            ["TOTAL SIGNÉS", "", report.signed_bus_count, "", float(report.estimated_amount)]
        )
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    for i, width in enumerate([14, 28, 10, 10, 20], start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    # Feuille 5 — Commentaires
    ws = wb.create_sheet("Commentaires")
    ws.append(["Ce qui a bien marché"])
    ws[1][0].font = Font(bold=True)
    ws.append([report.went_well or "—"])
    ws.append([])
    ws.append(["Difficultés rencontrées"])
    ws[4][0].font = Font(bold=True)
    ws.append([report.difficulties or "—"])
    ws.append([])
    ws.append(["Besoins pour la semaine prochaine"])
    ws[7][0].font = Font(bold=True)
    ws.append([report.needs_next_week or "—"])
    ws.column_dimensions["A"].width = 80

    # Feuille 6 — Commission (si fiche existante)
    commission = getattr(report, "commission", None)
    if commission is not None:
        ws = wb.create_sheet("Commission")
        ws.append(["Poste", "Détail", "Montant ($)"])
        _style_header(ws)
        ws.append(
            [
                "Fixe mensuel",
                f"{commission.fixed_amount} $ (part hebdomadaire du fixe)",
                float(commission.fixed_amount),
            ]
        )
        ws.append(
            [
                "Commission signatures",
                f"{s.commission_per_signature} $ × {commission.signatures_count} école(s)",
                float(commission.signature_amount),
            ]
        )
        ws.append(
            [
                "Commission bus actifs",
                f"{s.commission_per_active_bus} $ × {commission.active_buses_count} bus",
                float(commission.bus_amount),
            ]
        )
        ws.append(["TOTAL À PAYER", "", float(commission.total_amount)])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
        ws.append([])
        ws.append(["Statut paiement", commission.get_payment_status_display(), ""])
        ws.append(
            ["Objectifs atteints", "OUI" if commission.objectives_met else "NON", ""]
        )
        ws.append(
            ["Réçu le", commission.received_at.strftime("%d/%m/%Y"), ""]
        )
        for i, width in enumerate([26, 46, 16], start=1):
            ws.column_dimensions[chr(64 + i)].width = width

    return wb


@login_required
@require_GET
def report_export_xlsx(request, pk):
    """GET /admin/sales-tracking/rapport/<pk>/export.xlsx — Excel complet."""
    report = get_object_or_404(
        WeeklyReport.objects.select_related("sales_rep").prefetch_related(
            "prospection_days", "demos", "contracts", "commission"
        ),
        pk=pk,
    )
    if not _can_edit_report(request.user, report):
        messages_error = "Accès refusé : ce rapport ne vous appartient pas."
        from django.contrib import messages

        messages.error(request, messages_error)
        return redirect_home(request)

    wb = build_report_workbook(report)
    response = HttpResponse(
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{_report_filename(report, "xlsx")}"'
    )
    wb.save(response)
    return response


def redirect_home(request):
    from django.shortcuts import redirect

    return redirect("routing:dashboard")


# ----------------------------------------------------------------------- PDF

@login_required
@require_GET
def report_export_pdf(request, pk):
    """GET /admin/sales-tracking/rapport/<pk>/export.pdf — fiche hebdo imprimable."""
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from django.contrib import messages

    report = get_object_or_404(
        WeeklyReport.objects.select_related("sales_rep").prefetch_related(
            "prospection_days", "demos", "contracts", "commission"
        ),
        pk=pk,
    )
    if not _can_edit_report(request.user, report):
        messages.error(request, "Accès refusé : ce rapport ne vous appartient pas.")
        return redirect_home(request)

    s = SalesSettings.get_solo()

    # Police Vera embarquée (Unicode + accents français), comme route_sheet_pdf.
    vera_dir = __import__("os").path.join(__import__("os").path.dirname(reportlab.__file__), "fonts")
    try:
        pdfmetrics.registerFont(TTFont("Vera", __import__("os").path.join(vera_dir, "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("VeraBd", __import__("os").path.join(vera_dir, "VeraBd.ttf")))
    except Exception:
        pass

    INDIGO = colors.HexColor("#2563EB")
    DARK = colors.HexColor("#1E293B")
    GRAY = colors.HexColor("#64748B")
    WHITE = colors.white
    GREEN = colors.HexColor("#16A34A")

    st_title = ParagraphStyle("title", fontName="VeraBd", fontSize=16, textColor=INDIGO, alignment=TA_LEFT)
    st_sub = ParagraphStyle("sub", fontName="Vera", fontSize=9, textColor=GRAY, alignment=TA_LEFT)
    st_h2 = ParagraphStyle("h2", fontName="VeraBd", fontSize=11, textColor=INDIGO, spaceBefore=8, spaceAfter=4)
    st_cell = ParagraphStyle("cell", fontName="Vera", fontSize=8.5, textColor=DARK)
    st_cell_b = ParagraphStyle("cellb", fontName="VeraBd", fontSize=8.5, textColor=DARK)
    st_hdr = ParagraphStyle("hdr", fontName="VeraBd", fontSize=8.5, textColor=WHITE, alignment=TA_CENTER)
    st_total = ParagraphStyle("total", fontName="VeraBd", fontSize=10, textColor=GREEN, alignment=TA_RIGHT)

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Vera", 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(
            14 * mm, 8 * mm,
            "Document généré par ScolaLoop - Suivi Commercial (ESTECH)",
        )
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=f"Rapport commercial {report.sales_rep} {report.week_start}",
        author="ScolaLoop",
    )

    def _table(header, rows, widths):
        data = [[Paragraph(h, st_hdr) for h in header]]
        for row in rows:
            data.append(
                [
                    cell if not isinstance(cell, str) else Paragraph(cell, st_cell)
                    for cell in row
                ]
            )
        table = Table(data, colWidths=widths, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, colors.HexColor("#F8FAFC")]),
                ]
            )
        )
        return table

    story = []
    story.append(Paragraph("Rapport hebdomadaire — Suivi Commercial", st_title))
    story.append(
        Paragraph(
            f"<b>{report.sales_rep}</b> · Semaine {_week_label(report)} · "
            f"Statut : {report.get_status_display()}",
            st_sub,
        )
    )
    story.append(Spacer(1, 6))

    # Synthèse
    story.append(Paragraph("1. Synthèse des objectifs", st_h2))
    synth = [
        ("Appels / écoles contactées", s.calls_target, report.total_contacted),
        ("Démos physiques", s.demos_target, report.demos_count),
        ("Signatures de contrats", s.signatures_target, report.signatures_count),
    ]
    story.append(
        _table(
            ["Indicateur", "Cible", "Réalisé", "Écart"],
            [
                [label, str(target), str(realized), str(realized - target)]
                for label, target, realized in synth
            ],
            [70 * mm, 30 * mm, 30 * mm, 30 * mm],
        )
    )
    story.append(
        Paragraph(
            f"Taux d'atteinte global : {report.attainment(s)} % — "
            f"Bus actifs générés (cumul) : {report.active_buses_cumulated()}",
            st_cell,
        )
    )

    # Prospection
    story.append(Paragraph("2. Prospection (appels + WhatsApp)", st_h2))
    days = {d.day: d for d in report.prospection_days.all()}
    prosp_rows = []
    for i in range(6):
        day = report.week_start + timedelta(days=i)
        obj = days.get(day)
        prosp_rows.append(
            [
                WEEKDAY_LABELS[i],
                day.strftime("%d/%m"),
                str(obj.ecoles_contactees if obj else 0),
                str(obj.reponses_positives if obj else 0),
                str(obj.rdv_fixes if obj else 0),
            ]
        )
    prosp_rows.append(
        ["TOTAL", "", str(report.total_contacted), str(report.total_replies), str(report.total_appointments)]
    )
    story.append(
        _table(
            ["Jour", "Date", "Écoles", "Réponses +", "RDV"],
            prosp_rows,
            [30 * mm, 25 * mm, 35 * mm, 35 * mm, 35 * mm],
        )
    )

    # Démos
    story.append(Paragraph("3. Démonstrations physiques", st_h2))
    if report.demos.exists():
        story.append(
            _table(
                ["Date", "École", "Directeur", "Durée", "Intérêt", "Prochaine action"],
                [
                    [
                        d.date.strftime("%d/%m"),
                        d.school_name,
                        d.director_name or "—",
                        f"{d.duration_minutes} min",
                        str(d.interest_level),
                        d.next_action or "—",
                    ]
                    for d in report.demos.all()
                ],
                [18 * mm, 38 * mm, 33 * mm, 20 * mm, 17 * mm, 39 * mm],
            )
        )
    else:
        story.append(Paragraph("Aucune démonstration enregistrée.", st_cell))

    # Contrats
    story.append(Paragraph("4. Signatures (contrats pilotes)", st_h2))
    if report.contracts.exists():
        story.append(
            _table(
                ["Date", "École", "Nb bus", "Signé", "Montant estimé"],
                [
                    [
                        c.date.strftime("%d/%m"),
                        c.school_name,
                        str(c.bus_count),
                        "Oui" if c.signed else "Non",
                        f"{c.estimated_amount} $",
                    ]
                    for c in report.contracts.all()
                ],
                [20 * mm, 55 * mm, 20 * mm, 22 * mm, 48 * mm],
            )
        )
        story.append(
            Paragraph(
                f"Total signé : {report.signatures_count} contrat(s) — "
                f"{report.signed_bus_count} bus — {report.estimated_amount} $ estimés",
                st_total,
            )
        )
    else:
        story.append(Paragraph("Aucun contrat enregistré.", st_cell))

    # Commentaires
    story.append(Paragraph("5. Commentaires & retours terrain", st_h2))
    for label, value in (
        ("Ce qui a bien marché", report.went_well),
        ("Difficultés rencontrées", report.difficulties),
        ("Besoins pour la semaine prochaine", report.needs_next_week),
    ):
        story.append(Paragraph(f"<b>{label} :</b> {value or '—'}", st_cell))

    # Commission
    commission = getattr(report, "commission", None)
    if commission is not None:
        story.append(Paragraph("6. Fiche de commission", st_h2))
        story.append(
            _table(
                ["Poste", "Détail", "Montant"],
                [
                    ["Fixe mensuel", f"part hebdo ({commission.fixed_amount} $)", f"{commission.fixed_amount} $"],
                    [
                        "Commission signatures",
                        f"{s.commission_per_signature} $ × {commission.signatures_count}",
                        f"{commission.signature_amount} $",
                    ],
                    [
                        "Commission bus actifs",
                        f"{s.commission_per_active_bus} $ × {commission.active_buses_count}",
                        f"{commission.bus_amount} $",
                    ],
                    ["TOTAL À PAYER", "", f"{commission.total_amount} $"],
                ],
                [50 * mm, 70 * mm, 45 * mm],
            )
        )
        story.append(
            Paragraph(
                f"Statut : {commission.get_payment_status_display()} · "
                f"Objectifs atteints : {'OUI' if commission.objectives_met else 'NON'} · "
                f"Reçu le {commission.received_at:%d/%m/%Y}",
                st_cell,
            )
        )

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    pdf_bytes = buf.getvalue()
    buf.close()

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{_report_filename(report, "pdf")}"'
    )
    return response


# ----------------------------------------------------------------------- CSV

@login_required
@require_GET
def reports_export_csv(request):
    """GET /admin/sales-tracking/export.csv — tableau comparatif de la période."""
    blocked = _require_sales_access(request)
    if blocked:
        return blocked
    import csv as csv_mod

    data = _dashboard_data(request.GET, request.user)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="suivi_commercial_{data["filters"]["start"]}.csv"'
    )
    response.write("\ufeff")  # BOM Excel

    writer = csv_mod.writer(response, delimiter=";")
    writer.writerow(
        [
            "Commercial",
            "Semaine / période",
            "Appels",
            "Réponses +",
            "RDV",
            "Démos",
            "Signatures",
            "Bus actifs",
            "Cible appels",
            "Cible démos",
            "Cible signatures",
            "Taux atteinte %",
            "Statut rapport",
        ]
    )
    # Ligne par rapport de la période (détail), plus les totaux par commercial.
    reports_by_rep = {}
    for r in data["reports"]:
        reports_by_rep.setdefault(r.sales_rep_id, []).append(r)
    for row in data["rows"]:
        rep = row["rep"]
        rep_reports = reports_by_rep.get(rep.pk, [])
        if rep_reports:
            for r in rep_reports:
                writer.writerow(
                    [
                        str(rep),
                        f"Semaine du {r.week_start:%d/%m/%Y}",
                        r.total_contacted,
                        r.total_replies,
                        r.total_appointments,
                        r.demos_count,
                        r.signatures_count,
                        r.signed_bus_count,
                        row["targets"]["calls"] // max(data["weeks"], 1),
                        row["targets"]["demos"] // max(data["weeks"], 1),
                        row["targets"]["signatures"] // max(data["weeks"], 1),
                        r.attainment(data["settings"]),
                        r.get_status_display(),
                    ]
                )
        else:
            writer.writerow(
                [
                    str(rep),
                    data["filters"]["label"],
                    0, 0, 0, 0, 0, 0,
                    row["targets"]["calls"],
                    row["targets"]["demos"],
                    row["targets"]["signatures"],
                    0,
                    row["status_label"],
                ]
            )
    writer.writerow([])
    kpis = data["kpis"]
    writer.writerow(
        [
            "TOTAL PÉRIODE",
            data["filters"]["label"],
            kpis["calls"],
            "",
            kpis["appointments"],
            kpis["demos"],
            kpis["signatures"],
            kpis["buses"],
            "",
            "",
            "",
            "",
            f"Commission : {kpis['commission']} $",
        ]
    )
    return response
