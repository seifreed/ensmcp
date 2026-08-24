# Veredicto

**Me parece un proyecto muy bueno y bastante más trabajado de lo que sugiere una versión `0.1.0`.** No es simplemente un buscador que devuelve texto del ENS: has modelado dimensiones, niveles, categorías, refuerzos obligatorios y alternativos, cálculo de aplicabilidad, cuestionario de auditoría, requisitos esenciales, evidencias y nivel de madurez. Esa parte es realmente diferencial.

Mi valoración global sería:

| Área                                     |                    Madurez |
| ---------------------------------------- | -------------------------: |
| Arquitectura y calidad del código        |                 **8,5/10** |
| Pruebas y CI configurada                 |                   **9/10** |
| Empaquetado y cadena de suministro       |                   **8/10** |
| Experiencia de instalación y adopción    |                   **6/10** |
| Gobierno y reproducibilidad de los datos |                   **5/10** |
| Licencias de los contenidos              |      **Bloqueo pendiente** |
| **Madurez global**                       | **6,5/10 — beta avanzada** |

Para uso interno controlado lo veo perfectamente aprovechable. Para presentarlo como una fuente estable en la que terceros puedan apoyar decisiones de cumplimiento, todavía no lo llamaría maduro ni sacaría una `1.0`.

## Lo que está especialmente bien

La arquitectura está bien separada entre dominio, scraping, snapshot, guía y capa MCP. Los modelos son inmutables, la lógica importante está encapsulada en funciones puras y el código contiene bastantes defensas frente a datos incompletos, carreras del DOM, resultados vacíos, códigos incoherentes y errores de normalización. El árbol de pruebas cubre dominio, DdA, consultas, scraping, extracción de la guía, snapshot, empaquetado e integración MCP.

La CI está configurada con una exigencia muy alta: `mypy` estricto, Ruff, Black, Bandit, `pip-audit`, matriz multiplataforma y cobertura de ramas del 100 %. La publicación valida que el tag coincida con la versión, prueba el wheel en un entorno limpio y utiliza Trusted Publishing mediante OIDC.

La release `0.1.0` existe realmente en PyPI desde el 14 de agosto de 2026, con wheel y sdist. Ambos artefactos tienen attestations `in-toto`/Sigstore que los vinculan con el workflow y el commit del repositorio. Esto está por encima de la media de muchos proyectos open source más maduros. ([pypi.org][1])

También has resuelto correctamente varios detalles difíciles del ENS:

* Diferencias entre texto normativo y preguntas de auditoría.
* Refuerzos obligatorios frente a grupos alternativos.
* Requisitos acumulativos para el alcance de auditoría.
* Cálculo de categoría a partir de las dimensiones.
* Funcionamiento offline mediante snapshot.
* Detección de cambios en la fuente y estado de frescura.

Eso hace que el proyecto tenga utilidad real para consultores, auditores, responsables de seguridad y equipos que estén preparando una adecuación al ENS.

# El bloqueo más importante: licencia de los datos

Antes de seguir difundiendo la release, trataría esto como un **P0**.

El paquete incluye en `guia_808.json` preguntas y propuestas de evidencias extraídas de la CCN-STIC 808. El propio script explica que distribuye los datos extraídos, aunque no distribuya el PDF completo.

El PDF oficial de la edición de abril de 2026 indica copyright del CCN y prohíbe, sin autorización escrita, la reproducción parcial o total mediante cualquier procedimiento, incluyendo tratamiento informático. El aviso legal general del portal también restringe la reproducción, distribución, transformación y puesta a disposición de sus contenidos y bases de datos salvo autorización previa por escrito. ([CCN-CERT][2])

No afirmo que el proyecto constituya necesariamente una infracción; eso requiere un análisis jurídico específico. Pero **“no distribuyo el PDF, distribuyo su extracción” no elimina automáticamente el riesgo**, especialmente cuando el JSON conserva literalmente las preguntas y las evidencias.

Además, el repositorio y PyPI declaran MIT para el paquete completo, mientras que una licencia MIT tuya no puede conceder derechos sobre contenido de terceros que tú no controles.  ([pypi.org][1])

Mi actuación inmediata sería:

