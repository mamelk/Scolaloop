import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.utils import timezone

from .models import (
    Bus,
    BusStop,
    CommissionValidation,
    GPSLog,
    Incident,
    PhysicalDemo,
    PilotContract,
    ProspectionDay,
    Route,
    RouteStop,
    SalesRep,
    SalesSettings,
    School,
    Student,
    WeeklyReport,
)
from .services import auto_assign_student_to_bus, estimate_bus_delay

User = get_user_model()


class AutoAssignTests(TestCase):
    """Affectation automatique des élèves au bus le plus proche (Haversine),
    avec respect de la capacité, alerte de surcapacité et réoptimisation
    dynamique de la feuille de route."""

    def setUp(self):
        # Le moteur central interroge OSRM (géométrie + matrice table) : on
        # neutralise les appels réseau dans les tests (repli sur Haversine /
        # lignes droites).
        self.osrm_patcher = patch(
            "routing.services.get_road_route_geometry", return_value=None
        )
        self.osrm_patcher.start()
        self.table_patcher = patch(
            "routing.services.get_road_distance_matrix", return_value=None
        )
        self.table_patcher.start()
        self.addCleanup(self.osrm_patcher.stop)
        self.addCleanup(self.table_patcher.stop)
        self.school = School.objects.create(
            code_ecole="ECO1", name="École Test", latitude=0.0, longitude=0.0
        )
        # BUS1 : arrêt à ~1,1 km de (0,0) — BUS2 : arrêt à ~11 km de (0,0)
        self.bus1 = self._make_bus("BUS1", capacity=1, stop_lat=0.01, stop_lng=0.0)
        self.bus2 = self._make_bus("BUS2", capacity=2, stop_lat=0.1, stop_lng=0.0)

    def _make_bus(self, code, capacity, stop_lat, stop_lng):
        bus = Bus.objects.create(
            code_bus=code,
            school=self.school,
            capacity=capacity,
            driver_name=f"Chauffeur {code}",
        )
        route = Route.objects.create(name=f"Trajet {code}", bus=bus, school=self.school)
        stop = BusStop.objects.create(name=f"Arrêt {code}", latitude=stop_lat, longitude=stop_lng)
        RouteStop.objects.create(route=route, stop=stop, order=1)
        return bus

    def _student(self, matricule, latitude=None, longitude=None, **kwargs):
        return Student.objects.create(
            matricule=matricule,
            nom="Test",
            address="Adresse",
            school=self.school,
            latitude=latitude,
            longitude=longitude,
            **kwargs,
        )

    def test_creation_with_gps_assigns_closest_bus_automatically(self):
        # Le signal post_save déclenche l'affectation dès la création.
        student = self._student("S1", latitude=0.0, longitude=0.0)
        student.refresh_from_db()
        self.assertEqual(student.assigned_route.bus, self.bus1)
        self.assertFalse(student.overcapacity_alert)

    def test_capacity_full_sends_next_student_to_next_closest_bus(self):
        self._student("S1", latitude=0.0, longitude=0.0)  # occupe BUS1 (capacité 1)
        student2 = self._student("S2", latitude=0.0, longitude=0.0)
        student2.refresh_from_db()
        self.assertEqual(student2.assigned_route.bus, self.bus2)

    def test_all_buses_full_marks_overcapacity_alert(self):
        # BUS1 (cap 1) + BUS2 (cap 2) = 3 places au total.
        for i in range(1, 4):
            self._student(f"S{i}", latitude=0.0, longitude=0.0)
        over = self._student("S4", latitude=0.0, longitude=0.0)
        over.refresh_from_db()
        self.assertIsNone(over.assigned_route)
        self.assertTrue(over.overcapacity_alert)

    def test_student_without_gps_stays_unassigned(self):
        student = self._student("S1")
        self.assertIsNone(student.assigned_route)
        self.assertFalse(student.overcapacity_alert)

    def test_function_reassigns_after_capacity_frees_up(self):
        # 4 élèves pour 3 places : le dernier créé est en surcapacité.
        for i in range(1, 4):
            self._student(f"S{i}", latitude=0.0, longitude=0.0)
        over = self._student("S4", latitude=0.0, longitude=0.0)
        over.refresh_from_db()
        self.assertTrue(over.overcapacity_alert)
        # Un élève quitte BUS2 : la place se libère → nouvelle affectation possible.
        freed = Student.objects.get(matricule="S2")
        freed.assigned_route = None
        freed.save(update_fields=["assigned_route"])
        bus = auto_assign_student_to_bus(over)
        over.refresh_from_db()
        self.assertIsNotNone(bus)
        self.assertFalse(over.overcapacity_alert)
        self.assertIsNotNone(over.assigned_route)

    def test_parent_onboarding_assigns_and_displays_bus_and_driver(self):
        parent = User.objects.create_user(username="PARENT", password="x")
        student = self._student("S1", parent_user=parent)
        self.client.force_login(parent)
        resp = self.client.post(
            "/parent/setup-home/",
            {"latitude": 0.0, "longitude": 0.0},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        student.refresh_from_db()
        self.assertEqual(student.assigned_route.bus, self.bus1)
        # Le parent voit immédiatement le bus et le chauffeur attribués.
        content = resp.content.decode()
        self.assertIn("BUS1", content)
        self.assertIn("Chauffeur BUS1", content)

    def test_adding_bus_auto_assigns_pending_students(self):
        school2 = School.objects.create(
            code_ecole="ECO4", name="École 4", latitude=0.0, longitude=0.0
        )
        admin = User.objects.create_user(username="ADMIN4", password="x")
        school2.user = admin
        school2.save(update_fields=["user"])
        # Créés avant l'existence des bus : ils restent en surcapacité.
        s1 = Student.objects.create(
            matricule="S1", nom="Test", address="A", school=school2,
            latitude=0.0, longitude=0.0,
        )
        s2 = Student.objects.create(
            matricule="S2", nom="Test", address="A", school=school2,
            latitude=0.01, longitude=0.0,
        )
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertTrue(s1.overcapacity_alert)
        self.assertTrue(s2.overcapacity_alert)
        # L'ajout d'un bus déclenche automatiquement le moteur central :
        # plus aucun bouton manuel n'est nécessaire.
        Bus.objects.create(
            code_bus="B1", school=school2, capacity=5, driver_name="Chauffeur B1"
        )
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertIsNotNone(s1.assigned_route)
        self.assertIsNotNone(s2.assigned_route)
        self.assertFalse(s1.overcapacity_alert)
        self.assertFalse(s2.overcapacity_alert)

    def test_onboarding_reoptimizes_route_and_driver_api_reflects_it(self):
        # BUS1 (cap 1) est rempli par S1 ; S2 va sur BUS2.
        s1 = self._student("S1", latitude=0.001, longitude=0.0)
        s2 = self._student("S2", latitude=0.002, longitude=0.0)
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.assigned_route.bus, self.bus1)
        self.assertEqual(s2.assigned_route.bus, self.bus2)
        # Un nouveau parent valide son domicile → affectation + réoptimisation.
        parent = User.objects.create_user(username="PARENT2", password="x")
        s3 = self._student("S3", parent_user=parent)
        self.client.force_login(parent)
        resp = self.client.post(
            "/parent/setup-home/",
            {"latitude": 0.003, "longitude": 0.0},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        s3.refresh_from_db()
        self.assertIsNotNone(s3.assigned_route)
        # BUS1 est plein : S3 rejoint BUS2.
        self.assertEqual(s3.assigned_route.bus, self.bus2)
        # Feuille de route réoptimisée : S2 (0.002) et S3 (0.003) ne sont
        # qu'à ~111 m l'un de l'autre → un seul arrêt commun (rayon de 200 m,
        # CLUSTER_RADIUS_METERS) : le chauffeur s'arrête une fois pour les deux.
        route = self.bus2.routes.order_by("-created_at").first()
        stops = list(route.stops.order_by("order"))
        self.assertEqual(len(stops), 1)
        self.assertIn("2 élèves", stops[0].stop.name)
        self.assertEqual(route.students_remaining, 2)
        self.assertIsNotNone(route.path_geometry)  # repli lignes droites (OSRM neutralisé)
        # L'API chauffeur reflète le nouvel itinéraire dès le prochain rafraîchissement.
        driver_user = User.objects.create_user(username="DRV2", password="x")
        self.bus2.driver_user = driver_user
        self.bus2.save(update_fields=["driver_user"])
        self.client.force_login(driver_user)
        resp = self.client.get("/api/driver/route/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual({s["id"] for s in data["students"]}, {s2.id, s3.id})
        self.assertEqual(data["total"], 2)

    def test_dashboard_shows_alert_when_students_unassigned(self):
        school2 = School.objects.create(
            code_ecole="ECO2", name="École 2", latitude=0.0, longitude=0.0
        )
        admin = User.objects.create_user(username="ADMIN2", password="x")
        school2.user = admin
        school2.save(update_fields=["user"])
        # Aucun bus dans cette école : l'élève avec GPS reste non affecté.
        Student.objects.create(
            matricule="S1",
            nom="Test",
            address="Adresse",
            school=school2,
            latitude=0.0,
            longitude=0.0,
        )
        # Élève sans GPS : non compté dans la bannière.
        Student.objects.create(
            matricule="S2", nom="Test", address="Adresse", school=school2
        )
        self.client.force_login(admin)
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("1 élève(s) non affecté(s) au transport", content)
        # Le bouton manuel d'affectation a disparu : tout est automatique.
        self.assertNotIn("Lancer l'affectation automatique globale", content)
        self.assertNotIn("Générer / Optimiser le trajet", content)

    def test_route_sheet_blocked_while_students_unassigned(self):
        school2 = School.objects.create(
            code_ecole="ECO3", name="École 3", latitude=0.0, longitude=0.0
        )
        admin = User.objects.create_user(username="ADMIN3", password="x")
        school2.user = admin
        school2.save(update_fields=["user"])
        bus = Bus.objects.create(code_bus="B1", school=school2, capacity=1)
        # Deux élèves avec GPS pour une seule place : le second reste non affecté.
        Student.objects.create(
            matricule="S1", nom="Test", address="Adresse",
            school=school2, latitude=0.0, longitude=0.0,
        )
        Student.objects.create(
            matricule="S2", nom="Test", address="Adresse",
            school=school2, latitude=0.01, longitude=0.0,
        )
        self.client.force_login(admin)
        # Bloqué : un élève avec GPS n'est pas affecté (capacité insuffisante).
        resp = self.client.get(f"/export/feuille-route/{bus.pk}/")
        self.assertEqual(resp.status_code, 302)
        # La capacité augmente → recalcul automatique → feuille de route générée.
        bus.capacity = 5
        bus.save(update_fields=["capacity"])
        resp = self.client.get(f"/export/feuille-route/{bus.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class SyncOfflineDataTests(TestCase):
    databases = {'default'}
    # Force the global URLconf so /api/sync-offline-data/ resolves.

    """Endpoint /api/sync-offline-data/ : resynchronisation groupée des données
    accumulées par le téléphone du chauffeur pendant une coupure réseau."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(
            code_ecole="ECOLE1",
            name="École Test",
            latitude=-11.6647,
            longitude=27.4794,
        )
        cls.driver_user = User.objects.create_user(username="BUS001", password="bus001")
        cls.bus = Bus.objects.create(
            code_bus="BUS001",
            school=cls.school,
            capacity=30,
            driver_name="Chauffeur Test",
            driver_user=cls.driver_user,
        )

    def setUp(self):
        self.client.force_login(self.driver_user)

    def _post(self, payload):
        return self.client.post(
            '/api/sync-offline-data/',
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_inserts_points_and_incidents_in_bulk(self):
        base = timezone.now()
        ts1 = base.isoformat()
        ts2 = (base + timedelta(seconds=1)).isoformat()
        resp = self._post(
            {
                "points": [
                    {"latitude": -11.6647, "longitude": 27.4794, "speed": 12.5, "timestamp": ts1},
                    {"latitude": -11.6650, "longitude": 27.4800, "speed": 0, "timestamp": ts2},
                ],
                "incidents": [
                    {"type_incident": "Panne", "description": "Pneu crevé", "timestamp": ts1},
                ],
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["synced_points"], 2)
        self.assertEqual(data["synced_incidents"], 1)
        self.assertEqual(GPSLog.objects.filter(bus=self.bus).count(), 2)
        self.assertEqual(
            Incident.objects.filter(bus=self.bus, type_incident="Panne").count(), 1
        )
        # Dernier état connu du bus mis à jour (point le plus récent du lot).
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.last_latitude, -11.6650)
        self.assertEqual(self.bus.last_longitude, 27.4800)
        self.assertTrue(self.bus.driver_connected)

    def test_older_points_do_not_overwrite_newer_state(self):
        older = (timezone.now() - timedelta(minutes=30)).isoformat()
        self.bus.last_latitude = 1.0
        self.bus.last_longitude = 2.0
        self.bus.last_position_at = timezone.now()
        self.bus.save()
        resp = self._post(
            {
                "points": [
                    {"latitude": -11.1, "longitude": 27.1, "speed": 0, "timestamp": older},
                ]
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.bus.refresh_from_db()
        # La position plus récente déjà en base n'est pas écrasée.
        self.assertEqual(self.bus.last_latitude, 1.0)
        self.assertEqual(self.bus.last_longitude, 2.0)

    def test_invalid_points_are_skipped_without_failing_batch(self):
        ts = timezone.now().isoformat()
        resp = self._post(
            {
                "points": [
                    {"latitude": "invalide", "longitude": 0, "speed": 0, "timestamp": ts},
                    {"latitude": 999, "longitude": 0, "speed": 0, "timestamp": ts},  # hors limites
                    {"latitude": -11.66, "longitude": 27.48, "speed": -5, "timestamp": ts},
                ],
                "incidents": [
                    {"type_incident": "TypeInconnu", "description": "", "timestamp": ts},
                ],
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["synced_points"], 1)
        self.assertEqual(data["synced_incidents"], 0)
        log = GPSLog.objects.get(bus=self.bus)
        self.assertEqual(log.latitude, -11.66)
        self.assertEqual(log.speed_kmh, 0.0)  # vitesse négative ramenée à 0

    def test_non_driver_is_rejected(self):
        other = User.objects.create_user(username="AUTRE", password="x")
        self.client.force_login(other)
        resp = self._post({"points": [{"latitude": 0, "longitude": 0, "speed": 0}]})
        self.assertEqual(resp.status_code, 403)

    def test_invalid_payload_is_rejected(self):
        resp = self.client.post(
            "/api/sync-offline-data/",
            data="pas du json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        resp = self._post({"points": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_get_method_is_rejected(self):
        resp = self.client.get("/api/sync-offline-data/")
        self.assertEqual(resp.status_code, 405)


class RealtimeApiTests(TestCase):
    """Endpoints de localisation temps réel : /api/bus/<id>/location/ et
    /api/school/fleet/ (position, feuille de route, statut, compteurs)."""

    def setUp(self):
        self.school = School.objects.create(
            code_ecole="ECOAPI", name="École API", latitude=0.0, longitude=0.0
        )
        self.bus = Bus.objects.create(
            code_bus="BUSX",
            school=self.school,
            capacity=10,
            driver_name="Chauffeur X",
            last_latitude=-11.66,
            last_longitude=27.47,
            last_position_at=timezone.now(),
            driver_connected=True,
        )
        # L'élève (avec GPS) est affecté automatiquement au bus : feuille de route créée.
        self.student = Student.objects.create(
            matricule="SA1", nom="Test", address="A", school=self.school,
            latitude=0.001, longitude=0.0,
        )
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.assigned_route)

    def test_parent_sees_bus_location_route_and_status(self):
        parent = User.objects.create_user(username="PARENT_API", password="x")
        self.student.parent_user = parent
        self.student.save(update_fields=["parent_user"])
        self.client.force_login(parent)
        resp = self.client.get(f"/api/bus/{self.bus.pk}/location/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["code_bus"], "BUSX")
        self.assertEqual(data["status"], "en_route")
        self.assertIsNotNone(data["latitude"])
        self.assertIsNotNone(data["last_updated"])
        self.assertTrue(data["is_active"])
        self.assertEqual(data["route"]["students_remaining"], 1)
        self.assertEqual(len(data["route"]["stops"]), 1)

    def test_fleet_api_returns_buses_and_counters(self):
        admin = User.objects.create_user(username="ADMIN_API", password="x")
        self.school.user = admin
        self.school.save(update_fields=["user"])
        self.client.force_login(admin)
        resp = self.client.get("/api/school/fleet/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["buses"]), 1)
        self.assertEqual(data["buses"][0]["code_bus"], "BUSX")
        self.assertEqual(data["buses"][0]["latitude"], -11.66)
        self.assertGreaterEqual(data["counters"]["assigned"], 1)
        self.assertIn("incidents", data["counters"])

    def test_bus_location_denied_for_stranger(self):
        stranger = User.objects.create_user(username="STRANGER", password="x")
        self.client.force_login(stranger)
        resp = self.client.get(f"/api/bus/{self.bus.pk}/location/")
        self.assertEqual(resp.status_code, 403)

    def test_fleet_api_reports_speed_and_speeding_flag(self):
        admin = User.objects.create_user(username="ADMIN_SP", password="x")
        self.school.user = admin
        self.school.save(update_fields=["user"])
        self.bus.speed_kmh = 62.0
        self.bus.save(update_fields=["speed_kmh"])
        self.client.force_login(admin)
        resp = self.client.get("/api/school/fleet/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["buses"][0]["speed_kmh"], 62.0)
        self.assertTrue(data["buses"][0]["is_speeding"])
        self.assertEqual(data["counters"]["speed_alerts"], 1)

    def test_driver_position_stores_measured_speed(self):
        # Le chauffeur envoie sa position avec la vitesse mesurée par l'appareil.
        self.bus.driver_user = User.objects.create_user(username="DRVSPD", password="x")
        self.bus.save(update_fields=["driver_user"])
        self.client.force_login(self.bus.driver_user)
        resp = self.client.post(
            "/driver/position/",
            {"latitude": -11.66, "longitude": 27.47, "speed": 12.5},
        )
        self.assertEqual(resp.status_code, 200)
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.speed_kmh, 12.5)
        self.assertFalse(self.bus.speed_kmh > 50)

    def test_full_realtime_cycle_driver_parent_school(self):
        """Cycle complet : affectation automatique → position + vitesse du
        chauffeur → suivi temps réel parent (vitesse, ETA, excès) et école
        (flotte + alerte d'excès de vitesse)."""
        # Le parent est lié à l'élève (déjà affecté automatiquement au bus).
        parent = User.objects.create_user(username="CYCLE_PARENT", password="x")
        self.student.parent_user = parent
        self.student.save(update_fields=["parent_user"])
        # Le chauffeur envoie sa position (hors dépôt) et sa vitesse mesurée.
        self.bus.driver_user = User.objects.create_user(username="CYCLE_DRV", password="x")
        self.bus.save(update_fields=["driver_user"])
        self.client.force_login(self.bus.driver_user)
        resp = self.client.post(
            "/driver/position/",
            {"latitude": 0.01, "longitude": 0.0, "speed": 58.0},
        )
        self.assertEqual(resp.status_code, 200)
        # Le parent voit la position, la vitesse, l'ETA et l'alerte d'excès.
        self.client.force_login(parent)
        data = self.client.get("/api/parent/live/").json()
        self.assertEqual(data["status"], "en_route")
        self.assertEqual(data["speed_kmh"], 58.0)
        self.assertTrue(data["is_speeding"])
        self.assertIsNotNone(data["eta_minutes"])
        # L'école voit l'excès de vitesse dans la flotte.
        admin = User.objects.create_user(username="CYCLE_ADMIN", password="x")
        self.school.user = admin
        self.school.save(update_fields=["user"])
        self.client.force_login(admin)
        fleet = self.client.get("/api/school/fleet/").json()
        self.assertTrue(any(b["is_speeding"] for b in fleet["buses"]))
        self.assertGreaterEqual(fleet["counters"]["speed_alerts"], 1)


class BusDelayTests(TestCase):
    """Estimation du retard réel : départ effectif (premier GPS du jour à plus
    de 400 m de l'école) vs progression réelle le long de la géométrie du
    trajet (horaire prévu reconstruit depuis la durée estimée)."""

    def setUp(self):
        self.school = School.objects.create(
            code_ecole="ECODLY", name="École Retard", latitude=0.0, longitude=0.0
        )
        self.bus = Bus.objects.create(
            code_bus="BUSDLY", school=self.school, capacity=10, driver_name="Chauffeur D"
        )
        self.route = Route.objects.create(
            name="Trajet test",
            bus=self.bus,
            school=self.school,
            estimated_duration_minutes=60.0,
            path_geometry=[
                [0.0, 0.0], [0.0, 0.1], [0.0, 0.2], [0.0, 0.3], [0.0, 0.4], [0.0, 0.5],
            ],
        )
        self.now = timezone.localtime()
        # Départ effectif : premier GPS du jour à plus de 400 m de l'école.
        dep = GPSLog.objects.create(bus=self.bus, latitude=0.0, longitude=0.01)
        GPSLog.objects.filter(pk=dep.pk).update(
            timestamp=self.now - timedelta(minutes=10)
        )

    def _position(self, longitude):
        self.bus.last_latitude = 0.0
        self.bus.last_longitude = longitude
        self.bus.last_position_at = self.now
        self.bus.save(
            update_fields=["last_latitude", "last_longitude", "last_position_at"]
        )

    def test_bus_behind_schedule_is_late(self):
        # 10 % de la boucle parcourue après 10 min sur 60 min prévues → retard.
        self._position(0.05)
        delay = estimate_bus_delay(self.bus, self.route, now=self.now)
        self.assertIsNotNone(delay)
        self.assertGreater(delay, 0)
        self.assertAlmostEqual(delay, 4.0, delta=1.0)

    def test_bus_ahead_of_schedule_is_early(self):
        # 70 % de la boucle parcourue après 10 min → en avance.
        self._position(0.35)
        delay = estimate_bus_delay(self.bus, self.route, now=self.now)
        self.assertIsNotNone(delay)
        self.assertLess(delay, 0)

    def test_bus_without_recent_gps_has_no_delay(self):
        self.bus.last_latitude = 0.0
        self.bus.last_longitude = 0.05
        self.bus.last_position_at = None
        self.assertIsNone(estimate_bus_delay(self.bus, self.route, now=self.now))

    def test_bus_still_at_depot_has_no_delay(self):
        # Tous les GPS du jour restent près de l'école : aucun départ détecté.
        GPSLog.objects.filter(bus=self.bus).update(latitude=0.0, longitude=0.001)
        self._position(0.001)
        self.assertIsNone(estimate_bus_delay(self.bus, self.route, now=self.now))


class ServiceWorkerTests(TestCase):
    """Le Service Worker est servi à la racine avec la portée couvrant le site."""

    def test_sw_served_at_root_with_scope_header(self):
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Service-Worker-Allowed"], "/")
        self.assertIn("application/javascript", resp["Content-Type"])
        self.assertIn(b"tile.openstreetmap.org", resp.content)


class MapTileSourceTests(TestCase):
    """Toutes les cartes doivent pointer vers un serveur de tuiles qui répond.
    Régression couverte : les URLs ``{s}.tile.openstreetmap.fr`` renvoyaient
    HTTP 404 pour chaque tuile → carte entièrement grise sur tous les écrans."""

    TILE_SNIPPET = "tile.openstreetmap.org/{z}/{x}/{y}.png"

    def test_templates_use_working_tile_server(self):
        for name in [
            "dashboard.html",
            "parent.html",
            "driver.html",
            "replay.html",
            "setup_home.html",
            "register_school.html",
        ]:
            with self.subTest(template=name):
                path = Path(settings.BASE_DIR) / "routing" / "templates" / "routing" / name
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("tile.openstreetmap.fr", source)

    def test_driver_map_script_uses_working_tile_server(self):
        path = Path(settings.BASE_DIR) / "routing" / "static" / "js" / "driver_map.js"
        source = path.read_text(encoding="utf-8")
        self.assertIn(self.TILE_SNIPPET, source)
        self.assertNotIn("tile.openstreetmap.fr", source)

    def test_maps_use_resilient_tile_helper(self):
        # Chaque carte doit passer par map_tiles.js : c'est lui qui bascule de
        # fournisseur quand les tuiles sont bloquées (403 OSM en production).
        for name in [
            "dashboard.html",
            "parent.html",
            "driver.html",
            "replay.html",
            "setup_home.html",
            "register_school.html",
        ]:
            with self.subTest(template=name):
                path = Path(settings.BASE_DIR) / "routing" / "templates" / "routing" / name
                source = path.read_text(encoding="utf-8")
                self.assertIn("js/map_tiles.js", source)
                self.assertNotIn("L.tileLayer(", source)

    def test_driver_map_script_uses_resilient_tile_helper(self):
        path = Path(settings.BASE_DIR) / "routing" / "static" / "js" / "driver_map.js"
        source = path.read_text(encoding="utf-8")
        self.assertIn("ScolaloopTiles.addTo", source)

    def test_tile_helper_lists_fallback_providers(self):
        path = Path(settings.BASE_DIR) / "routing" / "static" / "js" / "map_tiles.js"
        source = path.read_text(encoding="utf-8")
        # Le chemin « /osmfr/ » est obligatoire : sans lui, OSM France renvoie 404.
        self.assertIn("tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", source)
        self.assertIn("server.arcgisonline.com", source)
        # CARTO sert désormais une image « API KEY REQUIRED » sans clé.
        self.assertNotIn("basemaps.cartocdn.com", source)

    def test_service_worker_fetches_tiles_in_cors_mode(self):
        # En mode « no-cors », une réponse 403 est opaque (status 0) : elle
        # paraîtrait valide et serait mise en cache, figeant la carte sur les
        # tuiles « Access blocked ». Le service worker force donc le CORS.
        resp = self.client.get("/sw.js")
        self.assertIn(b"mode: 'cors'", resp.content)
        self.assertNotIn(b"response.type === 'opaque'", resp.content)


class SeoAndHomeTests(TestCase):
    """Page d'accueil et endpoints SEO : le rendu lit settings.SITE_NAME /
    SITE_URL / etc. Régression couverte : la suppression de ces réglages dans
    settings.py provoquait un AttributeError → 500 sur l'accueil en production."""

    def test_home_renders_seo_context(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        # canonical + og:url injectent settings.SITE_URL dans le rendu.
        self.assertContains(resp, settings.SITE_URL)

    def test_robots_txt_references_sitemap(self):
        resp = self.client.get("/robots.txt")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"Sitemap: {settings.SITE_URL}/sitemap.xml")

    def test_sitemap_xml_references_site_url(self):
        resp = self.client.get("/sitemap.xml")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f"<loc>{settings.SITE_URL}/</loc>")


class RegisterSchoolMapTests(TestCase):
    """Carte d'inscription d'école : le conteneur masqué (map-wrap) doit être
    affiché AVANT map.invalidateSize(). Sinon Leaflet mesure le conteneur en
    0×0, une seule tuile se charge et la carte reste grise jusqu'à un
    redimensionnement de fenêtre (bug réel corrigé)."""

    def test_register_school_page_renders(self):
        resp = self.client.get("/register-school/")
        self.assertEqual(resp.status_code, 200)

    def test_map_unhidden_before_invalidate_size(self):
        html = self.client.get("/register-school/").content.decode()
        unhide = html.index("mapWrap.style.display = 'block'")
        measure = html.index("map.invalidateSize()")
        self.assertLess(unhide, measure)


class SalesTrackingTests(TestCase):
    """Suivi commercial : accès par rôle, CRUD des rapports, calcul des
    commissions, API JSON et exports."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_superuser("root", "root@example.com", "pw")
        cls.commercial_user = User.objects.create_user("stephen", password="pw")
        cls.intruder = User.objects.create_user("intruder", password="pw")
        cls.rep = SalesRep.objects.create(
            full_name="Stephen MBUYU", user=cls.commercial_user
        )
        cls.rep2 = SalesRep.objects.create(full_name="Crispin TSHONDO")
        cls.s = SalesSettings.get_solo()
        # Lundi de la semaine courante (référence pour tous les tests).
        cls.monday = timezone.localdate() - timedelta(
            days=timezone.localdate().weekday()
        )

    def _login(self, user):
        self.client.force_login(user)

    # ---------------------------------------------------------------- Accès

    def test_dashboard_requires_role(self):
        resp = self.client.get("/admin/sales-tracking/")
        self.assertEqual(resp.status_code, 302)  # anonyme → login
        self._login(self.intruder)
        resp = self.client.get("/admin/sales-tracking/")
        self.assertEqual(resp.status_code, 302)  # sans rôle → refus

    def test_dashboard_allows_admin_group_and_superuser(self):
        User = get_user_model()
        group_user = User.objects.create_user("admin1", password="pw")
        group_user.groups.add(Group.objects.get_or_create(name="ADMIN")[0])
        self._login(group_user)
        self.assertEqual(self.client.get("/admin/sales-tracking/").status_code, 200)
        self._login(self.admin)
        self.assertEqual(self.client.get("/admin/sales-tracking/").status_code, 200)

    def test_commercial_can_open_own_report_form(self):
        self._login(self.commercial_user)
        self.assertEqual(self.client.get("/admin/sales-tracking/rapport/nouveau/").status_code, 200)

    # ------------------------------------------------------------- Formulaire

    def _post_payload(self, **overrides):
        """Payload POST minimal du formulaire (6 jours + 1 démo + 1 contrat)."""
        data = {
            "sales_rep": str(self.rep.pk),
            "week_start": self.monday.isoformat(),
            "went_well": "Bonne réactivité des directeurs.",
            "difficulties": "Peu de temps d'écoute en fin de journée.",
            "needs_next_week": "Brochures A4 supplémentaires.",
            "prospection-TOTAL_FORMS": "6",
            "prospection-INITIAL_FORMS": "0",
            "prospection-MIN_NUM_FORMS": "0",
            "prospection-MAX_NUM_FORMS": "1000",
            "demos-TOTAL_FORMS": "2",
            "demos-INITIAL_FORMS": "0",
            "demos-MIN_NUM_FORMS": "0",
            "demos-MAX_NUM_FORMS": "1000",
            "contracts-TOTAL_FORMS": "2",
            "contracts-INITIAL_FORMS": "0",
            "contracts-MIN_NUM_FORMS": "0",
            "contracts-MAX_NUM_FORMS": "1000",
            "action": "save",
        }
        for i in range(6):
            # Jour 0 : 5 écoles, 2 réponses, 1 RDV ; les autres à zéro.
            data[f"prospection-{i}-ecoles_contactees"] = "5" if i == 0 else ""
            data[f"prospection-{i}-reponses_positives"] = "2" if i == 0 else ""
            data[f"prospection-{i}-rdv_fixes"] = "1" if i == 0 else ""
        data.update({
            "demos-0-date": self.monday.isoformat(),
            "demos-0-school_name": "Ecole A",
            "demos-0-director_name": "M. Kalala",
            "demos-0-duration_minutes": "45",
            "demos-0-interest_level": "8",
            "demos-0-next_action": "Relance vendredi",
            "contracts-0-date": (self.monday + timedelta(days=3)).isoformat(),
            "contracts-0-school_name": "Ecole A",
            "contracts-0-bus_count": "3",
            "contracts-0-signed": "on",
        })
        data.update(overrides)
        return data

    def test_report_creation_saves_all_sections(self):
        self._login(self.admin)
        resp = self.client.post(
            "/admin/sales-tracking/rapport/nouveau/", self._post_payload()
        )
        report = WeeklyReport.objects.get()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(report.sales_rep, self.rep)
        self.assertEqual(report.status, WeeklyReport.Status.DRAFT)
        # Grille : 6 lignes J→S, la première porte les valeurs.
        days = list(report.prospection_days.order_by("day"))
        self.assertEqual(len(days), 6)
        self.assertEqual(days[0].day, self.monday)
        self.assertEqual(report.total_contacted, 5)
        self.assertEqual(report.total_appointments, 1)
        self.assertEqual(report.demos_count, 1)
        self.assertEqual(report.signatures_count, 1)
        self.assertEqual(report.signed_bus_count, 3)
        # Montant estimé : 3 bus × 30 $.
        self.assertEqual(report.estimated_amount, self.s.pilot_bus_amount * 3)
        self.assertEqual(report.went_well, "Bonne réactivité des directeurs.")

    def test_duplicate_week_rejected_and_nothing_saved(self):
        self._login(self.admin)
        self.client.post("/admin/sales-tracking/rapport/nouveau/", self._post_payload())
        resp = self.client.post(
            "/admin/sales-tracking/rapport/nouveau/",
            self._post_payload(),
        )
        self.assertEqual(WeeklyReport.objects.count(), 1)  # pas de doublon
        self.assertContains(resp, "Un rapport existe déjà", status_code=200)

    def test_negative_value_rejected(self):
        self._login(self.admin)
        resp = self.client.post(
            "/admin/sales-tracking/rapport/nouveau/",
            self._post_payload(**{"prospection-0-ecoles_contactees": "-3"}),
        )
        self.assertEqual(resp.status_code, 200)  # réaffiché avec erreurs
        self.assertEqual(WeeklyReport.objects.count(), 0)
        # Message par défaut de MinValueValidator sur PositiveIntegerField.
        self.assertContains(resp, "supérieure ou égale à 0")

    def test_empty_demo_row_not_created(self):
        self._login(self.admin)
        payload = self._post_payload()
        # La 2e ligne de démo (extra) reste entièrement vide.
        payload.update({
            "demos-1-date": "", "demos-1-school_name": "",
            "demos-1-director_name": "", "demos-1-duration_minutes": "",
            "demos-1-interest_level": "", "demos-1-next_action": "",
        })
        self.client.post("/admin/sales-tracking/rapport/nouveau/", payload)
        self.assertEqual(PhysicalDemo.objects.count(), 1)

    def test_interest_level_out_of_range_rejected(self):
        self._login(self.admin)
        resp = self.client.post(
            "/admin/sales-tracking/rapport/nouveau/",
            self._post_payload(**{"demos-0-interest_level": "15"}),
        )
        self.assertEqual(PhysicalDemo.objects.count(), 0)
        self.assertContains(resp, "10")

    def test_commercial_auto_detected_and_scoped(self):
        self._login(self.commercial_user)
        # Création : le commercial est imposé même si un autre est envoyé.
        self.client.post(
            "/admin/sales-tracking/rapport/nouveau/",
            self._post_payload(**{"sales_rep": str(self.rep2.pk)}),
        )
        report = WeeklyReport.objects.get()
        self.assertEqual(report.sales_rep, self.rep)
        # Lecture/modification d'un rapport d'un autre commercial : refusé.
        self._login(self.intruder)
        resp = self.client.get(f"/admin/sales-tracking/rapport/{report.pk}/")
        self.assertEqual(resp.status_code, 302)
        self._login(self.commercial_user)
        resp = self.client.get(f"/admin/sales-tracking/rapport/{report.pk}/")
        self.assertEqual(resp.status_code, 200)

    def test_week_change_rebuilds_grid(self):
        self._login(self.admin)
        self.client.post("/admin/sales-tracking/rapport/nouveau/", self._post_payload())
        report = WeeklyReport.objects.get()
        payload = self._post_payload(**{"week_start": (self.monday + timedelta(days=7)).isoformat()})
        # Édition : le formset devient initial (lignes existantes + leurs ids,
        # exactement comme le template rend les champs cachés).
        day_ids = list(report.prospection_days.order_by("day").values_list("id", flat=True))
        self.assertEqual(len(day_ids), 6)
        payload["prospection-INITIAL_FORMS"] = "6"
        for i, pk in enumerate(day_ids):
            payload[f"prospection-{i}-id"] = str(pk)
            payload[f"prospection-{i}-ecoles_contactees"] = ""
        payload["demos-INITIAL_FORMS"] = "1"
        payload["demos-0-id"] = str(report.demos.get().pk)
        payload["contracts-INITIAL_FORMS"] = "1"
        payload["contracts-0-id"] = str(report.contracts.get().pk)
        resp = self.client.post(f"/admin/sales-tracking/rapport/{report.pk}/", payload)
        self.assertEqual(resp.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.week_start, self.monday + timedelta(days=7))
        days = list(report.prospection_days.order_by("day"))
        self.assertEqual(len(days), 6)  # grille reconstruite, pas de doublon
        self.assertEqual(days[0].day, self.monday + timedelta(days=7))
        self.assertEqual(report.demos.count(), 1)  # mis à jour, pas dupliqué
        self.assertEqual(report.contracts.count(), 1)

    # ------------------------------------------------------ Soumission / commission

    def test_submit_and_commission_calculation(self):
        self._login(self.admin)
        self.client.post("/admin/sales-tracking/rapport/nouveau/", self._post_payload())
        report = WeeklyReport.objects.get()
        # Soumission.
        resp = self.client.post(f"/admin/sales-tracking/rapport/{report.pk}/soumettre/")
        report.refresh_from_db()
        self.assertEqual(report.status, WeeklyReport.Status.SUBMITTED)
        self.assertIsNotNone(report.submitted_at)

        # Validation : fixe 30/4=7.50 + 5×1 signature + 2×3 bus = 20.50 $.
        resp = self.client.post(
            f"/admin/sales-tracking/rapport/{report.pk}/valider/",
            {
                "received_at": timezone.localdate().isoformat(),
                "objectives_met": "on",
                "payment_status": "PENDING",
                "action": "validate",
            },
        )
        self.assertEqual(resp.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, WeeklyReport.Status.VALIDATED)
        commission = CommissionValidation.objects.get(report=report)
        self.assertEqual(commission.signatures_count, 1)
        self.assertEqual(commission.active_buses_count, 3)
        self.assertEqual(commission.fixed_amount, self.s.monthly_fixed_amount / 4)
        self.assertEqual(
            commission.total_amount,
            self.s.monthly_fixed_amount / 4
            + self.s.commission_per_signature
            + self.s.commission_per_active_bus * 3,
        )

    def test_commission_cumulates_buses_across_weeks(self):
        """La commission bus récurrente cumule les bus signés des semaines antérieures."""
        r1 = WeeklyReport.objects.create(sales_rep=self.rep, week_start=self.monday - timedelta(days=7))
        PilotContract.objects.create(
            report=r1, date=r1.week_start, school_name="Ecole X", bus_count=2, signed=True
        )
        r2 = WeeklyReport.objects.create(sales_rep=self.rep, week_start=self.monday)
        PilotContract.objects.create(
            report=r2, date=r2.week_start, school_name="Ecole Y", bus_count=4, signed=True
        )
        # Cumul jusqu'à r2 : 2 + 4 = 6 bus.
        self.assertEqual(r2.active_buses_cumulated(), 6)
        bd = r2.commission_breakdown()
        self.assertEqual(bd["active_buses"], 6)
        self.assertEqual(bd["bus_amount"], self.s.commission_per_active_bus * 6)

    def test_commission_view_denied_to_commercial(self):
        self._login(self.admin)
        self.client.post("/admin/sales-tracking/rapport/nouveau/", self._post_payload())
        report = WeeklyReport.objects.get()
        self._login(self.commercial_user)
        resp = self.client.get(f"/admin/sales-tracking/rapport/{report.pk}/valider/")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(CommissionValidation.objects.exists())

    # ------------------------------------------------------------------ API JSON

    def _api_payload(self):
        return {
            "sales_rep": self.rep.pk,
            "week_start": self.monday.isoformat(),
            "prospection": [
                {"ecoles_contactees": 4, "reponses_positives": 1, "rdv_fixes": 1},
            ],
            "demos": [
                {
                    "date": self.monday.isoformat(),
                    "school_name": "Ecole API",
                    "director_name": "M. Test",
                    "duration_minutes": 30,
                    "interest_level": 7,
                    "next_action": "Relancer",
                }
            ],
            "contracts": [
                {
                    "date": self.monday.isoformat(),
                    "school_name": "Ecole API",
                    "bus_count": 2,
                    "signed": True,
                }
            ],
            "went_well": "OK",
        }

    def test_api_create_report(self):
        self._login(self.admin)
        resp = self.client.post(
            "/api/sales/reports/", json.dumps(self._api_payload()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["report"]["totals"]["calls"], 4)
        self.assertEqual(body["report"]["totals"]["signatures"], 1)
        self.assertEqual(len(body["report"]["prospection"]), 1)

    def test_api_update_replaces_provided_sections_only(self):
        self._login(self.admin)
        self.client.post(
            "/api/sales/reports/", json.dumps(self._api_payload()),
            content_type="application/json",
        )
        report = WeeklyReport.objects.get()
        # PATCH : ne fournit QUE les démos → les autres sections sont intactes.
        patch_data = {
            "demos": [
                {
                    "date": self.monday.isoformat(),
                    "school_name": "Ecole REMPLACEE",
                    "director_name": "",
                    "duration_minutes": 20,
                    "interest_level": 5,
                    "next_action": "",
                }
            ],
        }
        resp = self.client.patch(
            f"/api/sales/reports/{report.pk}/", json.dumps(patch_data),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(PhysicalDemo.objects.count(), 1)
        self.assertEqual(PhysicalDemo.objects.get().school_name, "Ecole REMPLACEE")
        # Contrats et commentaires non touchés.
        self.assertEqual(PilotContract.objects.count(), 1)
        self.assertEqual(WeeklyReport.objects.get().went_well, "OK")

    def test_api_validate_commission(self):
        self._login(self.admin)
        self.client.post(
            "/api/sales/reports/", json.dumps(self._api_payload()),
            content_type="application/json",
        )
        report = WeeklyReport.objects.get()
        resp = self.client.post(
            f"/api/sales/reports/{report.pk}/validate/",
            json.dumps({
                "received_at": timezone.localdate().isoformat(),
                "objectives_met": True,
                "payment_status": "APPROVED",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        commission = CommissionValidation.objects.get()
        self.assertEqual(commission.payment_status, "APPROVED")
        # 30/4 + 5×1 + 2×2 = 16.50 $
        self.assertEqual(
            commission.total_amount,
            self.s.monthly_fixed_amount / 4 + self.s.commission_per_signature + 4,
        )
        report.refresh_from_db()
        self.assertEqual(report.status, WeeklyReport.Status.VALIDATED)

    def test_api_kpis_requires_role(self):
        self._login(self.commercial_user)
        resp = self.client.get("/api/sales/kpis/")
        self.assertEqual(resp.status_code, 403)
        self._login(self.admin)
        resp = self.client.get("/api/sales/kpis/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("kpis", resp.json())

    def test_api_normalizes_negative_calls(self):
        self._login(self.admin)
        payload = self._api_payload()
        payload["prospection"][0]["ecoles_contactees"] = -5
        resp = self.client.post(
            "/api/sales/reports/", json.dumps(payload), content_type="application/json"
        )
        # La grille prospection normalise à 0 (jamais de négatif en base).
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(ProspectionDay.objects.get().ecoles_contactees, 0)

    def test_api_delete_draft_only_for_commercial(self):
        self._login(self.admin)
        self.client.post(
            "/api/sales/reports/", json.dumps(self._api_payload()),
            content_type="application/json",
        )
        report = WeeklyReport.objects.get()
        self._login(self.commercial_user)
        # Brouillon : suppression OK pour l'auteur.
        resp = self.client.delete(f"/api/sales/reports/{report.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(WeeklyReport.objects.count(), 0)

    # ------------------------------------------------------------------ Exports

    def test_exports_render(self):
        self._login(self.admin)
        self.client.post("/admin/sales-tracking/rapport/nouveau/", self._post_payload())
        report = WeeklyReport.objects.get()
        xlsx = self.client.get(f"/admin/sales-tracking/rapport/{report.pk}/export.xlsx")
        self.assertEqual(xlsx.status_code, 200)
        self.assertIn("spreadsheetml", xlsx["Content-Type"])
        pdf = self.client.get(f"/admin/sales-tracking/rapport/{report.pk}/export.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        csv = self.client.get("/admin/sales-tracking/export.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("text/csv", csv["Content-Type"])
        self.assertIn("Commercial", csv.content.decode("utf-8-sig"))
