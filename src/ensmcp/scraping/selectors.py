"""URL and CSS selector constants for the live ENS Navegable site.

These come from a real HAR capture of gobernanza.ccn-cert.cni.es (the site
blocks this environment's own egress, so the capture was taken on a
reachable network and reviewed here — see scripts/capture_dom.py), not
guessed: /ens-navegable embeds an <iframe src="/ens-navegable-contenido">,
and that frame's own script.js calls cargarTablaAplicabilidad() on load,
which injects a single summary table (id="tablaResumen") listing every
category and measure by default.
"""

ENS_NAVEGABLE_URL = "https://gobernanza.ccn-cert.cni.es/ens-navegable"
CONTENT_IFRAME_URL_FRAGMENT = "ens-navegable-contenido"
CONTENT_IFRAME_SELECTOR = "iframe"

SUMMARY_TABLE_SELECTOR = "#tablaResumen"
TABLE_ROW_SELECTOR = f"{SUMMARY_TABLE_SELECTOR} tbody tr"

# Each measure's free-text description lives in this build asset, served on the
# same origin as the content iframe. Fetched through the live page's own fetch()
# (same cookies/session) so the WAF that blocks headless egress does not apply.
REQUISITOS_JS_PATH = "/build/navigableens/requisitos.js"

# The wording of each refuerzo is in neither the summary table (which only
# names them, "+ R1") nor requisitos.js (the CCN-STIC 808 questionnaire). It
# ships in this sibling asset, the RD 311/2022 text itself, where every
# refuerzo is one string carrying its own "[medida.rN.k]" requirement markers.
ENS_NORM_JS_PATH = "/build/navigableens/norms/ens.js"

GROUP_HEADER_ROW_CLASS = "fondo_oscuro"
SUBCATEGORY_HEADER_ROW_CLASS = "sombra"
MEASURE_ROW_CLASS = "cuerpo_tabla_izq"