1. **Solicitar autorización escrita al CCN** para extraer, transformar y redistribuir el contenido de la CCN-STIC 808 y del ENS Navegable. Presentaría el proyecto como una integración abierta que puede mejorar la accesibilidad del ENS y ofrecería atribución, control de versiones y enlaces permanentes a las fuentes.

2. Mientras se resuelve, consideraría hacer un `yank` temporal de `0.1.0` o publicar rápidamente una `0.1.1` sin los textos extraídos de la guía. Un `yank` evita instalaciones normales sin borrar la trazabilidad de la versión.

3. Mantendría el parser, pero haría que el usuario proporcionase localmente el PDF legítimamente descargado:

   ```text
   ensmcp ingest-guide /ruta/CCN-STIC-808.pdf
   ```

   Así distribuyes el software de extracción, no el contenido extraído.

4. Separaría claramente:

   ```text
   LICENSE                 # código propio, MIT
   NOTICE.md               # atribuciones y no afiliación
   DATA_LICENSE.md         # condiciones de cada corpus
   LICENSES/MIT.txt
   ```

5. Usaría el BOE consolidado como fuente canónica del texto del Real Decreto y mantendría la información del CCN como un corpus distinto, con licencia y procedencia separadas.

# No deberías llamarlo todavía una “DdA completa”

La herramienta `declaracion_aplicabilidad` calcula correctamente la **aplicabilidad normativa base** a partir de los niveles introducidos, pero el README y PyPI la describen como “la DdA completa”.

Una Declaración de Aplicabilidad real no es solamente la lista calculada de medidas y refuerzos. Debe recoger, entre otros aspectos:

* Identificación, alcance y versión del sistema.
* Categorización formal y responsables.
* Aplicabilidad de cada medida.
* Exclusiones y su justificación.
* Medidas compensatorias.
* Medidas complementarias de vigilancia.
* Situación de implantación.
* Referencias a evidencias y documentos.
* Aprobación o suscripción del responsable de seguridad.

La propia CCN-STIC 808 exige justificar exclusiones y medidas compensatorias, y plantea la DdA como un documento formal. ([CCN-CERT][2])

Cambiaría la descripción a:

> “Calcula la matriz normativa base para preparar una Declaración de Aplicabilidad.”

Incluso valoraría renombrar la tool en una versión futura:

```text
calcular_aplicabilidad_base
generar_borrador_dda
validar_borrador_dda
```

Mantendría `declaracion_aplicabilidad` como alias deprecado durante varias versiones para no romper clientes.

Lo mismo sucede, en menor grado, con `alcance_auditoria`: actualmente genera un **checklist normativo de auditoría**, pero el alcance formal de una auditoría también incluye límites del sistema, ubicaciones, procesos, muestreo, interfaces, exclusiones, metodología y otros elementos. Podría llamarse `generar_checklist_auditoria` o dejar muy claro ese matiz.

## Corrige también la terminología oficial

Ahora reutilizas `ApplicabilityLevel.BASICO` para dos conceptos distintos y expones `basico`, `medio`, `alto` tanto para dimensiones como para la categoría.

El Real Decreto distingue expresamente:

* **Niveles de dimensión:** `BAJO`, `MEDIO`, `ALTO`.
* **Categorías del sistema:** `BÁSICA`, `MEDIA`, `ALTA`. ([BOE][3])

Separaría los tipos:

```python
class DimensionLevel(Enum):
    BAJO = "bajo"
    MEDIO = "medio"
    ALTO = "alto"

class SystemCategory(Enum):
    BASICA = "basica"
    MEDIA = "media"
    ALTA = "alta"
```

Durante una transición, aceptaría `basico` como alias de `bajo`, pero devolvería la terminología oficial. Esto parece menor, pero en una herramienta de compliance la precisión del vocabulario importa mucho.

# El comportamiento live debería cambiar

Actualmente el servidor responde desde el snapshot, pero al iniciarse puede abrir Chrome en segundo plano, acceder a la fuente oficial y, si detecta diferencias, sustituir automáticamente el corpus servido en memoria.

La implementación defensiva es buena, pero el comportamiento de producto no es ideal para una herramienta normativa:

