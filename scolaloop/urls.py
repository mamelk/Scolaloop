from django.urls import path
from django.contrib import admin
from django.urls import include
from routing import views

from .healthz import healthz_view

urlpatterns = [
    # Suivi commercial — Doit précéder l'admin Django : les routes partagent
    # le préfixe « admin/ » et seraient sinon avalées par l'admin (404).
    path('', include('routing.sales_urls')),

    path('admin/', admin.site.urls),

    # Routing app montée à la racine (comme au dépôt initial) — doit précéder
    # auth.urls : l'app routing fournit son propre /login/ et /logout/.
    path('', include('routing.urls')),
    path('', include('django.contrib.auth.urls')),
    path('', views.home_view, name='home'),
    path('api/sync-offline-data/', views.sync_offline_data, name='sync_offline_data'),

    # Electromenager views
    path('electromenager/', views.electromenager_liste, name='electromenager_liste'),
    path('electromenager/<int:metier_id>/', views.electromenager_detail, name='electromenager_detail'),

    # Metiers-metiers views
    path('metiers-metiers/', views.metiers_metiers_liste, name='metiers_metiers_liste'),
    path('metiers-metiers/<int:metier_id>/', views.metiers_metiers_detail, name='metiers_metiers_detail'),

    # Sante-ally views
    path('sante-ally/', views.sante_ally_liste, name='sante_ally_liste'),
    path('sante-ally/<int:metier_id>/', views.sante_ally_detail, name='sante_ally_detail'),

    # Healthz
    path('healthz/', healthz_view, name='healthz'),
]
