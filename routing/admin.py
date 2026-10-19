from django.contrib import admin

from .models import (
    Absence,
    Bus,
    BusMaintenance,
    BusStop,
    CommissionValidation,
    GPSLog,
    Incident,
    PhysicalDemo,
    PilotContract,
    ProspectionDay,
    Route,
    RouteStop,
    School,
    SalesRep,
    SalesSettings,
    Student,
    WeeklyReport,
)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = (
        "code_ecole", "name", "is_active", "user",
        "latitude", "longitude", "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("code_ecole", "name", "address", "user__username")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "matricule", "prenom", "postnom", "nom", "address",
        "parent_phone", "is_active", "is_frozen", "is_taken",
    )
    list_filter = ("is_active", "is_frozen", "is_taken")
    search_fields = ("matricule", "prenom", "postnom", "nom", "address", "parent_phone")


@admin.register(Bus)
class BusAdmin(admin.ModelAdmin):
    list_display = ("code_bus", "capacity", "driver_name", "driver_user", "is_in_service")
    list_filter = ("is_in_service",)
    search_fields = ("code_bus", "driver_name", "driver_user__username")


@admin.register(BusStop)
class BusStopAdmin(admin.ModelAdmin):
    list_display = ("name", "latitude", "longitude")
    search_fields = ("name",)


class RouteStopInline(admin.TabularInline):
    model = RouteStop
    extra = 1


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "bus",
        "school",
        "total_distance_km",
        "estimated_duration_minutes",
        "students_taken",
        "students_remaining",
        "created_at",
    )
    list_filter = ("bus", "school")
    search_fields = ("name",)
    inlines = [RouteStopInline]


@admin.register(RouteStop)
class RouteStopAdmin(admin.ModelAdmin):
    list_display = ("route", "stop", "order")
    list_filter = ("route",)
    ordering = ("route", "order")


@admin.register(Absence)
class AbsenceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "reason", "created_at")
    list_filter = ("date",)
    search_fields = ("student__matricule", "student__nom", "reason")


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ("bus", "type_incident", "timestamp", "resolved")
    list_filter = ("type_incident", "resolved", "bus")
    search_fields = ("bus__code_bus", "description")


@admin.register(GPSLog)
class GPSLogAdmin(admin.ModelAdmin):
    list_display = ("bus", "latitude", "longitude", "speed_kmh", "timestamp")
    list_filter = ("bus",)


@admin.register(BusMaintenance)
class BusMaintenanceAdmin(admin.ModelAdmin):
    list_display = (
        "bus", "service_type", "cost", "date_effectuee", "prochaine_echeance_km_ou_date",
    )
    list_filter = ("service_type", "bus")


# ---------------------------------------------------------------- Suivi commercial

@admin.register(SalesRep)
class SalesRepAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "email", "is_active", "fixed_monthly_salary", "user")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone", "email", "user__username")


@admin.register(SalesSettings)
class SalesSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "calls_target", "demos_target", "signatures_target", "monthly_fixed_amount",
        "commission_per_signature", "commission_per_active_bus", "pilot_bus_amount",
    )

    def has_add_permission(self, request):
        # Singleton : pas de deuxième instance.
        return not SalesSettings.objects.filter(pk=1).exists() and super().has_add_permission(request)


class ProspectionDayInline(admin.TabularInline):
    model = ProspectionDay
    extra = 0


class PhysicalDemoInline(admin.TabularInline):
    model = PhysicalDemo
    extra = 0


class PilotContractInline(admin.TabularInline):
    model = PilotContract
    extra = 0


@admin.register(WeeklyReport)
class WeeklyReportAdmin(admin.ModelAdmin):
    list_display = ("sales_rep", "week_start", "status", "submitted_at", "validated_by")
    list_filter = ("status", "sales_rep")
    search_fields = ("sales_rep__full_name",)
    inlines = [ProspectionDayInline, PhysicalDemoInline, PilotContractInline]


@admin.register(CommissionValidation)
class CommissionValidationAdmin(admin.ModelAdmin):
    list_display = (
        "report", "received_at", "objectives_met", "total_amount",
        "payment_status", "paid_at", "validated_by",
    )
    list_filter = ("payment_status", "objectives_met")