* Dos ejecuciones de la misma versión pueden responder con corpus distintos.
* Un cambio temporal, error o compromiso de la web podría alterar las respuestas.
* El usuario puede no esperar que un MCP aparentemente offline abra un navegador y genere tráfico de red.
* La versión instalada deja de identificar exactamente el contenido utilizado.
* No existe revisión humana del diff antes de adoptar el nuevo corpus.

La especificación MCP subraya el consentimiento, el control del usuario y la claridad sobre el acceso a datos y las operaciones realizadas. ([Model Context Protocol][4])

Haría que el comportamiento predeterminado fuera:

```text
offline / snapshot fijo
```

Y ofrecería tres modos explícitos:

```text
--offline        Nunca usa red ni navegador
--check-updates  Comprueba, pero no cambia el corpus
--live           Comprueba y adopta temporalmente la fuente viva
```

La opción recomendable sería `--check-updates`. Cuando haya una diferencia, debería devolver un diff estructurado:

```json
{
  "status": "update_available",
  "snapshot_version": "2026-08-07",
  "added_measures": [],
  "removed_measures": [],
  "changed_measures": ["op.acc.5"],
  "changed_fields": {
    "op.acc.5": ["norm_text", "reinforcements"]
  }
}
```

Aún mejor: retiraría el scraping del runtime normal y lo convertiría en un pipeline de mantenimiento:

1. Job programado semanal.
2. Captura de las fuentes.
3. Validación.
4. Generación de diff.
5. Revisión humana.
6. Pull request automático.
7. Firma del manifiesto.
8. Nueva release de datos.

Así los clientes MCP consumen snapshots revisados y firmados; no necesitan Chrome, Patchright, display ni acceso al CCN.

# Mejoras concretas para la siguiente release

## 1. Distribución realmente sencilla

Ahora PyPI permite instalar el paquete, pero el README sigue llevando al usuario por un flujo de desarrollo con clone, entorno virtual, lock completo y editable install. ([pypi.org][1])

La instalación principal debería ser algo parecido a:

```bash
uv tool install --python 3.14 ensmcp
```

o:

```bash
pipx install ensmcp
```

Y después configurar simplemente:

```json
{
  "mcpServers": {
    "ensmcp": {
      "command": "ensmcp",
      "env": {
        "ENSMCP_MODE": "offline"
      }
    }
  }
}
```

El flujo de clone y `pylock.toml` debería quedar bajo “Desarrollo”.

También movería Patchright a un extra opcional:

```toml
dependencies = [
    "mcp>=2,<3",
]

[project.optional-dependencies]
live = [
    "patchright>=1.61,<2",
]
```

Entonces:

```bash
pip install ensmcp
pip install "ensmcp[live]"
```

El núcleo offline no debería depender de una librería de automatización de navegador.

## 2. Ampliar compatibilidad con Python

Exigir exclusivamente Python 3.14 reduce bastante la adopción. PyPI confirma actualmente `Python >=3.14`. ([pypi.org][1])

El código utiliza sintaxis moderna que requiere al menos Python 3.12, pero no veo a primera vista una razón conceptual para limitarlo solamente a 3.14. Probaría una matriz:

```text
Python 3.12
Python 3.13
Python 3.14
```

Y bajaría el mínimo únicamente después de verificar dependencias y suite completa.

## 3. Endurecer la release

La cadena de publicación está bien, pero hay una combinación mejorable:

* `main` no está protegida.
* El commit actual no está firmado.
* El workflow de release construye y prueba el wheel, pero no vuelve a ejecutar toda la suite completa antes de publicar.
* Un tag podría crearse sobre un commit que no haya pasado todos los gates.

Aplicaría:

* Protección de `main`.
* Pull request obligatorio.
* Required status checks.
* Prohibición de force-push.
* Tags firmados.
* Releases solamente desde commits con CI completa.
* `environment: pypi` con aprobación manual.
* `SECURITY.md`.
* `CHANGELOG.md`.
* Política de versiones y compatibilidad de schemas.
* Dependabot o Renovate.
* SBOM CycloneDX/SPDX adjunto a cada release.

Los tests contra la web real no deberían bloquear todos los pull requests. Los dejaría como canary programado o manual; los PR deberían depender de fixtures deterministas. Esto reduce falsos fallos por red/WAF y evita golpear el portal oficial en cada cambio.

# Mejoras específicas de MCP

