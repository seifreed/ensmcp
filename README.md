<p align="center">
  <img src="https://img.shields.io/badge/ensmcp-MCP%20Server%20ENS%20Navegable-blue?style=for-the-badge" alt="ensmcp">
</p>

<h1 align="center">ensmcp</h1>

<!-- mcp-name: io.github.seifreed/ensmcp -->

<p align="center">
  <strong>Servidor MCP con las medidas de seguridad del ENS (Anexo II del RD 311/2022): consulta el Anexo II, calcula la matriz normativa de aplicabilidad y genera un checklist de auditoría, sin conexión</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12--3.14-blue?style=flat-square&logo=python&logoColor=white" alt="Python Version"></a>
  <a href="https://github.com/seifreed/ensmcp/actions"><img src="https://img.shields.io/github/actions/workflow/status/seifreed/ensmcp/ci.yml?style=flat-square&logo=github&label=CI" alt="CI Status"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/MCP-Server-black?style=flat-square" alt="MCP Server"></a>
</p>

<p align="center">
  <a href="https://github.com/seifreed/ensmcp/stargazers"><img src="https://img.shields.io/github/stars/seifreed/ensmcp?style=flat-square" alt="GitHub Stars"></a>
  <a href="https://github.com/seifreed/ensmcp/issues"><img src="https://img.shields.io/github/issues/seifreed/ensmcp?style=flat-square" alt="GitHub Issues"></a>
  <a href="https://buymeacoffee.com/seifreed"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-yellow?style=flat-square&logo=buy-me-a-coffee&logoColor=white" alt="Buy Me a Coffee"></a>
</p>

---

## Qué es

