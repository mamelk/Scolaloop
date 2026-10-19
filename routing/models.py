from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


LATITUDE_VALIDATORS = [MinValueValidator(-90.0), MaxValueValidator(90.0)]
LONGITUDE_VALIDATORS = [MinValueValidator(-180.0), MaxValueValidator(180.0)]


def _local_date():
    """Date locale du jour (utilisée comme défaut de champ sérialisable)."""
    return timezone.localdate()


class School(models.Model):
    """École : point de départ/retour des trajets et conteneur des données."""
    code_ecole = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(validators=LATITUDE_VALIDATORS)
    longitude = models.FloatField(validators=LONGITUDE_VALIDATORS)
    is_active = models.BooleanField(
        default=True,
        help_text="False si l'école est désactivée par l'administration générale "
        "(accès bloqué, mais données conservées).",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school",
        help_text="Compte administrateur de l'école (identifiant = code_ecole).",
    )
    created_at = models.DateTimeField(
        auto_now_add=True, null=True, blank=True, help_text="Date de création de l'école."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code_ecole})"


class Student(models.Model):
    matricule = models.CharField(max_length=50, unique=True)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="students",
    )
    must_change_password = models.BooleanField(
        default=True,
        help_text="True si l'élève doit personnaliser son mot de passe à la première connexion.",
    )
    prenom = models.CharField(max_length=100)
    postnom = models.CharField(max_length=100, blank=True)
    nom = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    latitude = models.FloatField(
        validators=LATITUDE_VALIDATORS,
        null=True,
        blank=True,
        help_text="Position GPS du domicile, définie par le parent lors de sa première connexion.",
    )
    longitude = models.FloatField(
        validators=LONGITUDE_VALIDATORS,
        null=True,
        blank=True,
        help_text="Position GPS du domicile, définie par le parent lors de sa première connexion.",
    )
    is_active = models.BooleanField(default=True)
    is_frozen = models.BooleanField(default=False)
    is_taken = models.BooleanField(
        default=False,
        help_text="True si l'élève a été pris en charge par le bus (ramassage confirmé).",
    )
    overcapacity_alert = models.BooleanField(
        default=False,
        help_text="True si l'élève a une position GPS mais qu'aucun bus actif de son école "
        "n'a de capacité disponible (tous les bus proches sont complets).",
    )
    parent_notified = models.BooleanField(
        default=False,
        help_text="True si un SMS d'approche (< 100 m) a déjà été envoyé au parent.",
    )
    parent_phone = models.CharField(max_length=20, blank=True, help_text="Téléphone du parent (ex. +243...).")
    parent_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        help_text="Compte de connexion du parent (facultatif — l'élève peut se connecter avec son matricule).",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student",
        help_text="Compte de connexion associé (matricule = identifiant).",
    )
    assigned_route = models.ForeignKey(
        "Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    class Meta:
        ordering = ["nom", "prenom"]

    def __str__(self):
        return " ".join(filter(None, [self.prenom, self.postnom, self.nom]))


class Bus(models.Model):
    code_bus = models.CharField(max_length=50)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="buses",
    )
    must_change_password = models.BooleanField(
        default=True,
        help_text="True si le chauffeur doit personnaliser son mot de passe à la première connexion.",
    )
    capacity = models.IntegerField()
    driver_name = models.CharField(max_length=100, blank=True)
    is_in_service = models.BooleanField(default=True)
    last_latitude = models.FloatField(
        null=True, blank=True, help_text="Dernière latitude GPS reçue du téléphone du chauffeur."
    )
    last_longitude = models.FloatField(
        null=True, blank=True, help_text="Dernière longitude GPS reçue du téléphone du chauffeur."
    )
    last_position_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Horodatage de la dernière position GPS reçue."
    )
    speed_kmh = models.FloatField(
        default=0.0,
        help_text="Dernière vitesse connue du bus (km/h), envoyée par le téléphone du chauffeur.",
    )
    driver_connected = models.BooleanField(
        default=False,
        help_text="True si le chauffeur est connecté (le bus apparaît alors en direct "
        "sur la carte de l'école ; il disparaît à la déconnexion).",
    )
    driver_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="driven_bus",
        help_text="Compte de connexion du chauffeur associé à ce bus.",
    )

    def __str__(self):
        return self.code_bus


