"""Jeu de données de démonstration complet (écoles, bus, élèves, carburant...).

Usage :
    python manage.py seed_demo --confirm

La commande crée une école de démonstration « DEMO » avec 3 bus, 8 élèves,
des trajets GPS, des pleins, un entretien à prévoir, des incidents et des
notifications parents. Elle est idempotente et refuse d'écrire sans --confirm.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from routing.services import recalculate_school_routes
from routing.signals import recalc_hold
from routing.models import (
    Bus,
    BusMaintenance,
    FuelRecord,
    GPSLog,
    Incident,
    ParentNotification,
    Route,
    School,
    Student,
    StudentTrip,
)

SCHOOL_CODE = "DEMO"

STUDENTS = [
    ("DEMO001", "Jean", "Kasongo", "Kabongo", -11.6647, 27.4794),
    ("DEMO002", "Marie", "Ilunga", "Mwamba", -11.6550, 27.4860),
    ("DEMO003", "Paul", "Kalenga", "Ilunga", -11.6725, 27.4720),
    ("DEMO004", "Grace", "Nkulu", "Tshimanga", -11.6600, 27.4960),
    ("DEMO005", "Daniel", "Mbuyi", "Kalenga", -11.6780, 27.4810),
    ("DEMO006", "Esther", "Kabongo", "Nkulu", -11.6480, 27.4670),
    ("DEMO007", "David", "Tshimanga", "Mbuyi", -11.6710, 27.5010),
    ("DEMO008", "Sarah", "Kalonji", "Mutombo", -11.6850, 27.4640),
]

BUSES = [
    ("DEMO-BUS-01", "Joseph Mwamba", 18),
    ("DEMO-BUS-02", "Patrick Ilunga", 22),
    ("DEMO-BUS-03", "Alain Kabeya", 15),
]


class Command(BaseCommand):
    help = "Crée un jeu de données de démonstration (école DEMO)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirme la création des données de démonstration.",
        )

    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError(
                "Ajoutez --confirm pour créer les données de démonstration "
                "(l'opération est sans danger mais écrit dans la base)."
            )

        school, created = School.objects.get_or_create(
            code_ecole=SCHOOL_CODE,
            defaults={
                "name": "École de démonstration Scolaloop",
                "address": "Av. de la Kasapa, Lubumbashi",
                "latitude": -11.6647,
                "longitude": 27.4794,
            },
        )
        if not created:
            self.stdout.write(
                self.style.WARNING(f"École {school.code_ecole} déjà présente : complétion.")
            )

        # Compte d'administration de l'école de démo.
        User = get_user_model()
        user, user_created = User.objects.get_or_create(username=SCHOOL_CODE.lower())
        if user_created:
            user.set_password("demo1234")
            user.save()
        if school.user_id != user.id:
            school.user = user
            school.save(update_fields=["user"])

        buses = []
        routes = []
        for code, driver, capacity in BUSES:
            bus, _ = Bus.objects.get_or_create(
                code_bus=code,
                defaults={
                    "school": school,
                    "capacity": capacity,
                    "driver_name": driver,
                    "fuel_alert_threshold": Decimal("24.0"),
                },
            )
            if bus.school_id != school.id:
                bus.school = school
                bus.save(update_fields=["school"])
            buses.append(bus)
            route, _ = Route.objects.get_or_create(
                name=f"Tournée {code}",
                bus=bus,
                defaults={
                    "school": school,
                    "total_distance_km": 12.5,
                    "estimated_duration_minutes": 45,
                },
            )
            if route.school_id != school.id:
                route.school = school
                route.save(update_fields=["school"])
            routes.append(route)

        students = []
        # Les signaux reconstruisent les tournées à chaque élève : on les suspend
        # pendant la création, puis on relance un seul calcul complet à la fin.
        with recalc_hold():
            for index, (matricule, prenom, postnom, nom, latitude, longitude) in enumerate(STUDENTS):
                student, _ = Student.objects.get_or_create(
                    matricule=matricule,
                    defaults={
                        "school": school,
                        "prenom": prenom,
                        "postnom": postnom,
                        "nom": nom,
                        "address": f"Domicile démo {index + 1}, Lubumbashi",
                        "latitude": latitude,
                        "longitude": longitude,
                        "parent_phone": f"+2439700000{index + 1:02d}",
                    },
                )
                if student.school_id is None:
                    student.school = school
                student.save()
                students.append(student)
        recalculate_school_routes(school)

        now = timezone.now()
        today = timezone.localdate()

        # Trois mois de pleins pour le premier bus, un plein récent pour les autres.
        for bus_index, bus in enumerate(buses):
            for month_offset in (2, 1, 0):
                date_plein = today.replace(day=min(5 + bus_index, 28)) - timedelta(
                    days=30 * month_offset
                )
                odometer = 42000 + bus_index * 5000 + (2 - month_offset) * 1200
                liters = Decimal("62.00") if bus_index == 0 else Decimal("48.00")
                # Bus 1 volontairement en surconsommation sur le dernier plein.
                if bus_index == 0 and month_offset == 0:
                    liters = Decimal("95.00")
                FuelRecord.objects.get_or_create(
                    bus=bus,
                    date_plein=date_plein,
                    defaults={
                        "liters": liters,
                        "price_per_liter": Decimal("1.35"),
                        "odometer_km": odometer,
                        "fuel_type": FuelRecord.FUEL_GASOIL,
                        "station": "Total Lubumbashi",
                    },
                )

        # Entretien : un réalisé, un à prévoir dans 7 jours.
        BusMaintenance.objects.get_or_create(
            bus=buses[0],
            service_type="Vidange",
            date_effectuee=today - timedelta(days=40),
            defaults={
                "cost": Decimal("85.00"),
                "prochaine_echeance_km": 44500,
                "prochaine_echeance_date": today + timedelta(days=7),
            },
        )

        # Incidents de démonstration.
        Incident.objects.get_or_create(
            bus=buses[1],
            type_incident=Incident.TYPE_EMBOUTEILLAGE,
            timestamp__date=today,
            defaults={"description": "Embouteillage avenue Kasapa, retard estimé 15 min."},
        )

        # Trajets GPS du jour pour le replay et le rapport mensuel.
        for offset in range(12):
            timestamp = now - timedelta(minutes=(12 - offset) * 5)
            GPSLog.objects.get_or_create(
                bus=buses[0],
                timestamp=timestamp,
                defaults={
                    "latitude": -11.6647 + offset * 0.0015,
                    "longitude": 27.4794 + offset * 0.001,
                    "speed_kmh": 22.0,
                },
            )

        # Historique et notifications pour le premier élève.
        student = students[0]
        StudentTrip.objects.get_or_create(
            student=student,
            bus=buses[0],
            event=StudentTrip.EVENT_PICKUP,
            timestamp=now - timedelta(days=1, hours=1),
        )
        StudentTrip.objects.get_or_create(
            student=student,
            bus=buses[0],
            event=StudentTrip.EVENT_DROPOFF,
            timestamp=now - timedelta(days=1),
        )
        ParentNotification.objects.get_or_create(
            student=student,
            bus=buses[0],
            category=ParentNotification.CATEGORY_PICKUP,
            message=f"{student.prenom} a été pris en charge par le bus {buses[0].code_bus}.",
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Démo prête : école {school.code_ecole}, {len(buses)} bus, "
                f"{len(students)} élèves, pleins et entretiens. "
                f"Connexion école : {SCHOOL_CODE.lower()} / demo1234"
            )
        )
