<p align="center">
  <img src="https://img.shields.io/badge/ensmcp-MCP%20Server%20ENS%20Navegable-blue?style=for-the-badge" alt="ensmcp">
</p>

<h1 align="center">ensmcp</h1>

<p align="center">
  <strong>Servidor MCP con las medidas de seguridad del ENS (Anexo II del RD 311/2022): consulta el Anexo II, calcula la matriz normativa de aplicabilidad y genera un checklist de auditoría, sin conexión</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.14-blue?style=flat-square&logo=python&logoColor=white" alt="Python Version"></a>
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

Además del Anexo II, incorpora el cuestionario de verificación de la **guía CCN-STIC 808** (edición para el RD 311/2022): las preguntas de auditoría por medida, las comprobaciones sobre el articulado del RD y las evidencias documentales que puede pedir el auditor.

### Características principales

| Característica | Descripción |
|----------------|-------------|
| **Servidor MCP sobre stdio** | Integrable en Claude Desktop, Claude Code y otros clientes MCP |
| **Funciona sin conexión** | El corpus completo viaja como snapshot en el paquete |
| **Snapshot determinista** | El modo predeterminado sirve siempre el corpus empaquetado |
| **Comprobación explícita** | `--check-updates` detecta cambios sin sustituir los datos servidos |
| **Matriz de aplicabilidad** | Calcula la base normativa para preparar la Declaración de Aplicabilidad |
| **Auditoría CCN-STIC 808** | Temario de auditoría, requisitos esenciales, artículos del RD y evidencias documentales |

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
| `alcance_auditoria` | mismas que la DdA | El temario de auditoría del sistema: las medidas aplicables con sus preguntas de verificación acumuladas y el nivel de madurez mínimo exigible. |
| `requisitos_auditoria` | `code?`, `level?` | El cuestionario CCN-STIC 808 en bruto, por medida o por tramo, marcando los requisitos esenciales. |
| `requisitos_articulos` | — | Las comprobaciones de auditoría sobre el articulado del RD (DdA formal, categorización, INES...). |
| `evidencias_auditoria` | `code?` | La documentación que puede pedir el auditor, por medida. |

### Estado y actualización

| Tool | Args | Descripción |
|------|------|-------------|
| `refresh_live_page` | — | Comprueba ahora el sitio oficial y actualiza los datos si han cambiado. |
| `snapshot_status` | — | Origen y frescura de los datos que se están sirviendo. |

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
  "raw_levels": { "basico": "n.a.", "medio": "aplica", "alto": "+ R1" }
}
```

`alternative` distingue los refuerzos obligatorios (`+ R1 + R2`) de los alternativos (`+ [R1 o R2]`, donde basta uno cualquiera): confundirlos cambia lo que hay que implantar.

## Declaración de Aplicabilidad

El ENS no aplica un nivel al sistema entero: aplica **uno por dimensión**. Se valora cada una y la tool devuelve lo que ese sistema debe cumplir:

```json
{
  "categoria_sistema": "alto",
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

## Requisitos

- Python **3.14+**

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
uv pip compile pyproject.toml --all-extras --universal --python-version 3.14 \
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
