from datetime import timedelta

from django import forms
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet

from .models import (
    Bus,
    CommissionValidation,
    PhysicalDemo,
    PilotContract,
    ProspectionDay,
    School,
    Student,
    WeeklyReport,
)


class StudentForm(forms.ModelForm):
    """Formulaire simplifié : la position GPS du domicile est définie par le
    parent lors de sa première connexion (onboarding), pas par l'école."""

    class Meta:
        model = Student
        fields = [
            "matricule",
            "nom",
            "postnom",
            "prenom",
            "address",
            "parent_phone",
        ]


class BusForm(forms.ModelForm):
    """Formulaire bus : le compte chauffeur (driver_user) n'est plus géré ici —
    il est lié via l'admin Django ou le seed."""

    class Meta:
        model = Bus
        fields = ["code_bus", "capacity", "driver_name", "is_in_service"]


class LoginForm(forms.Form):
    matricule = forms.CharField(
        label="Matricule",
        max_length=50,
        widget=forms.TextInput(
            attrs={"placeholder": "Ex : MAT0001", "autofocus": True}
        ),
    )
    password = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput(attrs={"placeholder": "Votre mot de passe"}),
    )


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ["code_ecole", "name", "address", "latitude", "longitude"]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }


class SchoolRegisterForm(forms.ModelForm):
    """Création d'une nouvelle école + compte administrateur."""
    password = forms.CharField(
        label="Mot de passe",
        min_length=8,
        widget=forms.PasswordInput(attrs={"placeholder": "Au moins 8 caractères"}),
    )
    confirm_password = forms.CharField(
        label="Confirmer le mot de passe",
        widget=forms.PasswordInput(attrs={"placeholder": "Répétez le mot de passe"}),
    )

    class Meta:
        model = School
        fields = ["code_ecole", "name", "address", "latitude", "longitude"]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any", "id": "id_latitude"}),
            "longitude": forms.NumberInput(attrs={"step": "any", "id": "id_longitude"}),
            "code_ecole": forms.TextInput(attrs={"placeholder": "Ex : ECO002", "id": "id_code_ecole"}),
            "name": forms.TextInput(attrs={"placeholder": "Nom complet de l'école"}),
            "address": forms.TextInput(attrs={"placeholder": "Adresse physique (ex : Av. du Marché, Lubumbashi)", "id": "id_address"}),
        }

    def clean_code_ecole(self):
        code = self.cleaned_data["code_ecole"].strip().upper()
        qs = School.objects.filter(code_ecole=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Le code école existe déjà.")
        return code

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("confirm_password"):
            self.add_error("confirm_password", "Les mots de passe ne correspondent pas.")
        return cleaned


# ---------------------------------------------------------------- Suivi commercial

WEEKDAY_LABELS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi"]


class WeeklyReportForm(forms.ModelForm):
    """En-tête + commentaires du rapport hebdomadaire."""

    class Meta:
        model = WeeklyReport
        fields = ["sales_rep", "week_start", "went_well", "difficulties", "needs_next_week"]
        widgets = {
            "week_start": forms.DateInput(attrs={"type": "date"}),
            "sales_rep": forms.Select(),
            "went_well": forms.Textarea(attrs={"rows": 3, "placeholder": "Ce qui a bien marché cette semaine…"}),
            "difficulties": forms.Textarea(attrs={"rows": 3, "placeholder": "Difficultés rencontrées…"}),
            "needs_next_week": forms.Textarea(attrs={"rows": 3, "placeholder": "Besoins pour la semaine prochaine…"}),
        }

    def __init__(self, *args, rep_queryset=None, restrict_rep=None, **kwargs):
        super().__init__(*args, **kwargs)
        if rep_queryset is not None:
            self.fields["sales_rep"].queryset = rep_queryset
        if restrict_rep is not None:
            # Commercial qui remplit lui-même : commercial imposé (champ masqué).
            self.fields["sales_rep"].initial = restrict_rep
            self.fields["sales_rep"].widget = forms.HiddenInput()
            self.fields["sales_rep"].disabled = True


class ProspectionDayForm(forms.ModelForm):
    """Ligne de la grille prospection (un jour). Les valeurs vides comptent 0."""

    class Meta:
        model = ProspectionDay
        fields = ["ecoles_contactees", "reponses_positives", "rdv_fixes"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("ecoles_contactees", "reponses_positives", "rdv_fixes"):
            self.fields[name].required = False
            self.fields[name].widget.attrs.update({"min": 0, "inputmode": "numeric", "placeholder": "0"})

    def has_changed(self):
        """La grille impose toujours 6 lignes (Lundi→Samedi) : une ligne vide
        compte 0 et doit être sauvegardée. Sans cela, le court-circuit
        empty_permitted du formset exclurait les lignes non remplies."""
        return True

    def clean_ecoles_contactees(self):
        return self.cleaned_data.get("ecoles_contactees") or 0

    def clean_reponses_positives(self):
        return self.cleaned_data.get("reponses_positives") or 0

    def clean_rdv_fixes(self):
        return self.cleaned_data.get("rdv_fixes") or 0


class PhysicalDemoForm(forms.ModelForm):
    """Ligne de démonstration physique (liste dynamique)."""

    class Meta:
        model = PhysicalDemo
        fields = [
            "date", "school_name", "director_name",
            "duration_minutes", "interest_level", "next_action",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "school_name": forms.TextInput(attrs={"placeholder": "Nom de l'école"}),
            "director_name": forms.TextInput(attrs={"placeholder": "Directeur rencontré"}),
            "next_action": forms.TextInput(attrs={"placeholder": "Ex : relance WhatsApp jeudi"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["duration_minutes"].required = False
        self.fields["duration_minutes"].widget.attrs.update({"min": 0})
        self.fields["interest_level"].widget.attrs.update({"min": 1, "max": 10})
        if self.instance.pk is None:
            # Le default du modèle (0) rendrait une ligne vide « modifiée »
            # (has_changed → True) et casserait le court-circuit empty_permitted
            # du formset → erreurs fantômes « champ obligatoire ».
            self.initial["duration_minutes"] = None

    def clean_duration_minutes(self):
        return self.cleaned_data.get("duration_minutes") or 0


class _PhysicalDemoFormSet(BaseInlineFormSet):
    """Préfixe explicite « demos » (attendu par le template et l'API)."""

    @classmethod
    def get_default_prefix(cls):
        return "demos"


PhysicalDemoFormSet = inlineformset_factory(
    WeeklyReport,
    PhysicalDemo,
    form=PhysicalDemoForm,
    formset=_PhysicalDemoFormSet,
    extra=2,
    can_delete=True,
)


class PilotContractForm(forms.ModelForm):
    """Ligne de contrat pilote (liste dynamique). Montant estimé calculé côté JS/serveur."""

    class Meta:
        model = PilotContract
        fields = ["date", "school_name", "bus_count", "signed"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "school_name": forms.TextInput(attrs={"placeholder": "Nom de l'école"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bus_count"].widget.attrs.update({"min": 1, "inputmode": "numeric"})
        self.fields["signed"].widget.attrs.update({"class": "js-signed"})
        if self.instance.pk is None:
            # Même piège que PhysicalDemoForm : le default (1) ferait basculer
            # une ligne vide en « modifiée » et bloquerait la validation.
            self.initial["bus_count"] = None

    def clean_bus_count(self):
        value = self.cleaned_data.get("bus_count") or 0
        if value < 1:
            raise forms.ValidationError("Au moins 1 bus par contrat.")
        return value


class _PilotContractFormSet(BaseInlineFormSet):
    """Préfixe explicite « contracts » (attendu par le template et l'API)."""

    @classmethod
    def get_default_prefix(cls):
        return "contracts"


PilotContractFormSet = inlineformset_factory(
    WeeklyReport,
    PilotContract,
    form=PilotContractForm,
    formset=_PilotContractFormSet,
    extra=2,
    can_delete=True,
)


class CommissionValidationForm(forms.ModelForm):
    """Bloc de validation de l'admin : réception, objectifs, statut de paiement.

    Les montants (fixe, signatures, bus, total) sont calculés puis figés par la vue
    au moment du clic sur « Valider et Générer la Fiche ».
    """

    class Meta:
        model = CommissionValidation
        fields = ["received_at", "objectives_met", "payment_status"]
        widgets = {
            "received_at": forms.DateInput(attrs={"type": "date"}),
            "payment_status": forms.Select(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["received_at"].label = "Date de réception du rapport"
        self.fields["objectives_met"].label = "Objectifs atteints (OUI / NON)"
        self.fields["payment_status"].label = "Statut du paiement"