**ensmcp** es un servidor [MCP](https://modelcontextprotocol.io/) (Model Context Protocol) que pone las medidas de seguridad del [ENS Navegable](https://gobernanza.ccn-cert.cni.es/ens-navegable) (Anexo II del RD 311/2022) al alcance de Claude Desktop, Claude Code y cualquier otro cliente MCP. Es un servidor independiente y no está afiliado, respaldado ni mantenido por el CCN, CCN-CERT o el CNI.

Los datos viajan incluidos en el paquete como un snapshot, así que el servidor responde al instante y funciona sin conexión. El modo predeterminado es `offline`: no abre Chrome ni accede a la red. `--check-updates` comprueba la fuente oficial sin sustituir el snapshot y `--live` permite adoptar temporalmente los datos vivos.

La procedencia y el alcance declarado de la autorización para publicar los datos se documentan en [`DATA_LICENSE.md`](DATA_LICENSE.md) y [`CCN_AUTHORIZATION.md`](CCN_AUTHORIZATION.md). La autorización del registro MCP consta en [`MCP_REGISTRY.md`](MCP_REGISTRY.md) y el procedimiento de publicación y firma está en [`RELEASING.md`](RELEASING.md).

Además del Anexo II, incorpora el cuestionario de verificación de la **guía CCN-STIC 808** (edición para el RD 311/2022): las preguntas de auditoría por medida, las comprobaciones sobre el articulado del RD y las evidencias documentales que puede pedir el auditor.

### Características principales

| Característica | Descripción |
|----------------|-------------|
| **Transportes MCP** | `stdio` por defecto y Streamable HTTP autenticado para despliegues controlados |
| **Funciona sin conexión** | El corpus completo viaja como snapshot en el paquete |
| **Snapshot determinista** | El modo predeterminado sirve siempre el corpus empaquetado |
| **Comprobación explícita** | `--check-updates` detecta cambios sin sustituir los datos servidos |
| **Matriz de aplicabilidad** | Calcula la base normativa para preparar la Declaración de Aplicabilidad |
| **Auditoría CCN-STIC 808** | Temario de auditoría, requisitos esenciales, artículos del RD y evidencias documentales |
| **Crosswalks externos** | Carga data packs versionados sin acoplar otros marcos al core |

## Tools disponibles

### Consulta del Anexo II

| Tool | Args | Descripción |
|------|------|-------------|
| `list_categories` | — | Las categorías del Anexo II con su grupo (`org`, `op`, `mp`). |
| `list_measures` | `category_code?`, `dimension?`, `level?` | Medidas filtradas por categoría, dimensión de seguridad o nivel. |
| `get_measure` | `code` | Una medida por código exacto (p. ej. `"org.1"`), con su texto del RD, refuerzos y niveles. |
| `search_measures` | `query` | Búsqueda por texto en código, título, descripción y redacción del RD (ignora mayúsculas y tildes). |

### Declaración de Aplicabilidad y auditoría

| Tool | Args | Descripción |
|------|------|-------------|
| `declaracion_aplicabilidad` | `confidencialidad?`, `integridad?`, `disponibilidad?`, `autenticidad?`, `trazabilidad?` | La matriz normativa base para preparar la DdA: se valora cada dimensión (`bajo`/`medio`/`alto`, u omitida) y devuelve las medidas exigibles con sus refuerzos. |
| `evaluate_system_profile` | `profile` | Calcula máximos por activos y servicios, aplica perfiles heredados y evalúa subsistemas. |
| `explain_applicability` | `code`, `profile`, `subsystem_id?` | Explica la dimensión, nivel, celda, justificación o regla de perfil que decide una medida. |
| `create_dda` | `record_id`, `profile`, `subsystem_id?` | Crea una DdA persistente con las 73 medidas y su decisión de aplicabilidad. |
| `list_dda` | — | Lista las DdA guardadas y resume sus estados de implantación. |
| `get_dda` | `record_id` | Recupera una DdA completa. |
| `update_dda_measure_status` | `record_id`, `code`, `implementation_status`, ... | Registra estado, responsable, evidencias, exclusión o compensación, vigilancia y fechas. |
| `export_dda` | `record_id`, `output_format` | Exporta como `json`, `csv`, `markdown`, `xlsx`, `ods` o `docx`; el contenido se devuelve en base64. |
| `alcance_auditoria` | mismas que la DdA | El temario de auditoría del sistema: las medidas aplicables con sus preguntas de verificación acumuladas y el nivel de madurez mínimo exigible. |
| `requisitos_auditoria` | `code?`, `level?` | El cuestionario CCN-STIC 808 en bruto, por medida o por tramo, marcando los requisitos esenciales. |
| `requisitos_articulos` | — | Las comprobaciones de auditoría sobre el articulado del RD (DdA formal, categorización, INES...). |
| `evidencias_auditoria` | `code?` | La documentación que puede pedir el auditor, por medida. |

### Crosswalks

| Tool | Args | Descripción |
|------|------|-------------|
| `list_data_packs` | `include_inactive?` | Lista packs configurados con fuente, versión, cobertura y vigencia. |
| `query_crosswalk` | `pack_id`, `ens_code?`, `external_reference?` | Consulta correspondencias en ambas direcciones, con paginación opcional. |

### Estado y actualización

| Tool | Args | Descripción |
|------|------|-------------|
| `refresh_live_page` | — | Comprueba ahora el sitio oficial y actualiza los datos si han cambiado. |
| `snapshot_status` | — | Origen y frescura de los datos que se están sirviendo. |

Los contratos públicos de entrada y salida están versionados en
`ens://schemas/v1/tools`; cada tool también expone su schema individual en
`ens://schemas/v1/tools/{name}`.

## Qué devuelve una medida

Cada medida trae dos textos, y hacen falta los dos: `norm_text` es la redacción del RD 311/2022 (lo que la medida **exige**) y `description` es el cuestionario de la CCN-STIC 808 (lo que el auditor **pregunta**). Los refuerzos vienen emparejados con el nivel que los exige y con su redacción en el RD:

```json
{
  "code": "mp.s.4",
  "title": "Protección frente a denegación de servicio",
  "description": "Categoría Media 1.1 ¿Se ha planificado y dotado al sistema de capacidad suficiente ...?",
  "norm_text": "Se establecerán medidas preventivas frente a ataques de denegación de servicio ...",
  "category_code": "mp.s",
  "dimensions": ["disponibilidad"],
  "levels": ["medio", "alto"],
  "reinforcements": [
    { "code": "R1", "level": "alto", "alternative": false, "text": "R1-Detección y reacción. ..." }
  ],
  "raw_levels": { "bajo": "n.a.", "medio": "aplica", "alto": "+ R1" }
}
```

`alternative` distingue los refuerzos obligatorios (`+ R1 + R2`) de los alternativos (`+ [R1 o R2]`, donde basta uno cualquiera): confundirlos cambia lo que hay que implantar.

## Declaración de Aplicabilidad

El ENS no aplica un nivel al sistema entero: aplica **uno por dimensión**. Se valora cada una y la tool devuelve lo que ese sistema debe cumplir:

```json
{
  "categoria_sistema": "alta",
  "measures": [
    { "code": "op.acc.5", "title": "Mecanismo de autenticación (usuarios externos)",
      "required_level": "alto",
      "required_reinforcements": [
        { "code": "R2", "alternative": true,  "text": "R2-..." },
        { "code": "R5", "alternative": false, "text": "R5-..." }
      ] }
  ]
}
```

La regla sale del RD 311/2022: la categoría del sistema es el mayor de los niveles valorados (Anexo I, ap. 4), las medidas marcadas «Categoría» se exigen según la categoría del sistema, las que protegen dimensiones según el nivel de esas dimensiones, y una dimensión sin valorar deja fuera las medidas que solo la protegen.

Las DdA persistentes se guardan como JSON versionado mediante escritura atómica. Por defecto viven en `~/.ensmcp/dda`; `ENSMCP_DATA_DIR=/ruta` cambia la raíz a `/ruta/dda`. Los estados admitidos son `not_assessed`, `implemented`, `partially_implemented`, `not_implemented`, `excluded` y `compensated`. Una exclusión exige motivo y una compensación exige al menos una medida compensatoria.

## Alcance de auditoría

Si la DdA contesta *«¿qué tengo que implantar?»*, `alcance_auditoria` contesta *«¿qué me va a preguntar el auditor?»*. Devuelve, por cada medida aplicable, los requisitos de verificación **acumulados** hasta su nivel exigible y el nivel de madurez mínimo que exige la guía:

| Categoría | Nivel mínimo de madurez |
|---|---|
| BÁSICA | **L2** — Reproducible, pero intuitivo |
| MEDIA | **L3** — Proceso definido |
| ALTA | **L4** — Gestionado y medible |

El matiz importa: los tramos que el ENS Navegable etiqueta «Categoría Básica / Media / Alta» son acumulativos según la CCN-STIC 808 §5 — «Categoría Básica» significa *exigible a todas las categorías*, no *solo para sistemas básicos*. Un sistema de categoría media responde las preguntas de básica **y** las de media. Para un sistema C=alto, I=medio, D=bajo, A=medio, T=medio, el temario real son **382 preguntas** (136 esenciales, cuyo incumplimiento bloquea la certificación), no las 73 de su tramo.

`requisitos_articulos` y `evidencias_auditoria` cubren la otra mitad de la auditoría: las comprobaciones sobre el articulado del RD (si la DdA existe y está suscrita, si el sistema está categorizado formalmente, si se reporta a INES...) y las 365 evidencias documentales que la guía propone. Estos datos salen de la CCN-STIC 808; el ENS Navegable no los publica.

## De dónde salen los datos

- **`src/ensmcp/data/anexo_ii.json`** — el corpus del ENS Navegable (medidas, textos del RD, cuestionario, aplicabilidad por niveles), capturado del sitio oficial. Cada consulta es un lookup en memoria.
- **`src/ensmcp/data/guia_808.json`** — el dato extraído de la guía CCN-STIC 808 con su atribución (la guía en sí no se redistribuye). `snapshot_status` indica de qué edición procede.

Para regenerar el snapshot:

```bash
python scripts/build_snapshot.py
```

El servidor no abre Chrome ni usa la red por defecto. Para consultar cambios explícitamente:

```bash
ensmcp --offline
ensmcp --check-updates
ensmcp --live
```

También puede configurarse con `ENSMCP_MODE=offline|check-updates|live`.

### Streamable HTTP autenticado

El transporte HTTP escucha únicamente en loopback y exige un Bearer token de al menos 32 caracteres:

```bash
export ENSMCP_HTTP_TOKEN="$(openssl rand -hex 32)"
ensmcp --transport http
```

El endpoint MCP queda en `http://127.0.0.1:8000/mcp`. Los clientes deben enviar
`Authorization: Bearer $ENSMCP_HTTP_TOKEN`. Un cliente web también necesita que
su Origin se autorice de forma exacta:

```bash
ensmcp --transport http --allow-origin https://cliente.example
```

`ENSMCP_TRANSPORT=http` selecciona el mismo transporte. `--host` sólo acepta
`127.0.0.1`, `localhost`, `::1` u otra dirección loopback; `--port` cambia el
puerto. Si un proxy inverso conserva un Host distinto, se autoriza con
`--allow-host mcp.example`.

Para acceso desde otra máquina, mantén `ensmcp` en loopback y publícalo tras un
proxy inverso con TLS. El token nunca debe ir en la URL. Este modo implementa
autenticación por secreto compartido, no el flujo OAuth 2.1 para servicios MCP
públicos o multiusuario; esos despliegues necesitan un gateway OAuth compatible.

### Data packs independientes

Los crosswalks no forman parte del paquete Python ni se activan implícitamente. `ENSMCP_DATA_PACKS` acepta un fichero, un directorio con ficheros JSON o varias rutas separadas por el separador del sistema (`:` en Unix, `;` en Windows):

```bash
ENSMCP_DATA_PACKS=./packs ensmcp --offline
```

El repositorio incluye packs parciales para ISO/IEC 27001:2022 y DORA. Las relaciones DORA son editoriales y no equivalencias jurídicas. El pack NIS2 sólo registra el estado de la fuente: está inactivo y vacío porque el CCN retiró la CCN-STIC 892 anterior con efecto inmediato; puede inspeccionarse con `include_inactive=true` y no debe utilizarse como perfil vigente.

## Requisitos

- Python **3.12-3.14**

Solo para **actualizar** el snapshot (`refresh_live_page`, la comprobación de arranque, `scripts/build_snapshot.py`) hacen falta además:

- Google Chrome instalado
- Un display (o `xvfb` en servidores sin él)

## Instalación

Para usar el servidor desde PyPI:

```bash
pip install ensmcp
ensmcp --offline
```

Para habilitar la comprobación live:

```bash
pip install "ensmcp[live]"
ensmcp --check-updates
```

### Desarrollo

```bash
git clone https://github.com/seifreed/ensmcp.git
cd ensmcp
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r pylock.toml   # versiones exactas, verificadas por hash
pip install -e . --no-deps   # el propio paquete, sin re-resolver
patchright install chromium
```

En Linux recién instalado puede hacer falta además:

```bash
patchright install-deps chromium
```

`pyproject.toml` es el único sitio donde se declaran las dependencias; `pylock.toml` es un artefacto generado a partir de él ([PEP 751](https://peps.python.org/pep-0751/)) que fija todas las dependencias —runtime y desarrollo juntas— a versión exacta y hash, con marcadores para Windows, Linux y macOS en x64 y ARM. Para actualizar dependencias, edita los rangos en `pyproject.toml` y regenera:

```bash
uv pip compile pyproject.toml --all-extras --universal --python-version 3.12 \
  --format pylock.toml -o pylock.toml
```

## Inicio rápido

```bash
python -m ensmcp
```

Configúralo en un cliente MCP (p. ej. Claude Desktop / Claude Code) apuntando al intérprete del entorno virtual:

```json
{
  "mcpServers": {
    "ensmcp": { "command": "ensmcp", "args": ["--offline"] }
  }
}
```

No hace falta configurar nada más: las consultas se responden desde el snapshot del paquete.

Para decisiones de conformidad prevalecen el BOE, las Instrucciones Técnicas de Seguridad, las guías oficiales vigentes y el criterio de la entidad auditora o de certificación correspondiente.

Para inspeccionarlo manualmente:

```bash
npx @modelcontextprotocol/inspector venv/bin/python -m ensmcp
```

## Arquitectura

La regla de dependencias, las capas y sus límites están documentados en
[`ARCHITECTURE.md`](ARCHITECTURE.md) y protegidos por tests estructurales.

## Contribuir

Las contribuciones son bienvenidas.

1. Haz un fork del repositorio
2. Crea tu rama de funcionalidad (`git checkout -b feature/nueva-funcionalidad`)
3. Haz commit de tus cambios (`git commit -m 'Añade nueva funcionalidad'`)
4. Sube la rama (`git push origin feature/nueva-funcionalidad`)
5. Abre un Pull Request

Asegúrate de que todas las gates de calidad y seguridad pasen sin errores ni warnings antes de enviar el PR.

## Apoya el proyecto

Si este proyecto te es útil, puedes apoyar su desarrollo:

<a href="https://buymeacoffee.com/seifreed" target="_blank">
  <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50">
</a>

## Autor

- **Marc Rivero López** | [@seifreed](https://github.com/seifreed)
- Repositorio: [github.com/seifreed/ensmcp](https://github.com/seifreed/ensmcp)

---

<p align="center">
  <sub>Las medidas del ENS, accesibles por MCP</sub>
</p>
