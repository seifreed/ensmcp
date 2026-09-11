# Changelog

## Unreleased

## 0.1.2 - 2026-09-11

- Publicación oficial de metadatos en MCP Registry mediante GitHub OIDC, con validación previa, publisher fijado e idempotencia.
- Transporte MCP Streamable HTTP autenticado, limitado a loopback y protegido frente a cabeceras Host y Origin no autorizadas.
- Dependencias HTTP actualizadas a `httpcore2` y `httpx2` 2.12.0 para corregir vulnerabilidades conocidas.
- Separación de las capacidades MCP de registro, presentación y validación, con schemas públicos versionados y errores homogéneos.
- Data packs independientes y versionados para ISO 27001 y DORA, crosswalks parciales y retirada explícita de la fuente NIS2 anterior.
- Declaraciones de aplicabilidad persistentes con responsables, evidencias, excepciones, medidas compensatorias, vigilancia y fechas.
- Exportación de declaraciones en JSON, CSV, Markdown, XLSX, ODS y DOCX, incluyendo neutralización de fórmulas en hojas de cálculo.
- Perfiles completos de sistema y subsistemas heredables, con resolución y explicación de aplicabilidad.
- Validaciones más estrictas para perfiles contradictorios, rutas de packs, declaraciones, referencias de crosswalk, evidencias y cabeceras duplicadas.
- Arquitectura reforzada mediante límites explícitos entre dominio, aplicación, infraestructura y presentación.
- Releases condicionadas a CI verde y tags anotados con firma verificada por GitHub.
- SBOM CycloneDX, paquetes Python, hashes y data packs adjuntos automáticamente a cada release de GitHub.
- CI para Python 3.12, 3.13 y 3.14 en Linux, macOS y Windows, con cancelación de ejecuciones obsoletas y arranque robusto del navegador.
- Snapshot ENS offline por defecto, modos `--offline`, `--check-updates` y `--live`, y documentación de procedencia e independencia del proyecto.

## 0.1.1 - 2026-08-24

- Soporte probado para Python 3.12, 3.13 y 3.14.
- Resources MCP, schemas de salida, paginación y respuestas compactas.
- Diff estructurado del snapshot y SBOM CycloneDX en releases.
