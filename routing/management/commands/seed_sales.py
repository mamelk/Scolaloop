"""Seed du module Suivi Commercial : groupes de rôles, paramètres, commerciaux.

Usage : python manage.py seed_sales
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from routing.models import SalesRep, SalesSettings


class Command(BaseCommand):
    help = (
        "Crée les groupes ADMIN/SUPER_ADMIN, les paramètres commerciaux par "
        "défaut (fixe mensuel 30 $) et les commerciaux Stephen MBUYU & Crispin TSHONDO."
    )

    def handle(self, *args, **options):
        # 1. Groupes de rôles (ADMIN = gestion du suivi commercial ; SUPER_ADMIN
        #    = tous les privilèges — les superusers y accèdent déjà de fait).
        admin_group, created = Group.objects.get_or_create(name="ADMIN")
        if created:
            # Toutes les permissions de l'app routing pour l'admin commercial.
            perms = Permission.objects.filter(content_type__app_label="routing")
            admin_group.permissions.set(perms)
            self.stdout.write(self.style.SUCCESS("Groupe ADMIN créé (perms routing)."))
        else:
            self.stdout.write(self.style.WARNING("Groupe ADMIN déjà présent."))

        super_group, created = Group.objects.get_or_create(name="SUPER_ADMIN")
        if created:
            super_group.permissions.set(Permission.objects.all())
            self.stdout.write(self.style.SUCCESS("Groupe SUPER_ADMIN créé."))
        else:
            self.stdout.write(self.style.WARNING("Groupe SUPER_ADMIN déjà présent."))

        # 2. Paramètres commerciaux (singleton) — fixe mensuel 30 $ modifiable.
        preexisted = SalesSettings.objects.filter(pk=1).exists()
        settings_obj = SalesSettings.get_solo()
        if preexisted:
            self.stdout.write(
                self.style.WARNING(
                    f"SalesSettings déjà présent : fixe {settings_obj.monthly_fixed_amount} $, "
                    f"cibles {settings_obj.calls_target}/{settings_obj.demos_target}/"
                    f"{settings_obj.signatures_target}."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"SalesSettings créé : fixe mensuel {settings_obj.monthly_fixed_amount} $."
                )
            )

        # 3. Commerciaux (comptes de connexion associés pour l'auto-détection).
        User = get_user_model()
        REPS = [
            # (nom complet, username, mot de passe)
            ("Stephen MBUYU", "stephen", "sales2026"),
            ("Crispin TSHONDO", "crispin", "sales2026"),
        ]
        for full_name, username, password in REPS:
            rep, created_rep = SalesRep.objects.get_or_create(
                full_name=full_name,
                defaults={"email": f"{username}@scolaloop.com"},
            )
            user, created_user = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@scolaloop.com"},
            )
            if created_user:
                user.set_password(password)
                user.save()
                self.stdout.write(
                    self.style.SUCCESS(f"Compte créé : {username} / {password}")
                )
            else:
                self.stdout.write(self.style.WARNING(f"Compte déjà présent : {username}"))
            if rep.user_id != user.id:
                rep.user = user
                rep.save(update_fields=["user"])
            if created_rep:
                self.stdout.write(self.style.SUCCESS(f"Commercial créé : {full_name}"))

        self.stdout.write(self.style.SUCCESS("Seed suivi commercial terminé."))