class BusStop(models.Model):
    name = models.CharField(max_length=100)
    latitude = models.FloatField(validators=LATITUDE_VALIDATORS)
    longitude = models.FloatField(validators=LONGITUDE_VALIDATORS)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Route(models.Model):
    name = models.CharField(max_length=100)
    bus = models.ForeignKey(
        Bus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routes",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routes",
    )
    total_distance_km = models.FloatField(default=0.0)
    estimated_duration_minutes = models.FloatField(default=0.0)
    students_taken = models.PositiveIntegerField(default=0)
    students_remaining = models.PositiveIntegerField(default=0)
    path_geometry = models.JSONField(
        null=True,
        blank=True,
        help_text="Points [lat, lon] de l'itinéraire routier réel (OSRM), au format JSON.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RouteStop(models.Model):
    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name="stops",
    )
    stop = models.ForeignKey(BusStop, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ["route", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["route", "order"],
                name="unique_stop_order_per_route",
            ),
        ]

    def __str__(self):
        return f"{self.route} — {self.stop} (#{self.order})"


class Absence(models.Model):
    """Absence déclarée par le parent : l'élève est exclu des tournées le jour même."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="absences")
    date = models.DateField(default=_local_date)
    reason = models.CharField(max_length=255, blank=True, help_text="Motif optionnel (ex : maladie).")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["student", "date"], name="unique_absence_per_day"),
        ]

    def __str__(self):
        return f"Absence {self.student} — {self.date}"


class Incident(models.Model):
    """Incident signalé par le chauffeur ou détecté automatiquement (excès de vitesse)."""
    TYPE_EMBOUTEILLAGE = "Embouteillage"
    TYPE_PANNE = "Panne"
    TYPE_ACCIDENT = "Accident"
    TYPE_VITESSE = "Excès de vitesse"
    TYPE_AUTRE = "Autre"
    INCIDENT_TYPES = [
        (TYPE_EMBOUTEILLAGE, "Embouteillage"),
        (TYPE_PANNE, "Panne"),
        (TYPE_ACCIDENT, "Accident"),
        (TYPE_VITESSE, "Excès de vitesse"),
        (TYPE_AUTRE, "Autre"),
    ]

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="incidents")
    type_incident = models.CharField(max_length=30, choices=INCIDENT_TYPES)
    description = models.TextField(blank=True, help_text="Description libre (optionnelle).")
    timestamp = models.DateTimeField(
        default=timezone.now,
        help_text="Horodatage fourni par le téléphone du chauffeur "
        "(sync offline) ; heure actuelle si absent.",
    )
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.type_incident} — {self.bus.code_bus}"


class GPSLog(models.Model):
    """Point de passage GPS enregistré à chaque mise à jour de position du bus."""
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="gps_logs")
    latitude = models.FloatField()
    longitude = models.FloatField()
    speed_kmh = models.FloatField(default=0.0, help_text="Vitesse instantanée estimée (km/h).")
    timestamp = models.DateTimeField(
        default=timezone.now,
        help_text="Horodatage du point GPS fourni par le téléphone du chauffeur "
        "(sync offline) ; heure actuelle si absent.",
    )

    class Meta:
        ordering = ["timestamp"]
        indexes = [models.Index(fields=["bus", "timestamp"])]

    def __str__(self):
        return f"GPS {self.bus.code_bus} @ {self.timestamp:%H:%M:%S}"


class BusMaintenance(models.Model):
    """Intervention d'entretien / révision technique sur un bus."""
    SERVICE_TYPES = [
        ("Vidange", "Vidange"),
        ("Freins", "Freins"),
        ("Pneumatiques", "Pneumatiques"),
        ("Contrôle technique", "Contrôle technique"),
        ("Autre", "Autre"),
    ]

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="maintenance_records")
    service_type = models.CharField(max_length=30, choices=SERVICE_TYPES)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    date_effectuee = models.DateField(default=_local_date)
    prochaine_echeance_km_ou_date = models.CharField(
        max_length=100, blank=True, help_text="Ex : 5000 km ou 15/12/2026."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_effectuee", "-created_at"]

    def __str__(self):
        return f"{self.service_type} — {self.bus.code_bus}"


# ---------------------------------------------------------------- Suivi commercial (ventes)


class SalesRep(models.Model):
    """Commerciaux de terrain (prospection d'écoles) — ex. Stephen MBUYU."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_profile",
        help_text="Compte de connexion : permet l'auto-détection du commercial dans le formulaire.",
    )
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    fixed_monthly_salary = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Fixe mensuel personnalisé ($). Vide = montant global (SalesSettings).",
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name

    def monthly_fixed(self, settings_obj=None):
        """Fixe mensuel applicable : valeur perso du commercial, sinon global."""
        if self.fixed_monthly_salary is not None:
            return self.fixed_monthly_salary
        return (settings_obj or SalesSettings.get_solo()).monthly_fixed_amount


class SalesSettings(models.Model):
    """Paramètres des objectifs et commissions — singleton (pk=1)."""

    calls_target = models.PositiveIntegerField(
        default=20, help_text="Objectif hebdomadaire : écoles contactées (appels)."
    )
    demos_target = models.PositiveIntegerField(default=3, help_text="Objectif hebdomadaire : démos physiques.")
    signatures_target = models.PositiveIntegerField(
        default=1, help_text="Objectif hebdomadaire : signatures de contrats pilotes."
    )
    monthly_fixed_amount = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("30.00"),
        help_text="Fixe mensuel global ($), modifiable — repris par commercial s'il a un fixe perso.",
    )
    commission_per_signature = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("5.00"),
        help_text="Commission par école signée ($).",
    )
    commission_per_active_bus = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("2.00"),
        help_text="Commission récurrente par bus actif généré ($).",
    )
    pilot_bus_amount = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("30.00"),
        help_text="Montant estimé d'un bus dans un contrat pilote ($).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Paramètres commerciaux"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @staticmethod
    def weeks_per_month():
        """Nombre de semaines utilisées pour le prorata du fixe mensuel (÷4)."""
        return Decimal(4)

    def weekly_fixed_amount(self):
        """Part hebdomadaire du fixe mensuel (fixe mensuel ÷ 4)."""
        return (self.monthly_fixed_amount / self.weeks_per_month()).quantize(Decimal("0.01"))


class WeeklyReport(models.Model):
    """Rapport hebdomadaire d'un commercial — Lundi → Samedi."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Brouillon"
        SUBMITTED = "SUBMITTED", "Soumis"
        VALIDATED = "VALIDATED", "Validé"

    sales_rep = models.ForeignKey(SalesRep, related_name="weekly_reports", on_delete=models.CASCADE)
    week_start = models.DateField(help_text="Lundi de la semaine couverte par le rapport.")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="validated_sales_reports"
    )
    went_well = models.TextField(blank=True, help_text="Ce qui a bien marché cette semaine.")
    difficulties = models.TextField(blank=True, help_text="Difficultés rencontrées.")
    needs_next_week = models.TextField(blank=True, help_text="Besoins pour la semaine prochaine.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "sales_rep"]
        constraints = [
            models.UniqueConstraint(
                fields=["sales_rep", "week_start"],
                name="unique_report_per_rep_and_week",
                violation_error_message=(
                    "Un rapport existe déjà pour ce commercial sur cette semaine."
                ),
            ),
        ]

    def __str__(self):
        return f"Rapport {self.sales_rep} — sem. du {self.week_start}"

    @property
    def week_end(self):
        """Samedi de la semaine (lundi + 5)."""
        return self.week_start + timedelta(days=5)

    @property
    def deadline(self):
        """Dimanche 23:59 : après cette date, un rapport non soumis est « En retard »."""
        return self.week_start + timedelta(days=6)

    @property
    def is_late(self):
        """True si le rapport est encore en brouillon après la date limite."""
        if self.status != self.Status.DRAFT:
            return False
        return timezone.localdate() > self.deadline

    @property
    def total_contacted(self):
        return sum(d.ecoles_contactees for d in self.prospection_days.all())

    @property
    def total_replies(self):
        return sum(d.reponses_positives for d in self.prospection_days.all())

    @property
    def total_appointments(self):
        return sum(d.rdv_fixes for d in self.prospection_days.all())

    @property
    def demos_count(self):
        return len(self.demos.all())

    @property
    def signed_contracts(self):
        return [c for c in self.contracts.all() if c.signed]

    @property
    def signatures_count(self):
        return len(self.signed_contracts)

    @property
    def signed_bus_count(self):
        return sum(c.bus_count for c in self.signed_contracts)

    @property
    def estimated_amount(self):
        """Montant estimé total des contrats signés (bus × pilot_bus_amount)."""
        s = SalesSettings.get_solo()
        return sum((c.bus_count * s.pilot_bus_amount for c in self.signed_contracts), Decimal(0))

    def attainment(self, settings_obj=None):
        """Taux d'atteinte global des objectifs hebdomadaires (%), plafonné à 100."""
        s = settings_obj or SalesSettings.get_solo()
        ratios = []
        for realized, target in (
            (self.total_contacted, s.calls_target),
            (self.demos_count, s.demos_target),
            (self.signatures_count, s.signatures_target),
        ):
            if target:
                ratios.append(realized / target)
        if not ratios:
            return 0.0
        return round(min(sum(ratios) / len(ratios), 1.0) * 100, 1)

    def active_buses_cumulated(self):
        """Bus actifs générés par le commercial cumulés jusqu'à cette semaine incluses."""
        total = (
            PilotContract.objects.filter(
                report__sales_rep_id=self.sales_rep_id,
                signed=True,
                report__week_start__lte=self.week_start,
            )
            .aggregate(total=models.Sum("bus_count"))
            .get("total")
        )
        return int(total or 0)

    def commission_breakdown(self, settings_obj=None):
        """Détail de la rémunération de la semaine (dict de Decimal).

        Fixe mensuel ÷ 4 (part hebdomadaire) + 5 $/signature + 2 $/bus actif cumulé.
        """
        s = settings_obj or SalesSettings.get_solo()
        monthly = self.sales_rep.monthly_fixed(s)
        fixed_week = (monthly / s.weeks_per_month()).quantize(Decimal("0.01"))
        signatures = self.signatures_count
        active_buses = self.active_buses_cumulated()
        signature_amount = (s.commission_per_signature * signatures).quantize(Decimal("0.01"))
        bus_amount = (s.commission_per_active_bus * active_buses).quantize(Decimal("0.01"))
        return {
            "monthly_fixed": monthly,
            "fixed_week": fixed_week,
            "signatures": signatures,
            "signature_amount": signature_amount,
            "active_buses": active_buses,
            "bus_amount": bus_amount,
            "total": (fixed_week + signature_amount + bus_amount).quantize(Decimal("0.01")),
            "settings": s,
        }


class ProspectionDay(models.Model):
    """Section 1 — grille Prospection : une ligne par jour (Lundi → Samedi)."""

    report = models.ForeignKey(WeeklyReport, related_name="prospection_days", on_delete=models.CASCADE)
    day = models.DateField()
    ecoles_contactees = models.PositiveIntegerField(default=0, help_text="Écoles / appels contactés.")
    reponses_positives = models.PositiveIntegerField(default=0)
    rdv_fixes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["day"]
        constraints = [
            models.UniqueConstraint(fields=["report", "day"], name="unique_prospection_day_per_report"),
        ]

    def __str__(self):
        return f"Prospection {self.day} ({self.report.sales_rep})"


class PhysicalDemo(models.Model):
    """>Section 2 — démonstrations physiques réalisées."""

    report = models.ForeignKey(WeeklyReport, related_name="demos", on_delete=models.CASCADE)
    date = models.DateField()
    school_name = models.CharField(max_length=150)
    director_name = models.CharField(max_length=150, blank=True, help_text="Directeur rencontré.")
    duration_minutes = models.PositiveIntegerField(default=0)
    interest_level = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="Niveau d'intérêt (1 à 10).",
    )
    next_action = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"Démo {self.school_name} ({self.date})"