El proyecto utiliza básicamente tools, pero una gran parte de su contenido es información de solo lectura. La especificación MCP distingue entre **resources**, **prompts** y **tools**; los resources están pensados precisamente para exponer datos identificados mediante URI. ([Model Context Protocol][4])

Añadiría resources como:

```text
ens://anexo-ii
ens://measures/org.1
ens://categories/op.acc
ens://guide/808/articles
ens://guide/808/evidence/op.acc.5
ens://data/status
```

Y reservaría las tools para operaciones:

```text
search_measures
calculate_applicability
generate_audit_checklist
compare_profiles
diff_snapshots
```

También incorporaría:

* `outputSchema` explícito para todas las tools.
* Anotaciones `readOnlyHint`, `idempotentHint` y `openWorldHint`.
* `refresh_live_page` marcado como operación con acceso externo.
* Orden determinista de tools y resultados.
* Schemas públicos versionados.
* Errores homogéneos: actualmente `get_measure` devuelve `null` para un código desconocido, mientras otras tools generan errores explicativos.

La especificación actual permite declarar schemas de salida y anotaciones de seguridad y comportamiento. ([Model Context Protocol][5])

## Evita respuestas gigantes

El propio README indica que un perfil puede generar 382 preguntas de auditoría. Devolverlas todas en una única llamada puede consumir una cantidad enorme de contexto. ([pypi.org][1])

Añadiría:

```text
limit
cursor
essential_only
measure_codes
compact
include_norm_text
include_questions
include_evidence
```

Por ejemplo:

```json
{
  "essential_only": true,
  "limit": 50,
  "cursor": "op.exp.4:12"
}
```

Y permitiría que la tool devolviera enlaces a resources en lugar de incrustar siempre todo el contenido.

## Corrige el badge “MCP 2.0”

La versión `2.0` parece referirse al SDK de Python, pero el protocolo MCP se versiona por fecha. La especificación vigente publicada está identificada como `2026-07-28`, no como “MCP 2.0”. ([Model Context Protocol][4])

Usaría uno de estos badges:

```text
MCP Server
MCP SDK 2.x
Tested against MCP 2026-07-28
```

No mezclaría versión de SDK y versión de protocolo.

Cuando lo anterior esté resuelto, publicaría también el servidor en el registro oficial de servidores MCP. ([Registro MCP][6])

# Funcionalidades que realmente lo harían diferencial

## Perfil de sistema y categorización justificable

En lugar de recibir únicamente cinco niveles finales, permitiría crear un perfil:

```json
{
  "system": "Portal de contratación",
  "scope": "...",
  "information_assets": [...],
  "services": [...],
  "dimensions": {
    "confidencialidad": {
      "level": "medio",
      "justification": "..."
    }
  }
}
```

El MCP podría calcular los máximos por información y servicio y conservar la justificación. El Real Decreto establece que el nivel de cada dimensión debe derivarse de la valoración del impacto y que la categoría se obtiene posteriormente de esos niveles. ([BOE][3])

## Explicabilidad de cada medida

Añadiría:

```text
explain_applicability(code, system_profile)
```

Con una respuesta como:

```json
{
  "measure_code": "op.cont.2",
  "applicable": true,
  "reason": {
    "basis": "dimension",
    "dimension": "disponibilidad",
    "system_level": "alto",
    "table_cell": "+ R1",
    "required_reinforcements": ["R1"]
  }
}
```

Esto sería tremendamente útil para consultores y auditores porque permite justificar por qué aparece cada fila de la DdA.

## Borrador completo de DdA

Crearía un modelo persistente:

```text
not_assessed
implemented
partially_implemented
not_implemented
excluded
compensated
```

Cada medida debería poder llevar:

```text
justification
implementation_status
owner
evidence_references
exclusion_reason
compensatory_measures
surveillance_measures
target_date
review_date
```

Después podrías exportar:

* JSON versionado.
* XLSX/ODS.
* Markdown.
* DOCX.
* CSV.
* Evidencias pendientes.
* Plan de adecuación.

## Perfiles de Cumplimiento Específico y subsistemas

El Real Decreto permite perfiles de cumplimiento específicos y sistemas con subsistemas segregados que requieran niveles diferentes. Tu modelo actual cubre un único perfil dimensional. ([BOE][3])

