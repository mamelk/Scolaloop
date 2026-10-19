/* ============================================================================
 * Scolaloop — Fond de carte résilient (static/js/map_tiles.js)
 * ----------------------------------------------------------------------------
 * Un fournisseur de tuiles peut tomber (404, 403 « Access blocked », panne) :
 * la carte restait alors entièrement grise, sans aucun message. Ce module
 * installe le fond de carte et bascule automatiquement vers le fournisseur
 * suivant dès qu'aucune tuile n'a pu être chargée.
 *
 * Ordre des fournisseurs (tous en CORS, donc servis par le service worker) :
 *   1. Esri World Street  — tuiles natives jusqu'au zoom 19, couverture mondiale
 *   2. OSM France         — serveurs dédiés (tuiles natives jusqu'au zoom 17)
 *   3. OpenStreetMap.org  — dernier recours (souvent bloqué en 403)
 *
 * CARTO n'est plus utilisé : ses tuiles raster renvoient désormais une image
 * « API KEY REQUIRED » sans clé, ce qui donnerait une carte grise annotée.
 *
 * API exposée sur window.ScolaloopTiles :
 *   - addTo(map)      → ajoute le fond de carte et renvoie le calque actif
 *   - useNext(map)    → bascule manuellement sur le fournisseur suivant
 *   - current()       → { index, name } du fournisseur actif
 * ========================================================================== */

(function () {
    'use strict';

    var ATTRIBUTION_OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';

    var PROVIDERS = [
        {
            name: 'Esri World Street Map',
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
            options: { maxZoom: 19 },
            attribution: 'Tuiles &copy; <a href="https://www.esri.com">Esri</a>' + ' · ' + ATTRIBUTION_OSM
        },
        {
            name: 'OSM France',
            url: 'https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png',
            // Les tuiles natives s'arrêtent au zoom 17 hors de France : au-delà
            // Leaflet agrandit la tuile z17 au lieu de demander un 404.
            options: { subdomains: 'abc', maxZoom: 19, maxNativeZoom: 17 },
            attribution: ATTRIBUTION_OSM + ' · tuiles <a href="https://openstreetmap.fr">OSM France</a>'
        },
        {
            name: 'OpenStreetMap',
            url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
            options: { maxZoom: 19 },
            attribution: ATTRIBUTION_OSM
        }
    ];

    // Nombre de tuiles en échec avant de changer de fournisseur. On ne bascule
    // qu'à condition qu'aucune tuile n'ait jamais abouti (sinon un simple trou
    // dans la couverture suffirait) ; 2 échecs car une petite carte ne demande
    // parfois que 2 à 4 tuiles.
    var TILE_ERROR_LIMIT = 2;

    var activeMap = null;
    var activeLayer = null;
    var index = 0;
    var errorCount = 0;
    var loadedCount = 0;

    function buildLayer(provider) {
        var options = {
            maxZoom: provider.options.maxZoom,
            attribution: provider.attribution,
            // Requêtes CORS : le service worker peut ainsi inspecter le statut
            // HTTP et refuser de mettre en cache une réponse 403.
            crossOrigin: true
        };
        // Ne transmettre que les options réellement définies : Leaflet plante
        // (e._getSubdomain) si on lui passe `subdomains: undefined`.
        if (provider.options.subdomains) {
            options.subdomains = provider.options.subdomains;
        }
        if (provider.options.maxNativeZoom) {
            options.maxNativeZoom = provider.options.maxNativeZoom;
        }
        return L.tileLayer(provider.url, options);
    }

    function attach() {
        var provider = PROVIDERS[index];
        errorCount = 0;
        loadedCount = 0;
        activeLayer = buildLayer(provider);

        activeLayer.on('tileload', function () {
            loadedCount += 1;
            errorCount = 0;
        });
        activeLayer.on('tileerror', function () {
            errorCount += 1;
            if (errorCount >= TILE_ERROR_LIMIT && loadedCount === 0) {
                useNext();
            }
        });

        activeLayer.addTo(activeMap);
        return activeLayer;
    }

    function useNext() {
        if (!activeMap || index >= PROVIDERS.length - 1) return false;
        if (activeLayer) activeMap.removeLayer(activeLayer);
        index += 1;
        attach();
        return true;
    }

    function addTo(map) {
        activeMap = map;
        index = 0;
        return attach();
    }

    window.ScolaloopTiles = {
        PROVIDERS: PROVIDERS,
        addTo: addTo,
        useNext: function () { return useNext(); },
        current: function () { return { index: index, name: PROVIDERS[index].name }; }
    };
})();