class PilotContract(models.Model):
    """Section 3 — contrats pilotes signés."""

    report = models.ForeignKey(WeeklyReport, related_name="contracts", on_delete=models.CASCADE)
    date = models.DateField()
    school_name = models.CharField(max_length=150)
    bus_count = models.PositiveIntegerField(
        default=1, validators=[MinValueValidator(1)], help_text="Nombre de bus du contrat."
    )
    signed = models.BooleanField(default=False, help_text="Statut signature (Oui/Non).")

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"Contrat {self.school_name} ({self.bus_count} bus)"

    @property
    def estimated_amount(self):
        """Montant estimé : nombre de bus × pilot_bus_amount (30 $ par défaut)."""
        if not self.signed:
            return Decimal(0)
        return self.bus_count * SalesSettings.get_solo().pilot_bus_amount


class CommissionValidation(models.Model):
    """Onglet 3 — fiche de commission d'un rapport (1:1), snapshots figés à la validation."""

    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "En attente"
        APPROVED = "APPROVED", "Approuvé"
        PAID = "PAID", "Payé"

    report = models.OneToOneField(WeeklyReport, related_name="commission", on_delete=models.CASCADE)
    received_at = models.DateField(help_text="Date de réception du rapport.")
    objectives_met = models.BooleanField(default=False, help_text="Objectifs atteints (OUI / NON).")
    signatures_count = models.PositiveIntegerField(default=0)
    active_buses_count = models.PositiveIntegerField(default=0)
    fixed_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal(0))
    signature_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal(0))
    bus_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal(0))
    total_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal(0))
    payment_status = models.CharField(
        max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    paid_at = models.DateField(null=True, blank=True)
    validated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="sales_commissions"
    )
    validated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-validated_at"]
        verbose_name = "Fiche de commission"

    def __str__(self):
        return f"Commission {self.report} — {self.total_amount} $"