Añadiría:

```text
profile_id
subsystems[]
inheritance
profile_overrides
additional_measures
excluded_measures
```

## Data packs separados

No convertiría el core en un monolito con todas las guías. Diseñaría paquetes independientes:

```text
ensmcp-core
ensmcp-data-rd311
ensmcp-data-ccn808
ensmcp-data-pce
ensmcp-crosswalk-iso27001
ensmcp-crosswalk-nis2
ensmcp-crosswalk-dora
```

Siempre sujetos a licencia, procedencia y revisión experta.

# Roadmap recomendado

| Versión   | Objetivo                                                                                                                                                                          |
| --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0.1.1** | Resolver licencias, añadir aviso de no afiliación, corregir las afirmaciones sobre “DdA completa”, poner modo offline por defecto, mejorar instalación y proteger `main`.         |
| **0.2.0** | Separar Patchright como extra, soportar Python 3.12–3.14, resources MCP, output schemas, paginación, respuestas compactas y diff estructurado.                                    |
| **0.3.0** | Perfil de sistema, categorización justificada, explicación de aplicabilidad, borrador real de DdA, estados y referencias de evidencias.                                           |
| **0.4.0** | Perfiles de cumplimiento específicos, subsistemas, exportaciones y API/Streamable HTTP opcional.                                                                                  |
| **1.0.0** | Autorización escrita sobre los datos, corpus firmado y reproducible, schemas estables, revisión por auditores ENS independientes, matriz de clientes MCP y varios pilotos reales. |

Para un transporte remoto utilizaría Streamable HTTP, pero solamente con autenticación, validación de `Origin` y bind seguro; la especificación actual exige estas precauciones. ([Model Context Protocol][7])

# Aviso que añadiría al README

> **ensmcp es un proyecto independiente y no está afiliado, respaldado ni mantenido por el CCN, CCN-CERT o el CNI. Sus resultados son una ayuda técnica para consultar y preparar documentación. Para decisiones de conformidad prevalecen el BOE, las Instrucciones Técnicas de Seguridad, las guías oficiales vigentes y el criterio de la entidad auditora o de certificación correspondiente.**

## Conclusión

**Has construido una beta técnicamente avanzada y con una base mucho mejor que la mayoría de primeras releases.** El motor de dominio, las pruebas, el snapshot offline y la cadena de publicación son puntos fuertes reales.

Lo que te impide llamarlo maduro no es principalmente el código. Es:

1. La autorización para redistribuir los contenidos.
2. La trazabilidad y reproducibilidad normativa.
3. La afirmación excesiva de generar una DdA completa.
4. El cambio automático de corpus en runtime.
5. La falta de gobierno de release y experiencia externa.

Mi prioridad absoluta sería sacar una **`0.1.1` centrada en confianza, licencias y precisión**, antes de añadir nuevas guías. Una vez resuelto eso, el proyecto sí tiene potencial para convertirse en el MCP open source de referencia para trabajar con el ENS.

*La valoración se basa en la revisión estática del código actual, los workflows y los artefactos publicados; no he considerado la suite como verificada independientemente mediante una ejecución local completa.*

[1]: https://pypi.org/project/ensmcp/ "ensmcp · PyPI"
[2]: https://www.ccn-cert.cni.es/es/800-guia-esquema-nacional-de-seguridad/518-ccn-stic-808-verificacion-del-cumplimiento-de-las-medidas-en-el-ens/file.html "https://www.ccn-cert.cni.es/es/800-guia-esquema-nacional-de-seguridad/518-ccn-stic-808-verificacion-del-cumplimiento-de-las-medidas-en-el-ens/file.html"
[3]: https://www.boe.es/buscar/act.php?id=BOE-A-2022-7191 "BOE-A-2022-7191 Real Decreto 311/2022, de 3 de mayo, por el que se regula el Esquema Nacional de Seguridad."
[4]: https://modelcontextprotocol.io/specification "https://modelcontextprotocol.io/specification"
[5]: https://modelcontextprotocol.io/specification/2026-07-28/schema "https://modelcontextprotocol.io/specification/2026-07-28/schema"
[6]: https://registry.modelcontextprotocol.io/ "https://registry.modelcontextprotocol.io/"
[7]: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http "https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http"
